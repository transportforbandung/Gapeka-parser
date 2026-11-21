"""Relearn the network: isolate errors per directed section and per station end, by train type and stop/pass.

Base: physics (fixed accel/brake per rolling-stock group) + linear calibration (margin, const, halt dwell) per TRAIN TYPE
      (krl / commuter / intercity / freight), so commuter and intercity no longer share corrections.
Corrections (ridge regression on training residuals, seconds):
  section[(a, b, type)]               every traversal of the directed pair a->b
  station[(s, 'dep'|'arr', 'stop'|'pass', type)]   at the first / last timed point of a leg
Writes station_effects.csv, section_effects.csv, relearn_test.csv; prints bands and a diagnosis.
"""
import csv
import sqlite3
from collections import defaultdict

import numpy as np
from scipy.sparse import csr_matrix
from scipy.sparse.linalg import lsqr

import physics_model as pm

KRL = {"Commuter Line Rangkasbitung", "Commuter Line Cikarang", "Commuter Line Bogor", "Commuter Line Yogyakarta",
       "Commuter Line Tangerang", "Commuter Line Tanjungpriuk", "Commuter Line BST"}
PHYS = {"krl": (0.45, 4.2, 0.85), "loco/krd": (0.15, 2.3, 0.15), "freight": (0.08, 1.2, 0.08)}
TYPES = ("krl", "commuter", "intercity", "freight")


def ttype(g):
    s = g["service"]
    if s in KRL:
        return "krl"
    if g["cls"] == "freight":
        return "freight"
    if s.startswith("Commuter") or s in ("Ka Feeder", "Bandara SMO"):
        return "commuter"
    return "intercity"


def phys_group(t):
    return "krl" if t == "krl" else ("freight" if t == "freight" else "loco/krd")


def apply_zones(g, zones):
    """manual station zones: (station, neighbour, length m, speed, types) for trains stopping at station"""
    cap, st = g["cap"], g["stations"]
    for s, nb, L, v, types in zones:
        if g["type"] not in types:
            continue
        n = min(int(L / pm.DX), len(cap))
        if g["start_rest"] and st[0] == s and st[1] == nb:
            cap = cap.copy(); cap[:n] = np.minimum(cap[:n], v)
        if g["end_rest"] and st[-1] == s and st[-2] == nb:
            cap = cap.copy(); cap[-n:] = np.minimum(cap[-n:], v)
    return cap


ZONES = [("KAC", "GDB", 1180, 30.0, ("intercity", "freight"))]      # user: Kiaracondong crossover to crossover

db = sqlite3.connect("/home/user/Gapeka-parser/gapeka.db")
names = dict(db.execute("select code,name from stations"))
legs, total = pm.build(db, "track")
for g in legs:
    g["type"] = ttype(g)
    g["cap"] = apply_zones(g, ZONES)
print(f"legs with profile: {len(legs)} of {total}")

# ---- physics simulation per rolling-stock group
sim = np.zeros(len(legs))
by = defaultdict(list)
for i, g in enumerate(legs):
    by[phys_group(g["type"])].append(i)
for gr, idx in by.items():
    sub = [legs[i] for i in idx]
    sim[idx] = pm.simulate(pm.prepare(sub), len(sub), *PHYS[gr])

y = np.array([g["T"] for g in legs], float)
nh = np.array([len(g["halts"]) for g in legs], float)
typ = np.array([g["type"] for g in legs])
train = np.array([not g["test"] and not g["noted"] for g in legs])
test = np.array([g["test"] for g in legs])
noted = np.array([g["noted"] for g in legs])


def lad(X, yy):
    c = np.linalg.lstsq(X, yy, rcond=None)[0]
    for _ in range(40):
        w = 1 / np.maximum(np.abs(yy - X @ c), 5.0)
        c = np.linalg.lstsq(X * w[:, None] ** .5, yy * w ** .5, rcond=None)[0]
    return c


X = np.column_stack([sim, np.ones(len(legs)), nh])
calib = {t: lad(X[train & (typ == t)], y[train & (typ == t)]) for t in TYPES}
base = np.array([X[i] @ calib[typ[i]] for i in range(len(legs))])
for t in TYPES:
    c = calib[t]
    print(f"calibration {t:9s}: margin {c[0] - 1:+.1%}  const {c[1]:4.0f} s  per halt {c[2]:3.0f} s")

# ---- correction features
def feats(g):
    f = []
    st, t = g["stations"], g["type"]
    for a, b in zip(st, st[1:]):
        f.append(("sec", a, b, t))
    f.append(("dep", st[0], "stop" if g["start_rest"] else "pass", t))
    f.append(("arr", st[-1], "stop" if g["end_rest"] else "pass", t))
    return f


F = [feats(g) for g in legs]
col = {}
rows, cols = [], []
tr_idx = np.where(train)[0]
for r, i in enumerate(tr_idx):
    for k in F[i]:
        rows.append(r)
        cols.append(col.setdefault(k, len(col)))
A = csr_matrix((np.ones(len(rows)), (rows, cols)), shape=(len(tr_idx), len(col)))
res = y[tr_idx] - base[tr_idx]
keep = np.ones(len(res), bool)
for _ in range(3):
    eff = lsqr(A[keep], res[keep], damp=2.0)[0]
    keep = np.abs(res - A @ eff) < 150
