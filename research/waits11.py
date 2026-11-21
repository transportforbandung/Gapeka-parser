"""Block-point rule for overtaking (user):
 double track (automatic block): waiting train may leave when the overtaker has passed the NEXT BLOCK POINT =
   the first block signal on that track (incl. signals at halts), else the next real station; halts are not block
   points by themselves (user);
 single track (semaphores): next block point = next real station (halts have no signals).
Waiting train departure = overtaker at the block point + g (dispatcher / route setting)."""
import json
from collections import defaultdict
import numpy as np
exec(open("waits9.py").read().split("res = defaultdict")[0])
P = json.load(open("line_profile.json"))
HALTS = {r[0] for r in db.execute("select distinct station from stops where kind='halt'")}
STOPPED = {r[0] for r in db.execute("select distinct station from stops where kind='stop'")}
def double_track(a, b):
    p = P.get(f"{a}-{b}") or P.get(f"{b}-{a}")
    if p is None: return None
    tags = [x["tag"] for x in p.get("blocks", [])] + [t for r in p["restr"] for t in r["tags"].split()]
    return any(t.startswith(("hu", "hi")) for t in tags)
def km(a, b):
    p = P.get(f"{a}-{b}") or P.get(f"{b}-{a}")
    return p["km"] if p else None
def time_at(ov, st, prev_t, prev_st):
    """overtaker's time at station st; interpolate by distance when it has no printed time (e.g. it stops at a halt)"""
    q = at(ov, st)
    if q is not None and t_at(q) is not None:
        return t_at(q)
    return None
res = defaultdict(list)
for (s, w), evs in groups.items():
    W = at(w, s)
    iw = next(i for i, r in enumerate(rows[w]) if r[0] == W[0])
    for kind, ov in evs:
        if kind != "overtake": continue
        O = at(ov, s)
        tS = O[4] if O[4] is not None else O[3]
        if tS is None or iw + 1 >= len(rows[w]): continue
        route = [r[1] for r in rows[w][iw + 1:iw + 8]]
        dbl = double_track(s, route[0])
        if dbl is None: continue
        # walk forward along the waiting train's route to the first block point
        cum, prev, ref, why = 0.0, s, None, None
        for st in route:
            L = km(prev, st)
            if L is None: break
            if dbl:
                offs, Lp = blocks_from(prev, st)
                if offs:                                         # an automatic block signal before the next station/halt
                    d = cum + offs[0]
                    # overtaker time at that point: interpolate between its times at prev and st
                    t0 = tS if prev == s else time_at(ov, prev, None, None)
                    t1 = time_at(ov, st, None, None)
                    if t0 is not None and t1 is not None:
                        ref, why = t0 + wrap(t1 - t0) * offs[0] / L, "block signal"
                    break
            cum += L
            is_block_point = (st in STOPPED or st not in HALTS)   # halts only count through their signals (user)
            if is_block_point:
                t1 = time_at(ov, st, None, None)
                if t1 is not None:
                    ref, why = t1, ("halt" if st in HALTS and st not in STOPPED else "station")
                break
            prev = st
        if ref is None: continue
        # old rule for comparison: next timed station of the waiting train where the overtaker has a time
        old = None
        for r in rows[w][iw + 1:]:
            q = at(ov, r[1])
            if q is not None and t_at(q) is not None:
                old = t_at(q); break
        res[("double" if dbl else "single", why)].append((wrap(W[4] - ref) / 60, wrap(W[4] - old) / 60 if old else None, line(s)))
print("overtaking, waiting train departure - overtaker at the reference point (minutes)")
for key in sorted(res):
    v = np.array([x[0] for x in res[key]]); md = np.median(v)
    o = np.array([x[1] for x in res[key] if x[1] is not None]); mo = np.median(o)
    print(f"  {key[0]:6s} track, next block point = {key[1]:12s} n={len(v):3d}   block rule: gap {md:+4.1f} min, "
          f"±0.5 {np.mean(np.abs(v - md) <= .5):4.0%}, ±1 {np.mean(np.abs(v - md) <= 1):4.0%}   |  next-station rule: gap {mo:+4.1f}, "
          f"±0.5 {np.mean(np.abs(o - mo) <= .5):4.0%}, ±1 {np.mean(np.abs(o - mo) <= 1):4.0%}")
for trk in ("double", "single"):
    v = np.array([x[0] for k, xs in res.items() if k[0] == trk for x in xs])
    o = np.array([x[1] for k, xs in res.items() if k[0] == trk for x in xs if x[1] is not None])
    if len(v):
        md, mo = np.median(v), np.median(o)
        print(f"  {trk:6s} track ALL            n={len(v):3d}   block rule: gap {md:+4.1f} min, ±0.5 {np.mean(np.abs(v - md) <= .5):4.0%}, ±1 {np.mean(np.abs(v - md) <= 1):4.0%}"
              f"   |  next-station rule: ±0.5 {np.mean(np.abs(o - mo) <= .5):4.0%}, ±1 {np.mean(np.abs(o - mo) <= 1):4.0%}")
print("\nIs the block rule a lower bound? share of GAPEKA departures EARLIER than overtaker-at-block-point + g")
for trk in ("double", "single"):
    v = np.array([x[0] for k, xs in res.items() if k[0] == trk for x in xs])
    for g in (0, 0.5, 1, 2):
        print(f"  {trk:6s} g = {g:3.1f} min: earlier {np.mean(v < g - 0.25):4.0%}   ", end="")
    print(f"  | gap percentiles 10/25/50/75/90: {np.percentile(v, [10, 25, 50, 75, 90]).round(1)}")
