"""Departure errors of waiting trains with the current rules, by case; and tests of ideas 1, 2, 5."""
import json
from collections import defaultdict
import numpy as np
src = open("waits11.py").read()
src = src.replace('res[("double" if dbl else "single", why)].append((wrap(W[4] - ref) / 60, wrap(W[4] - old) / 60 if old else None, line(s)))',
                  'res[("double" if dbl else "single", why)].append((wrap(W[4] - ref) / 60, wrap(W[4] - old) / 60 if old else None, line(s), s, w, ov, ref))')
exec(src.split('print("overtaking, waiting train departure')[0])
CTC = set(json.load(open("ctc_stations.json")))
def ttype_(no): return ttype(no)
out = defaultdict(list)
# --- crossings: reference = other train arrives/passes; rule +2 min; take the LAST of all trains it waits for (idea 1)
for (s, w), evs in groups.items():
    W = at(w, s)
    crosses = [ov for k, ov in evs if k == "cross"]
    if not crosses: continue
    refs = []
    for ov in crosses:
        O = at(ov, s)
        r = t_at(O)
        if r is None: continue
        refs.append((wrap(r - W[3]), ov, O[3] is not None and O[4] is not None))
    if not refs: continue
    last = max(refs)
    first = min(refs)
    pred_last = W[3] + last[0] + 120
    pred_first = W[3] + first[0] + 120
    both_stop = last[2]
    case = ("KRL" if ttype_(w) == "krl" else ("freight" if ttype_(w) == "freight" else "passenger"))
    out[("cross", case, "both stop" if both_stop else "other passes", "n_opponents=%d" % len(refs) if len(refs) > 1 else "1 opponent")].append(
        (wrap(W[4] - pred_last) / 60, wrap(W[4] - pred_first) / 60))
# --- overtakes: block rule + typical gap (single +2, double +3.5)
for k, xs in res.items():
    for gap, _, ln, s, w, ov, ref in xs:
        case = ("KRL" if ttype_(w) == "krl" else ("freight" if ttype_(w) == "freight" else "passenger"))
        g = 2.0 if k[0] == "single" else 3.5
        out[("overtake", case, k[0] + " track", "CTC" if s in CTC else "not CTC")].append((gap - g, None))
print(f"{'case':70s} {'n':>4s}  {'exact (±0.5)':>12s} {'±1':>5s}  {'model early':>11s} {'model late':>10s}")
for key in sorted(out):
    e = np.array([x[0] for x in out[key]])
    print(f"{' / '.join(key):70s} {len(e):4d}  {np.mean(np.abs(e) <= .5):12.0%} {np.mean(np.abs(e) <= 1):5.0%}  "
          f"{np.mean(e > .5):11.0%} {np.mean(e < -.5):10.0%}")
# idea 1: did using the last opponent matter?
m = [x for k, v in out.items() if k[0] == "cross" and k[3] != "1 opponent" for x in v]
if m:
    a = np.array(m)
    print(f"\nidea 1, waits for several opposing trains (n={len(a)}): exact with LAST opponent {np.mean(np.abs(a[:, 0]) <= .5):.0%}, with FIRST {np.mean(np.abs(a[:, 1]) <= .5):.0%}")