support = np.asarray(A[keep].sum(0)).ravel()


def correction(i):
    return sum(eff[col[k]] for k in F[i] if k in col)


pred = base + np.array([correction(i) for i in range(len(legs))])


def bands(e):
    a = np.abs(e) / 60
    return f"<0.5 {np.mean(a < .5):4.0%}  0.5-1 {np.mean((a >= .5) & (a <= 1)):4.0%}  >1 {np.mean(a > 1):4.0%}  (n {len(a)})"


print("\nheld-out trains, legs without crossing/overtake note")
m = test & ~noted
print(f"  physics + calibration only      {bands((base - y)[m])}")
print(f"  + section/station corrections   {bands((pred - y)[m])}")
for t in TYPES:
    mm = m & (typ == t)
    print(f"     {t:9s}                    {bands((pred - y)[mm])}")
print(f"  legs with a note                {bands((pred - y)[test & noted])}")

# ---- outputs
with open("station_effects.csv", "w", newline="") as f:
    w = csv.writer(f)
    w.writerow(["station", "name", "end", "stop_or_pass", "train_type", "effect_s", "legs"])
    for k, j in col.items():
        if k[0] in ("dep", "arr"):
            w.writerow([k[1], names.get(k[1], ""), k[0], k[2], k[3], round(eff[j]), int(support[j])])
with open("section_effects.csv", "w", newline="") as f:
    w = csv.writer(f)
    w.writerow(["from", "to", "train_type", "effect_s", "legs"])
    for k, j in col.items():
        if k[0] == "sec":
            w.writerow([k[1], k[2], k[3], round(eff[j]), int(support[j])])
with open("relearn_test.csv", "w", newline="") as f:
    w = csv.writer(f)
    w.writerow(["train", "service", "type", "from", "to", "ends", "note", "T_min", "pred_min", "err_min"])
    for i in np.where(test)[0]:
        g = legs[i]
        w.writerow([g["train"], g["service"], g["type"], g["stations"][0], g["stations"][-1],
                    ("stop" if g["start_rest"] else "pass") + "->" + ("stop" if g["end_rest"] else "pass"),
                    g["noted"], y[i] / 60, round(pred[i] / 60, 2), round((pred[i] - y[i]) / 60, 2)])

# ---- diagnosis: station ends -- which station effects are big, and do they depend on type / stop-pass / side?
print("\nSTATION ENDS: effects >= 45 s with >= 5 legs (seconds; + = GAPEKA slower than physics)")
st_eff = defaultdict(dict)
for k, j in col.items():
    if k[0] in ("dep", "arr") and support[j] >= 5:
        st_eff[k[1]][(k[0], k[2], k[3])] = (eff[j], int(support[j]))
rank = sorted(st_eff, key=lambda s: -max(abs(v[0]) for v in st_eff[s].values()))
for s in rank[:30]:
    big = {k: v for k, v in st_eff[s].items() if abs(v[0]) >= 45}
    if not big:
        break
    allv = st_eff[s]
    cells = "  ".join(f"{e}/{sp}/{t}:{v[0]:+.0f}({v[1]})" for (e, sp, t), v in sorted(allv.items()))
    # classify the pattern
    tags = []
    ts = {k[2] for k in big}
    if len({k[2] for k in allv}) > 1 and len(ts) < len({k[2] for k in allv}):
        tags.append("TYPE-SPECIFIC:" + ",".join(sorted(ts)))
    if {k[1] for k in big} == {"stop"} and any(k[1] == "pass" for k in allv):
        tags.append("STOP-ONLY")
    if {k[1] for k in big} == {"pass"} and any(k[1] == "stop" for k in allv):
        tags.append("PASS-ONLY")
    if len({k[0] for k in big}) == 1 and len({k[0] for k in allv}) == 2:
        tags.append("ONE-SIDE:" + next(iter({k[0] for k in big})))
    print(f"  {s:5s} {names.get(s, ''):18s} {' '.join(tags) or 'GENERAL'}")
    print(f"        {cells}")

# ---- diagnosis: directed sections -- direction asymmetry and type dependence
print("\nSECTIONS: largest effects with >= 5 legs, with the opposite direction alongside (seconds)")
sec = {}
for k, j in col.items():
    if k[0] == "sec" and support[j] >= 5:
        sec[(k[1], k[2], k[3])] = (eff[j], int(support[j]))
for (a, b, t), (e, n) in sorted(sec.items(), key=lambda kv: -abs(kv[1][0]))[:30]:
    if abs(e) < 30:
        break
    opp = sec.get((b, a, t))
    other_types = {tt: sec[(a, b, tt)][0] for tt in TYPES if tt != t and (a, b, tt) in sec}
    print(f"  {a:>5s} -> {b:<5s} {t:9s} {e:+5.0f} s ({n:3d})   opposite: {('%+.0f' % opp[0]) if opp else '  -':>5s}   "
          f"other types: {', '.join(f'{k} {v:+.0f}' for k, v in other_types.items()) or '-'}")
