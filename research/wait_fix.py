"""Test fixes for waiting-train departures. Gaps fitted (median) on training trains, scored on held-out trains
(crc32(train) % 5 == 0). error = model - GAPEKA (minus = GAPEKA departs later)."""
import json, zlib
from collections import defaultdict
import numpy as np
src = open("waits11.py").read()
src = src.replace('res[("double" if dbl else "single", why)].append((wrap(W[4] - ref) / 60, wrap(W[4] - old) / 60 if old else None, line(s)))',
                  'res[("double" if dbl else "single", why)].append((wrap(W[4] - ref) / 60, wrap(W[4] - old) / 60 if old else None, line(s), s, w, ov, ref))')
exec(src.split('print("overtaking, waiting train departure')[0])
CTC = set(json.load(open("ctc_stations.json")))
test = lambda no: zlib.crc32(no.encode()) % 5 == 0
grp = lambda no: {"krl": "KRL", "freight": "freight"}.get(ttype(no), "passenger")

def score(name, items):
    """items: list of (key, train, gap_minutes) ; prediction = median gap of training items with the same key"""
    med = {}
    by = defaultdict(list)
    for k, no, g in items:
        if not test(no): by[k].append(g)
    allg = np.median([g for k, no, g in items if not test(no)])
    E = defaultdict(list)
    for k, no, g in items:
        if test(no):
            p = np.median(by[k]) if len(by[k]) >= 5 else allg
            E[k].append(p - g)
    allE = np.concatenate([np.array(v) for v in E.values()])
    print(f"\n{name}:  all held-out n={len(allE)}  exact(±0.5) {np.mean(np.abs(allE) <= .5):.0%}  ±1 {np.mean(np.abs(allE) <= 1):.0%}")
    for k in sorted(E, key=str):
        e = np.array(E[k])
        print(f"   {str(k):60s} n={len(e):3d} gap {np.median(by[k]) if len(by[k]) >= 5 else allg:+5.1f}  exact {np.mean(np.abs(e) <= .5):4.0%} ±1 {np.mean(np.abs(e) <= 1):4.0%}  early {np.mean(e < -.5):4.0%} late {np.mean(e > .5):4.0%}")
    return allE

# ---------------- crossings
cases = defaultdict(list)
for (s, w), evs in groups.items():
    W = at(w, s)
    cr = [at(ov, s) for k, ov in evs if k == "cross"]
    cr = [O for O in cr if O is not None and t_at(O) is not None]
    if not cr: continue
    # last opponent
    O = max(cr, key=lambda O: wrap(t_at(O) - W[3]))
    both = O[3] is not None and O[4] is not None
    g = grp(w)
    if g == "KRL": continue
    arr_o = t_at(O)
    cases["A other arr/pass"].append(((g, both), w, wrap(W[4] - arr_o) / 60))
    if both:
        cases["B other dep (both stop)"].append(((g, both), w, wrap(W[4] - O[4]) / 60))
        # which train arrived second? if the other train arrived first, the waiter is really the one crossed
        cases["C later of both arrivals"].append(((g, "other first" if wrap(arr_o - W[3]) < 0 else "waiter first"), w,
                                                 wrap(W[4] - max(W[3], arr_o, key=lambda x: wrap(x - W[3]))) / 60))
        # the other train's dwell tells whether it waits too (both wait for each other)
        dwell_o = wrap(O[4] - O[3]) / 60
        cases["D other arr, split by other dwell"].append(((g, "other dwell<=2" if dwell_o <= 2 else "other dwell>2"), w, wrap(W[4] - arr_o) / 60))
        cases["E other dep, split by other dwell"].append(((g, "other dwell<=2" if dwell_o <= 2 else "other dwell>2"), w, wrap(W[4] - O[4]) / 60))
for k in ("A other arr/pass",):
    score("CROSSING " + k, cases[k])
for k in ("B other dep (both stop)", "C later of both arrivals", "D other arr, split by other dwell", "E other dep, split by other dwell"):
    score("CROSSING both stop: " + k, cases[k])
# best combined for both-stop: dep = max(other arr + g1, other dep + g2)?
both_items = [(k, w, g) for k, w, g in cases["A other arr/pass"] if k[1]]
print("\nboth-stop crossings: distribution of waiter dep - other arr (min):", np.percentile([g for _, _, g in both_items], [10, 25, 50, 75, 90]).round(1))
print("                                waiter dep - other dep (min):", np.percentile([g for _, _, g in cases["B other dep (both stop)"]], [10, 25, 50, 75, 90]).round(1))

# ---------------- overtakes: fit gap per (type, track, CTC)
ot = []
for k, xs in res.items():
    for gap, _, ln, s, w, ov, ref in xs:
        if grp(w) == "KRL": continue
        ot.append(((grp(w), k[0], "CTC" if s in CTC else "-"), w, gap))
score("OVERTAKE gap per (type, track, CTC)", ot)
score("OVERTAKE gap per (type, track)", [((k[0], k[1]), w, g) for k, w, g in ot])
score("OVERTAKE gap per (type, track, block point kind)", [((grp(w), k[0], k[1]), w, gap) for k, xs in res.items() for gap, _, ln, s, w, ov, ref in xs if grp(w) != "KRL"])
