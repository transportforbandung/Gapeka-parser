"""Overtaking: the waiting train may be passed by several trains (a platoon); use ALL trains that pass the station in
the same direction while it stands there, not only the one named in the note."""
from collections import defaultdict
import numpy as np
exec(open("waits3.py").read().split("tab = defaultdict")[0])
# index: station -> list of (train, seq index in its own route, time at station, is_pass)
by_station = defaultdict(list)
for no, rr in rows.items():
    for i, r in enumerate(rr):
        t = t_at(r)
        if t is not None:
            by_station[r[1]].append((no, i, r))
def same_direction_followers(s, w):
    """trains passing station s in w's direction while w stands there, with their time at s and at w's next station"""
    W = at(w, s)
    iw = next(i for i, r in enumerate(rows[w]) if r[0] == W[0])
    nxt = [r[1] for r in rows[w][iw + 1:iw + 3]]           # next one or two stations of the waiting train
    out = []
    for no, i, r in by_station[s]:
        if no == w: continue
        ts = r[4] if r[4] is not None else r[3]            # departure / pass time at s
        if ts is None: continue
        if not (0 <= wrap(ts - W[3]) <= wrap(W[4] - W[3]) + 60): continue
        after = [q[1] for q in rows[no][i + 1:i + 4]]
        if not nxt or nxt[0] not in after: continue         # not going the same way
        q = at(no, nxt[0])
        tn = t_at(q)
        out.append((no, ts, tn))
    return out, nxt[0] if nxt else None

res = defaultdict(lambda: defaultdict(list))
for (s, w), evs in groups.items():
    if not any(k == "overtake" for k, _ in evs): continue
    W = at(w, s)
    fol, n = same_direction_followers(s, w)
    if not fol: continue
    ln = line(s)
    last_s = max(fol, key=lambda f: wrap(f[1] - W[3]))
    gap_station = wrap(W[4] - last_s[1]) / 60                      # after the last follower passes the station
    tns = [f for f in fol if f[2] is not None]
    gap_next = wrap(W[4] - max(tns, key=lambda f: wrap(f[2] - W[3]))[2]) / 60 if tns else None
    noted_only = [ov for k, ov in evs if k == "overtake"]
    res[ln]["n_followers"].append(len(fol))
    res[ln]["extra_unnoted"].append(len({f[0] for f in fol} - set(noted_only)))
    res[ln]["last_at_station"].append(gap_station)
    if gap_next is not None: res[ln]["last_at_next"].append(gap_next)
def cell(v):
    if len(v) < 4: return f"{'-':>27s}"
    v = np.array(v); md = np.median(v)
    return f"med {md:+5.1f} ±0.5 {np.mean(np.abs(v - md) <= .5):4.0%} ±1 {np.mean(np.abs(v - md) <= 1):4.0%}"
print(f"{'line':45s} {'n':>4s} {'trains passing':>15s} | {'dep - LAST passer at station':>33s} | {'dep - LAST passer at next station':>33s}")
for ln in sorted(res, key=lambda l: -len(res[l]['last_at_station'])):
    t = res[ln]
    print(f"{ln:45s} {len(t['last_at_station']):4d} {np.mean(t['n_followers']):5.1f} ({np.mean(t['extra_unnoted']):3.1f} not noted) | "
          f"{cell(t['last_at_station']):>33s} | {cell(t['last_at_next']):>33s}")
