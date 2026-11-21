import csv, math, sys
from collections import Counter, defaultdict
import numpy as np
sys.path.insert(0, "/home/user/Gapeka-parser")
import sqlite3, gapeka_pipeline as gp
db = sqlite3.connect("model.db"); dist = gp.make_distance(db)
D = list(csv.DictReader(open("detail.csv")))
# ceiling: one value per (halt, prev)
grp = defaultdict(list)
for r in D: grp[(r["halt"], r["prev"])].append(float(r["kai"]))
ok = sum(Counter(v).most_common(1)[0][1] for v in grp.values())
print(f"ceiling (one offset per halt+direction): {ok}/{len(D)} = {ok/len(D):.0%} exact")
H = {(r["train"], r["halt"]): r for r in csv.DictReader(open("graph_halts.csv"))}
def pred(r, c, use_min=True):
    h = H[(r["train"], r["halt"])]
    chain = h["chain"].split("-"); i = chain.index(r["halt"])
    p = [dist(chain[k], chain[k+1]) for k in range(len(chain)-1)]
    T = float(r["T_min"] if use_min else r["T"]) * 60
    if not all(p): return T * i / (len(chain)-1) / 60
    w = [x + c for x in p]
    return T * sum(w[:i]) / sum(w) / 60
def score(rows, c, rr, use_min=True):
    e = np.array([math.floor(pred(r, c, use_min) + rr) - float(r["kai"]) for r in rows])
    return np.mean(e == 0), np.mean(abs(e) <= 1), np.mean(abs(e))
bd = [r for r in D if r["service"].endswith(("Bandung Raya", "Garut"))]
sb = [r for r in D if r not in bd]
for name, rows in (("all", D), ("bandung", bd), ("surabaya", sb)):
    best = max(((score(rows, c, rr), c, rr) for c in np.arange(0, 12.1, .5) for rr in np.arange(0, 1.0, .1)), key=lambda x: (x[0][0], -x[0][2]))
    print(f"best on {name:9s}: c={best[1]:.1f} km-equiv, round offset {best[2]:.1f} -> exact {best[0][0]:.0%} ±1 {best[0][1]:.0%} mae {best[0][2]:.2f}")
for c, rr in ((4.25, .5), (3.0, .5), (5.0, .5), (4.0, .4)):
    print(f"c={c} rr={rr}: all {score(D,c,rr)[0]:.0%}  bandung {score(bd,c,rr)[0]:.0%}  surabaya {score(sb,c,rr)[0]:.0%}  | mae all {score(D,c,rr)[2]:.2f}")
# cross-validation
for tr, te, nm in ((bd, sb, "fit Bandung -> test Surabaya"), (sb, bd, "fit Surabaya -> test Bandung")):
    best = max(((score(tr, c, rr), c, rr) for c in np.arange(0, 12.1, .5) for rr in np.arange(0, 1.0, .1)), key=lambda x: (x[0][0], -x[0][2]))
    s = score(te, best[1], best[2])
    print(f"{nm}: c={best[1]}, rr={best[2]:.1f} -> test exact {s[0]:.0%} ±1 {s[1]:.0%} mae {s[2]:.2f}")
