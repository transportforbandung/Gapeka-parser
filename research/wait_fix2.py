"""Unified departure rule for a waiting train: dep = max over its events of the event rule, and own arrival + 1 min.
crossing: other passes -> other pass + 2;  both stop -> other dep + 2 if other dwell <= D else other arr + 2.
overtake: overtaker at next block point + g(track, block kind).  Passenger only (freight on hold, KRL excluded).
Scored on all passenger waiting trains (gaps are whole fixed minutes, only D is chosen; listed per D)."""
import json
from collections import defaultdict
import numpy as np
src = open("waits11.py").read()
src = src.replace('res[("double" if dbl else "single", why)].append((wrap(W[4] - ref) / 60, wrap(W[4] - old) / 60 if old else None, line(s)))',
                  'res[("double" if dbl else "single", why)].append((wrap(W[4] - ref) / 60, wrap(W[4] - old) / 60 if old else None, line(s), s, w, ov, ref))')
exec(src.split('print("overtaking, waiting train departure')[0])
grp = lambda no: {"krl": "KRL", "freight": "freight"}.get(ttype(no), "passenger")
OT = {}                                         # (s, w, ov) -> (track, kind, ref time)
for k, xs in res.items():
    for gap, _, ln, s, w, ov, ref in xs:
        OT[(s, w, ov)] = (k[0], k[1], ref)
G_OT = {("single", "station"): 2, ("double", "block signal"): 2.5, ("double", "station"): 2, ("double", "halt"): 2}
for D in (1, 2, 3, 4, 99):
    E = defaultdict(list)
    for (s, w), evs in groups.items():
        if grp(w) != "passenger": continue
        W = at(w, s)
        preds, kinds = [], []
        for kind, ov in evs:
            O = at(ov, s)
            if kind == "cross" and O is not None and t_at(O) is not None:
                if O[3] is not None and O[4] is not None:
                    dw = wrap(O[4] - O[3]) / 60
                    preds.append(O[4] + 120 if dw <= D else O[3] + 120); kinds.append("cross both stop")
                else:
                    preds.append(t_at(O) + 120); kinds.append("cross other passes")
            elif kind == "overtake" and (s, w, ov) in OT:
                trk, why, ref = OT[(s, w, ov)]
                preds.append(ref + 60 * G_OT.get((trk, why), 2)); kinds.append(f"overtake {trk}")
        if not preds: continue
        p = max(preds, key=lambda t: wrap(t - W[3]))
        p = max(p, W[3] + 60, key=lambda t: wrap(t - W[3]))
        key = kinds[0] if len(set(kinds)) == 1 and len(kinds) == 1 else "several events"
        E[key].append(wrap(p - W[4]) / 60)
        E["ALL"].append(wrap(p - W[4]) / 60)
    print(f"\nD = {D} min (other dwell threshold for 'both stop')")
    for k in sorted(E):
        e = np.array(E[k])
        print(f"   {k:22s} n={len(e):4d}  exact {np.mean(np.abs(e) <= .5):4.0%}  ±1 {np.mean(np.abs(e) <= 1):4.0%}  GAPEKA later {np.mean(e < -.5):4.0%}  GAPEKA earlier {np.mean(e > .5):4.0%}")
