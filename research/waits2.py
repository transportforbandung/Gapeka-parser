"""Predict the departure of waiting trains from the other trains' times."""
import csv
from collections import defaultdict
import numpy as np
exec(open("waits.py").read().split("out = []")[0])          # reuse loading + event detection from waits.py

def ref_times(kind, s, w, ov):
    """candidate reference times for one waiting event"""
    W, O = at(w, s), at(ov, s)
    c = {}
    if kind == "cross":
        c["other arrives/passes"] = t_at(O)
        if O[3] is not None and O[4] is not None:
            c["other departs"] = O[4]
    else:
        c["overtaker passes station"] = t_at(O) if O[3] is None else O[4]
        for r in [r for r in rows[w] if r[0] > W[0]]:
            q = at(ov, r[1])
            if q is not None and t_at(q) is not None:
                c["overtaker at next station"] = t_at(q)
                break
    return c

# group all notes of one waiting train at one station (it can wait for several trains)
groups = defaultdict(list)
for kind, s, w, ov in events:
    W = at(w, s)
    if W is None or W[3] is None or W[4] is None: continue
    groups[(s, w)].append((kind, ov))
print(f"waiting trains (station, train): {len(groups)};  with more than one train to wait for: {sum(len(v) > 1 for v in groups.values())}")

# 1) crossing where both stop: arrival or departure of the other train?
both = []
for (s, w), evs in groups.items():
    for kind, ov in evs:
        if kind == "cross":
            O = at(ov, s)
            if O[3] is not None and O[4] is not None:
                W = at(w, s)
                both.append((wrap(W[4] - O[3]), wrap(W[4] - O[4])))
if both:
    b = np.array(both) / 60
    print(f"\ncrossing where both stop (n={len(b)}): waiter departs after other's ARRIVAL: median {np.median(b[:, 0]):+.1f} min, "
          f"within 0.5 of +2: {np.mean(np.abs(b[:, 0] - 2) <= .5):.0%};  after other's DEPARTURE: median {np.median(b[:, 1]):+.1f} min, "
          f"within 0.5 of median: {np.mean(np.abs(b[:, 1] - np.median(b[:, 1])) <= .5):.0%}")

# 2) predictor: dep = max(arr + min dwell, max over events (reference + g)); evaluate per waiting train
DWELL_MIN = {"krl": 60, "commuter": 60, "intercity": 60, "freight": 60}
def evaluate(g_cross, g_ot, cross_ref, ot_ref, label):
    res = defaultdict(list)
    for (s, w), evs in groups.items():
        W = at(w, s)
        refs = []
        for kind, ov in evs:
            c = ref_times(kind, s, w, ov)
            r = c.get(cross_ref if kind == "cross" else ot_ref) or c.get("other arrives/passes") or c.get("overtaker passes station")
            if r is None: continue
            refs.append(r + (g_cross if kind == "cross" else g_ot))
        if not refs: continue
        # references can lie on the other side of midnight: compare relative to the waiter's arrival
        rel = [wrap(r - W[3]) for r in refs]
        pred = W[3] + max(DWELL_MIN[ttype(w)], max(rel))
        err = wrap(pred - W[4]) / 60
        k = "+".join(sorted({kind for kind, _ in evs}))
        res[(k, ttype(w))].append(err)
        res[("ALL", "")].append(err)
    print(f"\n{label}")
    for key in sorted(res, key=lambda k: (k[0] != "ALL", k)):
        e = np.array(res[key])
        if len(e) < 10: continue
        print(f"   {key[0]:15s} {key[1]:9s} n={len(e):4d}  exact minute {np.mean(np.abs(e) < .5):4.0%}  within 1 {np.mean(np.abs(e) <= 1):4.0%}  "
              f"model early (waits too short) {np.mean(e < -.5):4.0%}  late {np.mean(e > .5):4.0%}")
evaluate(120, 120, "other arrives/passes", "overtaker at next station", "A: crossing +2 after other arrives/passes; overtaking +2 after overtaker reaches the next station")
evaluate(120, 120, "other departs", "overtaker at next station", "B: as A, but when both stop at a crossing use the other's departure")
evaluate(120, 120, "other arrives/passes", "overtaker passes station", "C: as A, but overtaking +2 after the overtaker passes the station itself")
