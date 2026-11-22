"""Just-in-time release (user rule): when the next station Y is full, a train is not held until a train leaves Y;
it is released so that it arrives at Y when a track there becomes free.

For every stop of a train at Y: was Y full IN THE TRAIN'S DIRECTION when the train left its previous timed station X
(trains of that direction standing at Y >= tracks usable in that direction, infra.db station_tracks.usable)? If so:
  release = departure from X - time the first standing train left Y (negative: released before Y had room = JIT)
  arrival = arrival at Y - that time (>= 0 expected; small = just in time)
Standing trains: stops with a dwell (arrival and departure printed), KA Dinas excluded (their times are estimates)."""
import sqlite3
from collections import defaultdict

import numpy as np

R = "/home/user/Gapeka-parser"
g, i = sqlite3.connect(f"{R}/gapeka.db"), sqlite3.connect(f"{R}/infra.db")
typ = dict(g.execute("select no, type from trains"))
cap = defaultdict(int)
for st, n in i.execute("select station, max(roman_tracks) from station_layouts group by station"):
    cap[st] = n
stand = defaultdict(list)                       # station -> [(arr, dep, train)] with both days
rows = defaultdict(list)
for no, seq, st, kind, a, d in g.execute("select train_no, seq, station, kind, arr_s, dep_s from stops order by train_no, seq"):
    if typ.get(no) == "dinas":
        continue
    rows[no].append((st, kind, a, d))
    if kind in ("stop", "halt") and a is not None and d is not None:
        d2 = d + 86400 if d < a else d
        if d2 > a:
            for k in (0, 86400):
                stand[st].append((a + k, d2 + k, no))

def standing(st, t, me):
    return [x for x in stand[st] if x[0] <= t < x[1] and x[2] != me]

# direction (L / R on the sheet) of a train at a station, from the diagram's end labels (as layout_directions.py)
line_of_sec = {}
for a, b, ln in i.execute("select a, b, line_id from sections"):
    line_of_sec[(a, b)] = line_of_sec[(b, a)] = ln
lay = {}
for st, ln, le, re_ in i.execute("select station, line_id, left_ends, right_ends from station_layouts"):
    lay.setdefault((st, ln), (set(le.split()), set(re_.split())))
use = defaultdict(lambda: {"L": 0, "R": 0})
for st, ln, us in i.execute("select station, line_id, usable from station_tracks where kind = 'roman' and out_of_service = 0"):
    for c in (us or ""):
        use[(st, ln)][c] += 1
route = {no: [x for x, *_ in R_] for no, R_ in rows.items()}
def direction(no, st):
    r = route[no]
    k = r.index(st)
    for ln in {line_of_sec.get((r[k - 1], st)) if k else None, line_of_sec.get((st, r[k + 1])) if k + 1 < len(r) else None}:
        if (st, ln) not in lay:
            continue
        left, right = lay[(st, ln)]
        after, before = set(r[k + 1:k + 6]), set(r[max(0, k - 5):k])
        if after & right or before & left:
            return "R", ln
        if after & left or before & right:
            return "L", ln
    return None, None
dir_cache = {}
def dir_of(no, st):
    if (no, st) not in dir_cache:
        dir_cache[(no, st)] = direction(no, st)
    return dir_cache[(no, st)]

rel, arr_lead, held, cases = [], [], [], []
for no, R_ in rows.items():
    timed = [(st, k, a, d) for st, k, a, d in R_ if a is not None or d is not None]
    for (x, kx, ax, dx), (y, ky, ay, dy) in zip(timed, timed[1:]):
        if ky not in ("stop",) or ay is None or dx is None or not cap.get(y):
            continue
        tdep = dx if dx >= 0 else dx
        tarr = ay if ay >= tdep else ay + 86400
        d, ln = dir_of(no, y)
        if d is None or not use[(y, ln)][d]:
            continue
        occ = [o for o in standing(y, tdep, no) if dir_of(o[2], y)[0] == d]
        if len(occ) < use[(y, ln)][d]:
            continue
        # the first moment a track frees at Y after X's departure: the earliest departure of the standing trains
        free = min(o[1] for o in occ)
        rel.append((tdep - free) / 60)
        arr_lead.append((tarr - free) / 60)
        cases.append((no, x, y, d, use[(y, ln)][d], len(occ), round((tdep - free) / 60, 1), round((tarr - free) / 60, 1)))

