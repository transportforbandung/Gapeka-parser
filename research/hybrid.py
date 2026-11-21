"""Standard running time lookup (identical leg pattern on other trains) with the physics model as fallback."""
import numpy as np
from collections import defaultdict
from siding_lib import legs, sims, full_eval, train_i, test_i, y, grp

S = sims(300)
pc = full_eval(S[(0, 0)], "physics + section correction")
def key(g, level):
    base = (tuple(g["stations"]), g["start_rest"], g["end_rest"], len(g["halts"]))
    return {"service": (g["service"],) + base, "group": (g["grp"],) + base, "any": base}[level]
look = {lv: defaultdict(list) for lv in ("service", "group", "any")}
for i in train_i:
    for lv in look: look[lv][key(legs[i], lv)].append(y[i])
pred, src = [], []
for i in test_i:
    g = legs[i]
    for lv in ("service", "group"):
        v = look[lv].get(key(g, lv))
        if v:
            pred.append(float(np.median(v))); src.append(lv); break
    else:
        pred.append(pc[i]); src.append("model")
pred, src = np.array(pred), np.array(src)
e = (pred - y[test_i]) / 60
noted = np.array([legs[i]["noted"] for i in test_i])
def b(a):
    a = np.abs(a); return f"<0.5 {np.mean(a < .5):4.0%}  0.5-1 {np.mean((a >= .5) & (a <= 1)):4.0%}  >1 {np.mean(a > 1):4.0%}  (n {len(a)})"
print(f"{'hybrid: lookup, else physics':44s} all: {b(e)}   |  no note: {b(e[~noted])}")
for s_ in ("service", "group", "model"):
    m = (src == s_) & ~noted
    print(f"   source {s_:8s} no note: {b(e[m])}")
    m2 = (src == s_) & ~noted
    if s_ != "model":
        print(f"   physics on the same legs        : {b(((pc[np.array(test_i)] - y[test_i]) / 60)[m2])}")
