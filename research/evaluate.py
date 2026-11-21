"""Score halt-time models against KAI published halt times."""
import csv
import math
import shutil
import sqlite3
import sys
from collections import defaultdict

import numpy as np

sys.path.insert(0, "/home/user/Gapeka-parser")
import gapeka_pipeline as gp  # noqa: E402

# pure model (no halt_offsets.csv) on a copy of the db
shutil.copy("/home/user/Gapeka-parser/gapeka.db", "model.db")
db = sqlite3.connect("model.db")
dist = gp.make_distance(db)
gp.estimate_halts(db, dist, 30)
pure = {(r[0], r[1]): r[2] for r in db.execute("select train_no,station,arr_s from halt_est")}

# all legs between timed points, keyed by (prev, mids pattern, next)
legs = defaultdict(list)          # key -> [(train, service, T)]
svc = dict(db.execute("select no,service from trains"))
for no in svc:
    rows = db.execute("select seq,station,kind,arr_s,dep_s from stops where train_no=? order by seq", (no,)).fetchall()
    last, mids = None, []
    for seq, st, kind, a, d in rows:
        if kind == "pass" and d is not None:
            a = d
        elif kind in ("halt", "pass"):
            mids.append((st, kind))
            continue
        if last is not None and a is not None:
            legs[(last[0], tuple(mids), st)].append((no, svc[no], a - last[1]))
        last, mids = ((st, d) if d is not None else None), []

H = list(csv.DictReader(open("graph_halts.csv")))
G = {(r["train"], r["halt"]): r for r in H}


def key_of(h):
    rows = db.execute("select seq,station,kind,arr_s,dep_s from stops where train_no=? order by seq", (h["train"],)).fetchall()
    chain = h["chain"].split("-")
    mids = []
    for seq, st, kind, a, d in rows:
        pass
    # rebuild mids from chain + kinds
    kinds = {st: kind for _, st, kind, _, _ in rows}
    return (chain[0], tuple((s, kinds.get(s)) for s in chain[1:-1]), chain[-1])


def parts(chain):
    p = [dist(chain[i], chain[i + 1]) for i in range(len(chain) - 1)]
    return p if all(p) else None


# global stopping-train sub-leg model a + b*km, fitted on Commuter legs with no mids (stop to stop)
X, Y = [], []
for (A, mids, B), v in legs.items():
    if mids:
        continue
    d = dist(A, B)
    if not d or d < 1:
        continue
    for no, s, T in v:
        if s.startswith("Commuter") and 0 < T < 3600:
            X.append(d)
            Y.append(T)
# lower envelope per pair: technical time
env = defaultdict(lambda: 1e9)
for (A, mids, B), v in legs.items():
    if mids:
        continue
    d = dist(A, B)
    if d and d >= 1:
        for no, s, T in v:
            if s.startswith("Commuter") and 0 < T < 3600:
                env[(A, B, d)] = min(env[(A, B, d)], T)
ex = np.array([k[2] for k in env])
ey = np.array(list(env.values()), float)
a_env, b_env, _ = gp.robust_fit(ex, ey)
print(f"commuter stop-to-stop lower envelope: {a_env:.1f} s/km + {b_env:.0f} s  (n pairs {len(ex)})")
a_all, b_all, _ = gp.robust_fit(np.array(X), np.array(Y, float))
print(f"commuter stop-to-stop all legs:      {a_all:.1f} s/km + {b_all:.0f} s  (n {len(X)})")

models = defaultdict(list)       # name -> list of (pred_offset_min, kai_offset_min)
detail = []
for h in H:
    kai = float(h["kai_off"])
    t0, T = float(h["dep_prev"]), float(h["T"]) * 60
    chain = h["chain"].split("-")
    i = chain.index(h["halt"])
    p = parts(chain)
    k = key_of(h)
    Ts = sorted(x[2] for x in legs.get(k, []))
    Tref = Ts[0] if Ts else T
    Tp10 = Ts[max(0, int(len(Ts) * .1) - 0)] if Ts else T
    nh = sum(1 for s, kd in k[1] if kd == "halt")
    pred = {}
    pm = pure.get((h["train"], h["halt"]))
    if pm is not None:
        pred["current pipeline (no csv)"] = (pm - t0) / 60
    if h["graph_off"]:
        pred["graph line crossing"] = float(h["graph_off"])
    fkm = sum(p[:i]) / sum(p) if p else i / (len(chain) - 1)
    pred["km-proportional over actual T"] = T * fkm / 60
    pred["equal split over actual T"] = T * i / (len(chain) - 1) / 60
    pred["km-prop over T_min, front"] = Tref * fkm / 60
    pred["equal split over T_min, front"] = Tref * i / (len(chain) - 1) / 60
    if p:
        w = [a_env * x + b_env for x in p]
        pred["(a*km+b)-weighted T_min, front"] = Tref * sum(w[:i]) / sum(w) / 60
        pred["(a*km+b)-weighted actual T"] = T * sum(w[:i]) / sum(w) / 60
        pred["envelope a*km+b directly"] = sum(w[:i]) / 60
    else:
        pred["(a*km+b)-weighted T_min, front"] = pred["equal split over T_min, front"]
        pred["(a*km+b)-weighted actual T"] = pred["equal split over actual T"]
        pred["envelope a*km+b directly"] = pred["equal split over T_min, front"]
    for n, v in pred.items():
        models[n].append((v, kai, h))
    detail.append((h, pred, Tref, len(Ts), fkm))

print(f"\n{'model':38s} {'n':>4s}  " + "  ".join(f"{r:>17s}" for r in ("floor", "round", "ceil")))
for n, v in models.items():
    cells = []
    for rf in (lambda x: math.floor(x + 1e-6), lambda x: round(x), lambda x: math.ceil(x - 1e-6)):
        e = np.array([rf(p) - k for p, k, _ in v])
        cells.append(f"{np.mean(e == 0) * 100:4.0f}% ±1:{np.mean(abs(e) <= 1) * 100:3.0f}% mae {np.mean(abs(e)):.2f}")
    print(f"{n:38s} {len(v):4d}  " + "  ".join(cells))

with open("detail.csv", "w", newline="") as f:
    w = csv.writer(f)
    names = list(models)
    w.writerow(["train", "service", "halt", "prev", "next", "T", "T_min", "n_legs", "f_km", "kai"] + names)
    for h, pred, Tref, n, fkm in detail:
        w.writerow([h["train"], h["service"], h["halt"], h["prev"], h["next"], h["T"], Tref / 60, n, round(fkm, 3), h["kai_off"]]
                   + [round(pred[x], 2) if x in pred else "" for x in names])
