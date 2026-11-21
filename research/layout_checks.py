"""Station track diagrams (infra.db station_layouts / station_tracks) against the timetable.

1. capacity: the most trains standing at a station at the same time (GAPEKA) vs its Roman (main + siding) tracks
2. fit: trains that wait for a crossing / overtake vs the station's longest Roman track (consists: cars x 21 m + 16 m
   locomotive; gapeka.db consists, or research/stam.json)
3. timetable stations (not halts) without a diagram (stations with a 'periksa lintas' note are drawn elsewhere)
4. waits by trains that do not fit: user rules (crossing: the long train departs first, the other stops and fits;
   double / single track transition: a train waiting on its own running track from the double-track side does not
   need to fit, its rear stands on the double track)
A station can have different diagrams on different lines; these checks use the one with the most Roman tracks.
"""
import json
import os
import sqlite3
from collections import Counter, defaultdict

import numpy as np

R = os.environ.get("REPO", "/home/user/Gapeka-parser")
g, i = sqlite3.connect(f"{R}/gapeka.db"), sqlite3.connect(f"{R}/infra.db")
typ = dict(g.execute("select no, type from trains"))
roman = defaultdict(list)
best = {}
for st, pg, n in i.execute("select station, page, roman_tracks from station_layouts"):
    if st not in best or n > best[st][1]:
        best[st] = (pg, n)
for st, pg, tr, ln in i.execute("select station, page, track, length_m from station_tracks where kind = 'roman'"):
    if best.get(st, (None,))[0] == pg:
        roman[st].append((tr, ln))

# 1. capacity
iv = defaultdict(list)
for no, st, a, d in g.execute("select train_no, station, arr_s, dep_s from stops where kind in ('stop', 'halt')"):
    if typ.get(no) != "dinas" and a is not None and d is not None:
        d = d + 86400 if d < a else d
        if d > a:
            iv[st].append((a, d, no))
res = []
for st, L in iv.items():
    if st not in roman:
        continue
    ev = sorted([(a + k, 1, no) for a, d, no in L for k in (0, 86400)] + [(d + k, -1, no) for a, d, no in L for k in (0, 86400)],
                key=lambda e: (e[0], e[1]))
    cur = peak = 0
    at = None
    for t, e, no in ev:
        cur += e
        if cur > peak:
            peak, at = cur, t
    res.append((st, peak, len(roman[st]), at % 86400))
over = [r for r in res if r[1] > r[2]]
print(f"1. capacity: {len(res)} stations; peak standing trains > Roman tracks at {len(over)}; "
      f"= Roman tracks at {sum(r[1] == r[2] for r in res)}")
for st, p, n, at in over:
    print(f"     {st}: peak {p} at {at // 3600:02d}:{at % 3600 // 60:02d}, Roman tracks {roman[st]}")

# 2. fit
try:
    cons = {n: c for n, c in g.execute("select train_no, cars from consists")}
except sqlite3.OperationalError:
    cons = {n: v["cars"] for n, v in json.load(open(f"{R}/research/stam.json")).items()}
m = defaultdict(list)
bad = []
for no, st, kind, note in g.execute("select train_no, station, kind, note from stops where kind = 'stop'"):
    if no in cons and roman.get(st):
        L = cons[no] * 21 + 16
        longest = max(x[1] for x in roman[st])
        k = "waits (note)" if note and "ka " in note else "other stops"
        m[k].append(longest - L)
        if k == "waits (note)" and longest < L:
            bad.append((st, no, cons[no], L, longest))
for k, v in m.items():
    v = np.array(v)
    print(f"2. fit, {k:12s}: n {len(v)}, train longer than the longest Roman track {np.mean(v < 0):.1%}, "
          f"margin p5 {np.percentile(v, 5):.0f} m")
dbl = {}
for a, b_, d in i.execute("select a, b, double_track from sections"):
    dbl[(a, b_)] = dbl[(b_, a)] = d
prev_st = {}
for no, s_, st in g.execute("select train_no, seq, station from stops where kind != 'halt' order by train_no, seq"):
    prev_st.setdefault(no, []).append(st)
def transition_ok(no, st):
    """the train comes from the double-track side of a double / single transition station"""
    path = prev_st.get(no, [])
    if st not in path or path.index(st) == 0:
        return False
    p = path[path.index(st) - 1]
    sides = {dbl.get((st, x)) for x in set(n for (a, n) in dbl if a == st)}
    return dbl.get((p, st)) == 1 and 0 in sides
for b in sorted(set((b[0], b[1], b[2], b[3], b[4]) for b in bad)):
    print("     ", b, "OK: waits on its own track at a double/single transition" if transition_ok(b[1], b[0]) else "")

# 3. stations without a diagram
kind = dict(i.execute("select code, kind from stations"))
names = dict(i.execute("select code, name from stations"))
c = Counter(st for (st,) in g.execute("select station from stops where kind in ('stop', 'pass', 'origin_or_end')"))
refs = {s_ for (s_,) in i.execute("select station from station_layout_refs")}
miss = [(s, names.get(s), n) for s, n in c.most_common() if s not in roman and s not in refs and kind.get(s) == "station"]
print(f"3. timetable stations (not halts) without a diagram: {len(miss)}")
print("    ", miss)

# 4. waiting trains that do not fit (user rule): crossing -> the long train departs first and the other train stops
#    and fits (or the two use non-adjacent tracks); overtake -> every train must fit
import re
same_dir = {}
seq = defaultdict(dict)
for no, s, st in g.execute("select train_no, seq, station from stops"):
    seq[no][st] = s
def direction_same(a, b, st):
    """both trains run the same way through st (compare the next common station)"""
    sa, sb = seq[a], seq[b]
    common = [x for x in sa if x in sb and x != st]
    if not common:
        return None
    x = common[0]
    return (sa[x] > sa[st]) == (sb[x] > sb[st])
print("4. waits by trains longer than the longest Roman track:")
for no, st, a, d, note in g.execute("select train_no, station, arr_s, dep_s, note from stops where kind = 'stop' and note like '%ka %'"):
    if no not in cons or not roman.get(st):
        continue
    L, longest = cons[no] * 21 + 16, max(x[1] for x in roman[st])
    if L <= longest:
        continue
    for other in re.findall(r"ka (\d{1,4}F?)", note):
        o = g.execute("select kind, arr_s, dep_s from stops where train_no = ? and station = ?", (other, st)).fetchone()
        if not o:
            continue
        same = direction_same(no, other, st)
        Lo = cons[other] * 21 + 16 if other in cons else None
        od = o[2]
        print(f"     {st} {no} ({L} m > {longest:g} m) {'overtaken by' if same else 'crossed by'} {other} "
              f"({o[0]}, {Lo or '?'} m): long train departs {d // 3600:02d}:{d % 3600 // 60:02d}, "
              f"other {'passes' if o[0] == 'pass' else 'departs'} {od // 3600:02d}:{od % 3600 // 60:02d}")
