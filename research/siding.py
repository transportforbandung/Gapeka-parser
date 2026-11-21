"""Siding-track model: 30 km/h zone of Z metres at the departure end / arrival end of a leg when the train stops at
that station on a siding. Policies: S0 none, S1 every stop, S2 only stops with a crossing/overtake note,
S3 S2 + per (station, group) learned siding use."""
import sqlite3, sys
from collections import Counter, defaultdict
import numpy as np
from scipy.sparse import csr_matrix
from scipy.sparse.linalg import lsqr
import physics_model as pm

KRL = {"Commuter Line Rangkasbitung", "Commuter Line Cikarang", "Commuter Line Bogor", "Commuter Line Yogyakarta",
       "Commuter Line Tangerang", "Commuter Line Tanjungpriuk", "Commuter Line BST"}
PHYS = {"krl": (0.45, 4.2, 0.85), "loco/krd": (0.15, 2.3, 0.15), "freight": (0.08, 1.2, 0.08)}
SIDING_V = 30.0
def group(g): return "krl" if g["service"] in KRL else ("freight" if g["cls"] == "freight" else "loco/krd")

db = sqlite3.connect("/home/user/Gapeka-parser/gapeka.db")
legs, total = pm.build(db, "track")
for g in legs: g["grp"] = group(g)
train_i = [i for i, g in enumerate(legs) if not g["test"] and not g["noted"]]
test_i = [i for i, g in enumerate(legs) if g["test"]]
y = np.array([g["T"] for g in legs], float)
nh = np.array([len(g["halts"]) for g in legs], float)
grp = np.array([g["grp"] for g in legs]); svc = np.array([g["service"] for g in legs])

def sims(Z):
    """sim seconds for 4 variants: (siding at start, siding at end) in {0,1}^2 -- only where the train stops"""
    nz = int(round(Z / pm.DX))
    out = {}
    for sa in (0, 1):
        for sb in (0, 1):
            L = []
            for g in legs:
                h = dict(g); cap = g["cap"].copy()
                if sa and g["start_rest"]: cap[:nz] = np.minimum(cap[:nz], SIDING_V)
                if sb and g["end_rest"]: cap[-nz:] = np.minimum(cap[-nz:], SIDING_V)
                h["cap"] = cap; L.append(h)
            s = np.zeros(len(L)); by = defaultdict(list)
            for i, g in enumerate(L): by[g["grp"]].append(i)
            for gr, idx in by.items():
                sub = [L[i] for i in idx]
                s[idx] = pm.simulate(pm.prepare(sub), len(sub), *PHYS[gr])
            out[(sa, sb)] = s
    return out

def fit_lin(s, mask):
    X = np.column_stack([s[mask], np.ones(mask.sum()), nh[mask]]); yy = y[mask]
    c = np.linalg.lstsq(X, yy, rcond=None)[0]
    for _ in range(40):
        w = 1 / np.maximum(np.abs(yy - X @ c), 5.0)
        c = np.linalg.lstsq(X * w[:, None] ** .5, yy * w ** .5, rcond=None)[0]
    return c

def full_eval(s, label):
    tr = np.zeros(len(legs), bool); tr[train_i] = True
    coef = {gr: fit_lin(s, tr & (grp == gr)) for gr in PHYS}
    for sv, n in Counter(svc[tr]).items():
        if n >= 40: coef[sv] = fit_lin(s, tr & (svc == sv))
    p = np.array([np.dot(coef.get(sv, coef[gr]), [x, 1, h]) for x, h, gr, sv in zip(s, nh, grp, svc)])
    # section correction on training residuals
    idx, rows, cols, vals = {}, [], [], []
    feats = []
    for g in legs:
        out, pos = defaultdict(float), 0
        for a, b in zip(g["stations"], g["stations"][1:]):
            nc = max(1, int(round(pm.profile(a, b)[0] * 1000 / pm.DX)))
            out[(g["grp"], a, b)] += float(np.sum(pm.DX / (g["cap"][pos:pos + nc] / 3.6))); pos += nc
        feats.append(out)
    for r, i in enumerate(train_i):
        for k, v in feats[i].items(): rows.append(r); cols.append(idx.setdefault(k, len(idx))); vals.append(v)
    A = csr_matrix((vals, (rows, cols)), shape=(len(train_i), len(idx)))
    res = y[train_i] - p[train_i]; keep = np.ones(len(res), bool)
    for _ in range(3):
        f = lsqr(A[keep], res[keep], damp=3.0)[0]; keep = np.abs(res - A @ f) < 90
    pc = p + np.array([sum(v * f[idx[k]] for k, v in F.items() if k in idx) for F in feats])
    e = (pc - y)[test_i] / 60
    noted = np.array([legs[i]["noted"] for i in test_i])
    def b(a):
        a = np.abs(a); return f"<0.5 {np.mean(a < .5):4.0%}  0.5-1 {np.mean((a >= .5) & (a <= 1)):4.0%}  >1 {np.mean(a > 1):4.0%}"
    print(f"{label:44s} all: {b(e)}   |  no note: {b(e[~noted])}   |  with note: {b(e[noted])}", flush=True)
    return pc

