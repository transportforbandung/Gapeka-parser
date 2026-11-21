"""Direction use of station tracks (infra.db station_tracks.usable) against GAPEKA overtakes and crossings.

Overtake: the waiting train stands on a track usable in its direction while the overtaker runs through on the main
of that direction, so the station needs a usable track for that direction other than that main.
Crossing (single track): both trains stand or pass on different tracks usable in their own directions.
Direction of a train at a station: L / R on the sheet, from the diagram's end labels (left_ends / right_ends) and
the train's previous / next stations (halts skipped)."""
import re
import sqlite3
from collections import Counter, defaultdict

R = "/home/user/Gapeka-parser"
g, i = sqlite3.connect(f"{R}/gapeka.db"), sqlite3.connect(f"{R}/infra.db")
line_of_sec = {}
for a, b, ln in i.execute("select a, b, line_id from sections"):
    line_of_sec[(a, b)] = line_of_sec[(b, a)] = ln
lay = {}
for st, ln, le, re_ in i.execute("select station, line_id, left_ends, right_ends from station_layouts"):
    lay.setdefault((st, ln), (set(le.split()), set(re_.split())))
tracks = defaultdict(list)
for st, ln, tr, us, thr, arr, oos in i.execute(
        "select station, line_id, track, usable, through, arrows, out_of_service from station_tracks where kind = 'roman'"):
    if not oos:
        tracks[(st, ln)].append((tr, us or "", thr, arr or ""))
route = defaultdict(list)
for no, st, kind in g.execute("select train_no, station, kind from stops order by train_no, seq"):
    route[no].append((st, kind))

def direction(no, st):
    """L / R of train no at st and the line used, from the diagram's end labels"""
    r = [x for x, k in route[no]]
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

res, bad = Counter(), []
for no, st, note in g.execute("select train_no, station, note from stops where kind = 'stop' and note like '%ka %'"):
    d, ln = direction(no, st)
    if d is None:
        res["direction unknown (no diagram / end labels)"] += 1
        continue
    for other in dict.fromkeys(x.upper() for x in re.findall(r"ka (\d{1,4}[fF]?)", note)):
        if not any(x == st for x, _ in route.get(other, [])):
            continue
        od, _ = direction(other, st)
        tr = tracks[(st, ln)]
        if od == d:                                  # overtake: a track for d other than the main of d
            main = [t for t in tr if t[2] and d in t[3]]
            ok = [t for t in tr if d in t[1] and t not in main[:1]]
            res["overtake: waiting track available" if ok else "overtake: NO waiting track for the direction"] += 1
            if not ok:
                bad.append(("overtake", st, ln, d, no, other, tr))
        elif od:                                     # crossing: two different tracks, one per direction
            a = [t for t in tr if d in t[1]]
            b = [t for t in tr if od in t[1]]
            ok = a and b and len({t[0] for t in a} | {t[0] for t in b}) >= 2
            res["crossing: tracks available" if ok else "crossing: NO track pair"] += 1
            if not ok:
                bad.append(("cross", st, ln, d, no, other, tr))
for k, v in sorted(res.items()):
    print(f"{k:48s} {v}")
c = Counter((b[0], b[1]) for b in bad)
print("cases without tracks, by station:", c.most_common(30))
for b in list({(x[0], x[1]): x for x in bad}.values())[:12]:
    print("  ", b[0], b[1], b[2], "dir", b[3], b[4], "vs", b[5], b[6])
