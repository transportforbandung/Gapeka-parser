import csv, math, sys
import numpy as np
sys.path.insert(0, "/home/user/Gapeka-parser")
import sqlite3, gapeka_pipeline as gp
db = sqlite3.connect("model.db"); dist = gp.make_distance(db)
D = list(csv.DictReader(open("detail.csv")))
H = {(r["train"], r["halt"]): r for r in csv.DictReader(open("graph_halts.csv"))}
def pred(r, c, d, show_dep):
    h = H[(r["train"], r["halt"])]
    chain = h["chain"].split("-"); i = chain.index(r["halt"])
    p = [dist(chain[k], chain[k+1]) for k in range(len(chain)-1)]
    if not all(p): return None
    T = float(r["T_min"]) * 60 - d * (len(chain) - 2)
    w = [x + c for x in p]
    return (T * sum(w[:i]) / sum(w) + d * ((i - 1) + show_dep)) / 60
def score(rows, c, d, sd, rr):
    e = []
    for r in rows:
        p = pred(r, c, d, sd)
        if p is not None: e.append(math.floor(p + rr) - float(r["kai"]))
    e = np.array(e); return np.mean(e == 0), np.mean(abs(e) <= 1), np.mean(abs(e)), len(e)
bd = [r for r in D if r["service"].endswith(("Bandung Raya", "Garut"))]
sb = [r for r in D if r not in bd]
grid = [(c, d, sd, rr) for c in np.arange(0, 12.1, .5) for d in (0, 30, 60) for sd in (0, 1) for rr in (0, .25, .5, .75)]
for name, rows in (("all", D), ("bandung", bd), ("surabaya", sb)):
    res = sorted(((score(rows, *g), g) for g in grid), key=lambda x: (-x[0][0], x[0][2]))[:4]
    for s, g in res:
        print(f"{name:9s} c={g[0]:4.1f} dwell={g[1]:2d}s show={'dep' if g[2] else 'arr'} rr={g[3]:.2f} -> exact {s[0]:.0%} ±1 {s[1]:.0%} mae {s[2]:.2f} (n {s[3]})")
for g in ((4.5, 30, 0, .5), (4.5, 60, 1, .25), (5.5, 30, 0, .5), (6.0, 60, 0, .5)):
    print(g, "bd", [round(x, 2) for x in score(bd, *g)[:3]], "sb", [round(x, 2) for x in score(sb, *g)[:3]], "all", [round(x, 2) for x in score(D, *g)[:3]])
print("--- automatic: c from commuter envelope fit (176 s / 41.4 s/km = 4.25 km), dwell 30 s")
for lab, rows, sd in (("bandung, show arrival", bd, 0), ("surabaya, show departure", sb, 1), ("surabaya, show arrival", sb, 0)):
    s = score(rows, 4.25, 30, sd, .5)
    print(f"  {lab:26s} exact {s[0]:.0%}  ±1 {s[1]:.0%}  mae {s[2]:.2f}  n {s[3]}")
cur = [r for r in D if r["current pipeline (no csv)"]]
for lab, rows in (("bandung", bd), ("surabaya", sb)):
    e = np.array([round(float(r["current pipeline (no csv)"])) - float(r["kai"]) for r in rows])
    print(f"  current model {lab:10s} exact {np.mean(e==0):.0%}  ±1 {np.mean(abs(e)<=1):.0%}  mae {np.mean(abs(e)):.2f}  n {len(e)}")
