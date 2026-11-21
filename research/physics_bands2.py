"""Bands after: restriction pairing fix, fixed rolling-stock physics per group (step 3), per-service margin/const/dwell,
section correction, note handling (step 2) and whole-minute rounding (step 4)."""
import sqlite3
from collections import Counter, defaultdict
import numpy as np
from scipy.optimize import minimize
from scipy.sparse import csr_matrix
from scipy.sparse.linalg import lsqr
import physics_model as pm

KRL = {"Commuter Line Rangkasbitung", "Commuter Line Cikarang", "Commuter Line Bogor", "Commuter Line Yogyakarta",
       "Commuter Line Tangerang", "Commuter Line Tanjungpriuk", "Commuter Line BST"}
PHYS = {"krl": (0.45, 4.2, 0.85), "loco/krd": (0.15, 2.3, 0.15), "freight": (0.08, 1.2, 0.08)}   # cluster medians
def group(g): return "krl" if g["service"] in KRL else ("freight" if g["cls"] == "freight" else "loco/krd")

db = sqlite3.connect("/home/user/Gapeka-parser/gapeka.db")
legs, total = pm.build(db, "track")
for g in legs: g["grp"] = group(g)
train = [g for g in legs if not g["test"] and not g["noted"]]
test = [g for g in legs if g["test"]]
print(f"legs with profile: {len(legs)} of {total}")

# simulate once per group physics (fixed), cache raw sim seconds
def raw(L):
    out = np.zeros(len(L)); by = defaultdict(list)
    for i, g in enumerate(L): by[g["grp"]].append(i)
    for gr, idx in by.items():
        sub = [L[i] for i in idx]
        out[idx] = pm.simulate(pm.prepare(sub), len(sub), *PHYS[gr])
    return out
s_tr, s_te = raw(train), raw(test)
nh_tr = np.array([len(g["halts"]) for g in train], float); nh_te = np.array([len(g["halts"]) for g in test], float)
y_tr = np.array([g["T"] for g in train], float); y_te = np.array([g["T"] for g in test], float)

def fit_lin(mask):
    """T = sim*(1+m) + c + d*halts, least absolute deviation (only 3 linear params -> IRLS)"""
    X = np.column_stack([s_tr[mask], np.ones(mask.sum()), nh_tr[mask]]); y = y_tr[mask]
    c = np.linalg.lstsq(X, y, rcond=None)[0]
    for _ in range(40):
        w = 1 / np.maximum(np.abs(y - X @ c), 5.0)
        c = np.linalg.lstsq(X * w[:, None] ** .5, y * w ** .5, rcond=None)[0]
    return c
grp_tr = np.array([g["grp"] for g in train]); grp_te = np.array([g["grp"] for g in test])
svc_tr = np.array([g["service"] for g in train]); svc_te = np.array([g["service"] for g in test])
coef = {gr: fit_lin(grp_tr == gr) for gr in PHYS}
for sv, n in Counter(svc_tr).items():
    if n >= 40: coef[sv] = fit_lin(svc_tr == sv)
def pred(s, nh, grp, svc):
    return np.array([np.dot(coef.get(sv, coef[gr]), [x, 1, h]) for x, h, gr, sv in zip(s, nh, grp, svc)])
p_tr, p_te = pred(s_tr, nh_tr, grp_tr, svc_tr), pred(s_te, nh_te, grp_te, svc_te)

def bands(e):
    a = np.abs(e) / 60
    return f"<0.5: {np.mean(a < .5):4.0%}   0.5-1: {np.mean((a >= .5) & (a <= 1)):4.0%}   >1: {np.mean(a > 1):4.0%}"
print(f"{'fixed physics, per-service m/c/d':40s} {bands(p_te - y_te)}")

# section correction
def sec_feats(g):
    out, pos = defaultdict(float), 0
    for a, b in zip(g["stations"], g["stations"][1:]):
        nc = max(1, int(round(pm.profile(a, b)[0] * 1000 / pm.DX)))
        out[(g["grp"], a, b)] += float(np.sum(pm.DX / (g["cap"][pos:pos + nc] / 3.6))); pos += nc
    return out
F_tr = [sec_feats(g) for g in train]; F_te = [sec_feats(g) for g in test]
idx, rows, cols, vals = {}, [], [], []
for r, F in enumerate(F_tr):
    for k, v in F.items(): rows.append(r); cols.append(idx.setdefault(k, len(idx))); vals.append(v)
A = csr_matrix((vals, (rows, cols)), shape=(len(train), len(idx)))
res = y_tr - p_tr; keep = np.ones(len(res), bool)
for _ in range(3):
    f = lsqr(A[keep], res[keep], damp=3.0)[0]; keep = np.abs(res - A @ f) < 90
c_te = np.array([sum(v * f[idx[k]] for k, v in F.items() if k in idx) for F in F_te])
pc = p_te + c_te
print(f"{'+ section correction':40s} {bands(pc - y_te)}")
noted = np.array([g["noted"] for g in test])
print(f"{'   legs without crossing/overtake note':40s} {bands((pc - y_te)[~noted])}   (n {(~noted).sum()})")
print(f"{'   legs with a note':40s} {bands((pc - y_te)[noted])}   (n {noted.sum()})")
for gr in PHYS:
    m = (grp_te == gr) & ~noted
    print(f"   {gr:9s} (no note)                       {bands((pc - y_te)[m])}   (n {m.sum()})")
# step 4: whole minutes like GAPEKA (start time is a whole minute, so round the predicted running time)
r = np.round(pc / 60) * 60 - y_te
print(f"{'rounded to whole minutes, no note':40s} exact {np.mean(r[~noted] == 0):4.0%}   off 1 min {np.mean(np.abs(r[~noted]) == 60):4.0%}   off >1 min {np.mean(np.abs(r[~noted]) > 60):4.0%}")
# leg types
for lab, fn in (("stop->stop", lambda g: g["start_rest"] and g["end_rest"]), ("pass->pass", lambda g: not g["start_rest"] and not g["end_rest"]),
                ("mixed", lambda g: g["start_rest"] != g["end_rest"]), ("with halts", lambda g: bool(g["halts"]))):
    m = np.array([fn(g) for g in test]) & ~noted
    print(f"   {lab:11s} (no note)                     {bands((pc - y_te)[m])}   (n {m.sum()})")
for gr, c in coef.items():
    if gr in PHYS: print(f"group {gr}: margin {c[0]-1:+.1%}  const {c[1]:.0f} s  per halt {c[2]:.0f} s")
