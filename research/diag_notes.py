import os
os.environ.setdefault("RULES", "rules.json"); os.environ.setdefault("HALT_DWELL", "1")
import sqlite3
from collections import defaultdict
import numpy as np
import relearn as R
legs, y, pred, base, test, noted, typ = R.legs, R.y, R.pred, R.base, R.test, R.noted, R.typ
db = sqlite3.connect("/home/user/Gapeka-parser/gapeka.db")
sym = defaultdict(set)
for no, st, s in db.execute("select train_no, station, symbol from notes"):
    sym[(no, st)].add(s)
ROLE = {"x": "crossing", "//": "overtakes another (passes it)", "=": "waits to be overtaken"}
def bands(e):
    a = np.abs(e) / 60
    return f"<0.5 {np.mean(a < .5):4.0%}  0.5-1 {np.mean((a >= .5) & (a <= 1)):4.0%}  >1 {np.mean(a > 1):4.0%}  mean {np.mean(e):+5.0f} s  (n {len(a)})"
print("\n=== Q1: legs with a crossing/overtake note, held-out trains, full model (err = model - GAPEKA; minus = GAPEKA slower)")
groups = defaultdict(list)
for i in np.where(test & noted)[0]:
    g = legs[i]
    for end, st, rest in (("start", g["stations"][0], g["start_rest"]), ("end", g["stations"][-1], g["end_rest"])):
        for s in sym.get((g["train"], st), ()):
            groups[(ROLE.get(s, s), "stops" if rest else "passes", end)].append(i)
for k in sorted(groups, key=lambda k: -len(groups[k])):
    ii = groups[k]
    if len(ii) >= 5:
        print(f"  {k[0]:30s} train {k[1]:6s} note at leg {k[2]:5s}  {bands(pred[ii] - y[ii])}")
m = test & ~noted
print("\n=== Q4: direction of the errors, held-out legs without notes, full model")
for lab, mm in [("all", m)] + [(t, m & (typ == t)) for t in R.TYPES]:
    e = (pred - y)[mm] / 60
    small = np.abs(e) < .5
    mid = (np.abs(e) >= .5) & (np.abs(e) <= 1)
    big = np.abs(e) > 1
    def split(sel):
        if sel.sum() == 0:
            return "-"
        return f"model faster {np.mean(e[sel] < 0):4.0%} / slower {np.mean(e[sel] > 0):4.0%}"
    print(f"  {lab:9s} <0.5 min ({small.mean():4.0%}): {split(small)}   0.5-1 ({mid.mean():4.0%}): {split(mid)}   >1 ({big.mean():4.0%}): {split(big)}")
e = (pred - y)[m] / 60
print("\n  error distribution inside the < 0.5 min band (minutes; minus = model faster than GAPEKA):")
h, edges = np.histogram(e[np.abs(e) < .5], bins=[-.5, -.4, -.3, -.2, -.1, 0, .1, .2, .3, .4, .5])
for c, lo, hi in zip(h, edges, edges[1:]):
    print(f"    {lo:+.1f} to {hi:+.1f}: {c:5d} {'#' * int(60 * c / h.max())}")
r = np.round(pred[m] / 60) - y[m] / 60
print(f"\n  after rounding the model to whole minutes: exact {np.mean(r == 0):4.0%}  1 min early {np.mean(r == -1):4.0%}  1 min late {np.mean(r == 1):4.0%}  more {np.mean(np.abs(r) > 1):4.0%}")
