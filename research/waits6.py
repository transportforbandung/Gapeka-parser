import numpy as np
from collections import defaultdict
exec(open("waits5.py").read().split("def corr(")[0])
rec = []
for (s, w), evs in groups.items():
    W = at(w, s)
    for kind, ov in evs:
        if kind != "cross": continue
        ref = ref_times(kind, s, w, ov).get("other arrives/passes")
        if ref is None: continue
        for i in leg_by.get((w, s, "end"), []):
            g = legs[i]
            dep0 = W[3] - y[i]                                   # departure from the previous timed point
            phys_arr = dep0 + pred[i]
            rec.append((wrap(ref - phys_arr) / 60, wrap(ref - W[3]) / 60, y[i] / 60, pred[i] / 60, g["type"], g["start_rest"], s, w))
a = np.array([(r[0], r[1]) for r in rec])
print(f"crossings with the leg into the waiting station: {len(rec)}")
print("model margin = other train arrives - model arrival;  GAPEKA margin = other train arrives - GAPEKA arrival")
for lo, hi in ((0, 2), (2, 4), (4, 6), (6, 8), (8, 12), (12, 20), (20, 999)):
    sel = (a[:, 0] >= lo) & (a[:, 0] < hi)
    if sel.sum() >= 5:
        gm = a[sel, 1]
        print(f"  model margin {lo:>3}-{hi:<3} min: n={sel.sum():4d}  GAPEKA margin median {np.median(gm):5.1f}  "
              f"IQR {np.percentile(gm, 25):4.1f}..{np.percentile(gm, 75):4.1f}   GAPEKA later than model by median {np.median(a[sel, 0] - gm):+4.1f} min")
# candidate rule: arrival = max(model arrival, other arrives - K): GAPEKA arrives at least K min before but not earlier than needed?
for K in (3, 4, 5, 6, 8, 10):
    e = []
    for (mm, gm, yy, pp, *_ ) in rec:
        model_arr_margin = mm
        new_margin = min(model_arr_margin, K) if model_arr_margin > K else model_arr_margin
        e.append(new_margin - gm)
    e = np.array(e)
    print(f"  rule 'arrive no earlier than {K:2d} min before the other train': exact {np.mean(np.abs(e) < .5):4.0%}  >1 {np.mean(np.abs(e) > 1):4.0%}")
e = a[:, 0] - a[:, 1]
print(f"  current model (no rule)                              : exact {np.mean(np.abs(e) < .5):4.0%}  >1 {np.mean(np.abs(e) > 1):4.0%}")