rel, arr_lead = np.array(rel), np.array(arr_lead)
print(f"stops where the station ahead was full (its direction) when the train left the previous station: {len(rel)}")
if len(rel):
    print(f"  released BEFORE a track at Y was free (just in time): {np.mean(rel < 0):.0%}; "
          f"held until it was free or later: {np.mean(rel >= 0):.0%}")
    print(f"  arrival at Y minus the moment a track freed: median {np.median(arr_lead):+.1f} min, "
          f"p10 {np.percentile(arr_lead, 10):+.1f}, p90 {np.percentile(arr_lead, 90):+.1f}; "
          f"arrived before it freed: {np.mean(arr_lead < 0):.0%}")
    h = np.histogram(arr_lead, bins=[-99, 0, 1, 2, 3, 4, 5, 10, 999])[0]
    print("  arrival lead histogram (<0, 0-1, 1-2, 2-3, 3-4, 4-5, 5-10, >10 min):", list(h))
    print("  examples (train, from, to, direction, tracks for it, standing same way, release, arrival):")
    for c in sorted(cases, key=lambda c: c[7])[:: max(1, len(cases) // 20)]:
        print("    ", c)

from collections import Counter
typ_of = lambda c: typ.get(c[0])
print("  release (departure from X - track free at Y), min:", sorted(Counter(np.round(rel).astype(int)).items()))
for t in ("krl", "commuter", "intercity", "freight"):
    sub = [c for c in cases if typ_of(c) == t]
    if sub:
        r_ = np.array([c[6] for c in sub]); a_ = np.array([c[7] for c in sub])
        print(f"  {t:10s} n {len(sub):4d}  release median {np.median(r_):+.1f} (range {r_.min():+.0f}..{r_.max():+.0f})  "
              f"arrival after free median {np.median(a_):+.1f} (p90 {np.percentile(a_, 90):+.1f})")
for c in [c for c in cases if typ_of(c) != "krl"][:12]:
    print("    ", c, typ_of(c))

# --- user: the 1 min margin protects the follower's path BEYOND Y (not blocked by the train ahead at the station after
#     Y); it should not apply when the follower ends at Y or leaves Y on another route, and grow with long blocks after Y
nxt = {no: [x for x, *_ in R_] for no, R_ in rows.items()}
km = {}
for a, b, k in i.execute("select a, b, km from sections"):
    km[(a, b)] = km[(b, a)] = k
nblk = defaultdict(int)
for a, b in i.execute("select a, b from block_signals"):
    nblk[(a, b)] += 1
    nblk[(b, a)] += 1
groups = defaultdict(list)
rows2 = []
for c in cases:
    no, x, y = c[0], c[1], c[2]
    occ_lead = [o for o in standing(y, [d for st, k, a, d in rows[no] if st == x][0], no) if dir_of(o[2], y)[0] == c[3]]
    lead = min(occ_lead, key=lambda o: o[1])[2] if occ_lead else None
    r_f, r_l = nxt[no], nxt.get(lead, [])
    after_f = r_f[r_f.index(y) + 1] if y in r_f and r_f.index(y) + 1 < len(r_f) else None
    after_l = r_l[r_l.index(y) + 1] if y in r_l and r_l.index(y) + 1 < len(r_l) else None
    kind = "follower ends at Y" if after_f is None else ("same route after Y" if after_f == after_l else "other route after Y")
    groups[kind].append(c[6])
    if kind == "same route after Y":
        L = km.get((y, after_f))
        rows2.append((c[6], L, nblk.get((y, after_f), 0)))
print("\n  release offset by what the follower does after Y:")
for k, v in groups.items():
    v = np.array(v)
    print(f"    {k:22s} n {len(v):4d}  release median {np.median(v):+.1f}  values {sorted(Counter(np.round(v).astype(int)).items())}")
if rows2:
    r2 = np.array([(a, b if b is not None else np.nan, c) for a, b, c in rows2], float)
    for lo, hi in ((0, 2), (2, 3), (3, 5), (5, 99)):
        m = (r2[:, 1] >= lo) & (r2[:, 1] < hi)
        if m.any():
            print(f"    same route, next section {lo}-{hi} km: n {m.sum():3d}, release median {np.median(r2[m, 0]):+.1f}, "
                  f"share released 2 min early {np.mean(r2[m, 0] <= -1.5):.0%}")
