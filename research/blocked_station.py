"""User rule (Nov 2026): a train standing at a station that is longer than every Roman track there blocks the
station's entry, so the whole station counts as occupied: no other train may stand at or pass that station until it
has departed; followers wait at the previous station and are released to arrive when it is projected to have left
(release rule). Test against GAPEKA 2026: every stop by a train longer than the longest Roman track (train length as
in layout_checks.py), and the other trains at / through the station during the stop.
GD wagon length: argv[1] (default 14.6 m; INKA's page gives 12.5 m)."""
import io, contextlib, runpy, sys
from collections import Counter
R = "/home/user/Gapeka-parser"
buf = io.StringIO()
with contextlib.redirect_stdout(buf):
    ns = runpy.run_path(f"{R}/research/layout_checks.py")
g, tol, WAGON_M, LOCO_M = ns["g"], ns["tracks_on_line"], ns["WAGON_M"], ns["LOCO_M"]
gd = float(sys.argv[1]) if len(sys.argv) > 1 else 14.6
WAGON_M["GD"] = gd
cons = {n: c * 21 + 16 for n, c in g.execute("select train_no, cars from consists")}
for n, c, w, lo in g.execute("select train_no, cars, wagon, locomotive from rangkaian_pokok where kind = 'freight'"):
    cons[n] = c * WAGON_M.get((w or "GK")[:2], 14.0) + LOCO_M.get(lo, 16)
typ = dict(g.execute("select no, type from trains"))
dbl = ns["dbl"]
f = lambda s: f"{s // 3600 % 24:02d}:{s % 3600 // 60:02d}"
blocked = ok = n_inside = 0
gaps = []
bad = []
for no, st, a, d, note in g.execute("select train_no, station, arr_s, dep_s, note from stops where kind = 'stop'"):
    if no not in cons or a is None or d is None:
        continue
    tr, _ = tol(no, st)
    longest = max((x[1] for x in tr if x[1] is not None), default=None) if tr else None
    if not longest or cons[no] <= longest:
        continue
    blocked += 1
    oth = g.execute("select train_no, kind, arr_s, dep_s from stops where station = ? and train_no != ? and "
                    "kind in ('stop', 'pass') and coalesce(arr_s, dep_s) < ? and coalesce(dep_s, arr_s) > ?",
                    (st, no, d, a)).fetchall()
    # trains already standing there when the long train arrives (and fitting a track) may stay: they leave after
    # it (crossing rule: the long train departs first). A train ENTERING while it stands there breaks the rule.
    inside = [o for o in oth if o[1] == "stop" and o[2] is not None and o[2] < a and
              (o[0] not in cons or cons[o[0]] <= longest) and (o[3] or 0) >= d]
    enter = [o for o in oth if o not in inside]
    if not enter:
        ok += 1
        if inside:
            n_inside += 1
    else:
        bad.append((st, no, typ.get(no), round(cons[no]), longest, f(a), f(d), note or "",
                    [(o[0], o[1], f(o[2] if o[2] is not None else o[3])) for o in enter]))
    # release: the first train that enters after the long train has left, and how soon
    nxt = g.execute("select train_no, kind, coalesce(arr_s, dep_s) from stops where station = ? and train_no != ? and "
                    "kind in ('stop', 'pass') and coalesce(arr_s, dep_s) >= ? order by 3 limit 1", (st, no, d)).fetchone()
    if nxt and not enter:
        gaps.append((nxt[2] - d) / 60)
print(f"GD wagon {gd} m: stops by trains longer than every Roman track {blocked}; station otherwise empty during "
      f"the stop or only trains already inside {ok} ({ok / max(blocked, 1):.0%}; {n_inside} with a train already "
      f"inside that leaves after it); another train ENTERS while it stands there {len(bad)}")
import numpy as np
gp = np.array(gaps)
print(f"  next train entering after the long train left: median {np.median(gp):.0f} min, p10 {np.percentile(gp, 10):.0f}, "
      f"min {gp.min():.0f}; within 2 min {np.mean(gp <= 2):.0%}")
print("  by type:", Counter(b[2] for b in bad))
for b in sorted(bad):
    print("  ", b[0], b[1], b[2], f"{b[3]} m > {b[4]:g} m", b[5], "-", b[6], b[7], "| others:", b[8])
