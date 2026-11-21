"""Grid search over freight acceleration (a0), power and braking; physics + per-type calibration, held-out trains."""
import itertools, json, sqlite3
import numpy as np
import physics_model as pm
from runtime_model import lad

db = sqlite3.connect("/home/user/Gapeka-parser/gapeka.db")
legs, _ = pm.build(db, "track")
legs = [g for g in legs if g["cls"] == "freight"]
RULES = [z for z in json.load(open("rules.json")) if z.get("kind") != "buffer" and "freight" in z["types"]]
for g in legs:                                   # station zones that apply to freight (same logic as relearn.apply_zones)
    cap, st = g["cap"], g["stations"]
    for z in RULES:
        for side in z["sides"]:
            L = z["dep_m"] if side == "dep" else z["arr_m"]
            n = min(int(L / pm.DX), len(cap))
            at, nb, rest, term = (st[0], st[1], g["start_rest"], g["first"]) if side == "dep" else (st[-1], st[-2], g["end_rest"], g["last"])
            w = z["when"]
            if not n or at != z["station"] or (z["towards"] != "*" and nb != z["towards"]): continue
            if (w == "stop" and not rest) or (w == "pass" and rest) or (w == "midstop" and not (rest and not term)) or (w == "terminal" and not (rest and term)): continue
            cap = cap.copy()
            if side == "dep": cap[:n] = np.minimum(cap[:n], z["speed"])
            else: cap[-n:] = np.minimum(cap[-n:], z["speed"])
    g["cap"] = cap
B = pm.prepare(legs)
y = np.array([g["T"] for g in legs], float)
feat = np.array([[1.0, len(g["halts"]), g["last"], g["first"], g["start_rest"], g["end_rest"]] for g in legs], float)
train = np.array([not g["test"] and not g["noted"] for g in legs]); test = np.array([g["test"] and not g["noted"] for g in legs])
kind = np.array([("stop" if g["start_rest"] else "pass") + "->" + ("stop" if g["end_rest"] else "pass") for g in legs])
print(f"freight legs {len(legs)} (stop->stop {np.sum(kind == 'stop->stop')}, pass->pass {np.sum(kind == 'pass->pass')}, mixed {np.sum((kind == 'stop->pass') | (kind == 'pass->stop'))})")
res = []
for a0, p, b in itertools.product((0.05, 0.08, 0.12, 0.18, 0.25, 0.35), (0.6, 1.2, 2.0, 3.0), (0.05, 0.08, 0.12, 0.2, 0.3, 0.45)):
    s = pm.simulate(B, len(legs), a0, p, b)
    X = np.column_stack([s, feat])
    c = lad(X[train], y[train])
    e = (X @ c - y) / 60
    a = np.abs(e[test])
    ends = {k: np.mean(np.abs(e[test & (kind == k)]) < .5) for k in ("stop->stop", "pass->pass", "stop->pass", "pass->stop")}
    res.append((np.mean(a > 1), -np.mean(a < .5), a0, p, b, np.mean(a < .5), np.mean((a >= .5) & (a <= 1)), np.mean(a > 1), np.mean(a), c, ends))
res.sort(key=lambda r: (r[8]))
cur = [r for r in res if (r[2], r[3], r[4]) == (0.08, 1.2, 0.08)][0]
print(f"current  a0 0.08 power 1.2 brake 0.08 : <0.5 {cur[5]:4.0%} 0.5-1 {cur[6]:4.0%} >1 {cur[7]:4.0%} mean|err| {cur[8] * 60:4.1f} s   const {cur[9][1]:.0f} s  margin {cur[9][0] - 1:+.1%}")
print("best 10 by mean |error|:")
for r in res[:10]:
    print(f"  a0 {r[2]:.2f} power {r[3]:.1f} brake {r[4]:.2f} : <0.5 {r[5]:4.0%} 0.5-1 {r[6]:4.0%} >1 {r[7]:4.0%} mean|err| {r[8] * 60:4.1f} s   const {r[9][1]:.0f} s  margin {r[9][0] - 1:+.1%}  "
          + "  ".join(f"{k} {v:.0%}" for k, v in r[10].items()))
print("worst 3:")
for r in res[-3:]:
    print(f"  a0 {r[2]:.2f} power {r[3]:.1f} brake {r[4]:.2f} : <0.5 {r[5]:4.0%} >1 {r[7]:4.0%} mean|err| {r[8] * 60:4.1f} s")