def combine(S, use_a, use_b):
    return np.choose(use_a * 2 + use_b, [S[(0, 0)], S[(0, 1)], S[(1, 0)], S[(1, 1)]])

note_a = np.array([g["note_a"] for g in legs]); note_b = np.array([g["note_b"] for g in legs])
start_rest = np.array([g["start_rest"] for g in legs]); end_rest = np.array([g["end_rest"] for g in legs])
st_a = np.array([g["stations"][0] for g in legs]); st_b = np.array([g["stations"][-1] for g in legs])
best = None
for Z in [int(z) for z in sys.argv[1:]] or (300, 500, 800):
    S = sims(Z)
    zero = np.zeros(len(legs), int)
    if best is None:
        full_eval(S[(0, 0)], "S0 no siding")
    full_eval(combine(S, start_rest.astype(int), end_rest.astype(int)), f"S1 every stop on siding, Z={Z} m")
    ua, ub = (note_a & start_rest).astype(int), (note_b & end_rest).astype(int)
    full_eval(combine(S, ua, ub), f"S2 noted stops on siding, Z={Z} m")
    # S3: learn per (station, group, end) whether stopping trains use a siding (training legs, quick group-level coefs)
    tr = np.zeros(len(legs), bool); tr[train_i] = True
    for it in range(2):
        s_cur = combine(S, ua, ub)
        coef = {gr: fit_lin(s_cur, tr & (grp == gr)) for gr in PHYS}
        cg = np.array([coef[gr] for gr in grp])
        pred = lambda s: cg[:, 0] * s + cg[:, 1] + cg[:, 2] * nh
        changed = 0
        for end, use, stv, rest, note in (("a", ua, st_a, start_rest, note_a), ("b", ub, st_b, end_rest, note_b)):
            keys = defaultdict(list)
            for i in np.where(tr & rest & ~note)[0]: keys[(stv[i], grp[i])].append(i)
            for k, ii in keys.items():
                if len(ii) < 5: continue
                ii = np.array(ii)
                on, off = use.copy(), use.copy(); on[ii] = 1; off[ii] = 0
                e_on = np.abs(pred(combine(S, on if end == "a" else ua, on if end == "b" else ub))[ii] - y[ii]).sum()
                e_off = np.abs(pred(combine(S, off if end == "a" else ua, off if end == "b" else ub))[ii] - y[ii]).sum()
                # apply to all legs (incl. test) that stop at that station with that group, without a note
                allii = np.where(rest & ~note & (stv == k[0]) & (grp == k[1]))[0]
                new = 1 if e_on < e_off * 0.97 else 0
                if (use[allii] != new).any(): changed += 1
                use[allii] = new
        print(f"   S3 iteration {it + 1}: {changed} station decisions changed; stations with siding use: "
              f"departures {len(set(zip(st_a[(ua == 1) & ~note_a], grp[(ua == 1) & ~note_a])))}, arrivals {len(set(zip(st_b[(ub == 1) & ~note_b], grp[(ub == 1) & ~note_b])))}", flush=True)
    full_eval(combine(S, ua, ub), f"S3 noted + learned per station, Z={Z} m")
    best = True
