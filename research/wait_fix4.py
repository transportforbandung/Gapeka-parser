"""Waiting-train departure with all events combined (max), passenger only.
Overtake gap variants: fixed per track; per line+track at block point; per line+track at the station itself; max of both.
Per-line gaps: median over OTHER waiting trains (leave-one-train-out) on the same line and track, using only
the binding (last) overtaker per waiting train; fallback to the track median when < 3 others."""
import json
from collections import defaultdict
import numpy as np
src = open("waits11.py").read()
src = src.replace('res[("double" if dbl else "single", why)].append((wrap(W[4] - ref) / 60, wrap(W[4] - old) / 60 if old else None, line(s)))',
                  'res[("double" if dbl else "single", why)].append((wrap(W[4] - ref) / 60, wrap(W[4] - old) / 60 if old else None, line(s), s, w, ov, ref))')
exec(src.split('print("overtaking, waiting train departure')[0])
grp = lambda no: {"krl": "KRL", "freight": "freight"}.get(ttype(no), "passenger")
OT = {}
for k, xs in res.items():
    for gap, _, ln, s, w, ov, ref in xs:
        OT[(s, w, ov)] = (k[0], k[1], ref, ln)
# binding overtaker per waiting train -> samples for per-line gaps
samp_blk, samp_st = defaultdict(list), defaultdict(list)
for (s, w), evs in groups.items():
    if grp(w) != "passenger": continue
    W = at(w, s)
    ots = [(OT[(s, w, ov)], ov) for k, ov in evs if k == "overtake" and (s, w, ov) in OT]
    if not ots or any(k == "cross" for k, _ in evs): continue
    (trk, why, ref, ln), ov = max(ots, key=lambda x: wrap(x[0][2] - W[3]))
    O = at(ov, s); tS = O[4] if O[4] is not None else O[3]
    samp_blk[(ln, trk)].append((w, wrap(W[4] - ref) / 60))
    samp_st[(ln, trk)].append((w, wrap(W[4] - tS) / 60))
def loo(samp, key, w, trk, fallback):
    v = [g for x, g in samp.get(key, []) if x != w]
    return np.median(v) if len(v) >= 3 else fallback
FB_BLK = {"single": 2.0, "double": 2.5}
FB_ST = {t: np.median([g for (l, tt), xs in samp_st.items() if tt == t for _, g in xs]) for t in ("single", "double")}
print("fallback gap at station:", FB_ST)
def predict(variant, s, w, evs):
    W = at(w, s); preds = []
    for kind, ov in evs:
        O = at(ov, s)
        if kind == "cross" and O is not None and t_at(O) is not None:
            if O[3] is not None and O[4] is not None:
                preds.append(O[4] + 120 if wrap(O[4] - O[3]) <= 120 else O[3] + 120)
            else:
                preds.append(t_at(O) + 120)
        elif kind == "overtake" and (s, w, ov) in OT:
            trk, why, ref, ln = OT[(s, w, ov)]
            tS = O[4] if O[4] is not None else O[3]
            gb = loo(samp_blk, (ln, trk), w, trk, FB_BLK[trk])
            gs = loo(samp_st, (ln, trk), w, trk, FB_ST[trk])
            if variant == "fixed": p = ref + 60 * FB_BLK[trk]
            elif variant == "line@block": p = ref + 60 * gb
            elif variant == "line@station": p = tS + 60 * gs
            else: p = max(ref + 60 * FB_BLK[trk], tS + 60 * gs, key=lambda t: wrap(t - W[3]))
            preds.append(p)
    if not preds: return None
    p = max(preds, key=lambda t: wrap(t - W[3]))
    return max(p, W[3] + 60, key=lambda t: wrap(t - W[3]))
for variant in ("fixed", "line@block", "line@station", "max(block+fixed, line@station)"):
    E = defaultdict(list)
    for (s, w), evs in groups.items():
        if grp(w) != "passenger": continue
        p = predict(variant, s, w, evs)
        if p is None: continue
        W = at(w, s)
        kinds = {k for k, _ in evs}
        ot_trk = {OT[(s, w, ov)][0] for k, ov in evs if k == "overtake" and (s, w, ov) in OT}
        key = "crossing only" if kinds == {"cross"} else ("overtake only " + "/".join(sorted(ot_trk)) if kinds == {"overtake"} else "crossing + overtake")
        e = wrap(p - W[4]) / 60
        E[key].append(e); E["ALL"].append(e)
    print(f"\novertake variant: {variant}")
    for k in sorted(E):
        e = np.array(E[k])
        print(f"   {k:26s} n={len(e):4d}  exact {np.mean(np.abs(e) <= .5):4.0%}  ±1 {np.mean(np.abs(e) <= 1):4.0%}  GAPEKA later {np.mean(e < -.5):4.0%}  GAPEKA earlier {np.mean(e > .5):4.0%}")
