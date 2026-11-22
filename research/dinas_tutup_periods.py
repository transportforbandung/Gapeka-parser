"""Dinas tutup periods from the '=' marks on the graph (user). KAI does not publish when dinas tutup stations close;
a train line marked '=' passes stations that are closed at that time.

Marks sit on sub-sections (between two sheet rows). Each mark is matched to the train whose line is at that spot:
the train's position is interpolated by km between its timed rows. Per closable station the trains passing it are
listed in time order with a flag (closed / open for this train), and the possible closing windows are the runs of
'closed' trains. Rules tested for which station a mark belongs to:
  either  - the station is closed for the train if a mark sits on either sub-section next to it
  both    - only if the train is marked on both sub-sections next to it (inside the arrow)
Check: crossings / overtakes printed at a closable station (notes 'x' / '//' / '=') need the station open.
Usage: python3 research/dinas_tutup_periods.py [either|both]"""
import collections
import sqlite3
import sys

REPO = "/home/user/Gapeka-parser"
RULE = sys.argv[1] if len(sys.argv) > 1 else "both"
inf = sqlite3.connect(f"{REPO}/infra.db")
tt = sqlite3.connect(f"{REPO}/gapeka.db")
DAY = 86400
nm = dict(inf.execute("select code, name from stations"))
kind = dict(inf.execute("select code, kind from stations"))
ttype = dict(tt.execute("select no, type from trains"))
seclen = {}
for a, b, km in inf.execute("select a, b, km from sections where variant=''"):
    seclen[(a, b)] = seclen[(b, a)] = km


def hm(s):
    s = int(round(s)) % DAY
    return f"{s // 3600:02d}:{s % 3600 // 60:02d}"


stops = collections.defaultdict(list)
for no, st, a, d in tt.execute("select train_no, station, arr_s, dep_s from stops order by train_no, seq"):
    stops[no].append((st, a, d))

# crossings / overtakes at stations (the station must be open)
meets = collections.defaultdict(list)
for no, st, sym, a, d in tt.execute("select n.train_no, n.station, n.symbol, s.arr_s, s.dep_s from notes n join stops s "
                                    "on s.train_no = n.train_no and s.seq = n.seq where n.symbol in ('x', '//', '=')"):
    t = a if a is not None else d
    if t is not None:
        meets[st].append((t % DAY, no, sym))

pieces = inf.execute("select end_a, end_b, closed, page from dinas_tutup order by page, end_a").fetchall()
marks = collections.defaultdict(list)
for a, b, ra, rb, fr, t, pg, x, y in inf.execute("select * from dinas_tutup_marks"):
    marks[(a, b)].append((t % DAY, ra, rb, fr))

report, totals = [], collections.Counter()
for a, b, closed, pg in pieces:
    seq = [a] + closed.split() + [b]
    pos, acc = {a: 0.0}, 0.0
    for x, y in zip(seq, seq[1:]):
        acc += seclen.get((x, y), 1.0)
        pos[y] = acc
    # train position over time inside the piece
    tr = {}
    for no, sl in stops.items():
        if ttype.get(no) == "dinas":
            continue
        pts = []
        for st, ar, dp in sl:
            if st in pos:
                if ar is not None:
                    pts.append((ar, pos[st]))
                if dp is not None and dp != ar:
                    pts.append((dp, pos[st]))
        if len(pts) < 2 or len({p for _, p in pts}) < 2:
            continue
        passes = [st for st, _, _ in sl if st in pos]
        tr[no] = (sorted(pts), passes)

    def time_at(no, p):
        pts = tr[no][0]
        for (t0, p0), (t1, p1) in zip(pts, pts[1:]):
            if min(p0, p1) - 1e-6 <= p <= max(p0, p1) + 1e-6 and p0 != p1:
                return t0 + (t1 - t0) * (p - p0) / (p1 - p0)
        return None

    marked = collections.defaultdict(set)      # train -> {(ra, rb)}
    unmatched = []
    for t, ra, rb, fr in marks[(a, b)]:
        p = pos[ra] + fr * (pos[rb] - pos[ra])
        best = None
        for no in tr:
            tm = time_at(no, p)
            if tm is None:
                continue
            dd = min(abs(t - tm % DAY), DAY - abs(t - tm % DAY))
            if dd <= 150 and (best is None or dd < best[0]):
                best = (dd, no)
        if best:
            marked[best[1]].add(frozenset((ra, rb)))
        else:
            unmatched.append(t)

    for k, st in enumerate(seq[1:-1], 1):
        if kind.get(st) != "station":
            continue                                   # halts are not closed; they are transparent
        i0 = k - 1
        while i0 > 0 and kind.get(seq[i0]) != "station":
            i0 -= 1
        i1 = k + 1
        while i1 < len(seq) - 1 and kind.get(seq[i1]) != "station":
            i1 += 1
        prev_ = {frozenset((seq[i], seq[i + 1])) for i in range(i0, k)}
        next_ = {frozenset((seq[i], seq[i + 1])) for i in range(k, i1)}
        rowsv = []
        for no, (pts, passes) in tr.items():
            if st not in passes:
                continue
            tm = time_at(no, pos[st])
            if tm is None:
                continue
            m = marked.get(no, set())
            flag = (bool(prev_ & m) and bool(next_ & m)) if RULE == "both" else bool((prev_ | next_) & m)
            rowsv.append((tm % DAY, no, flag))
        rowsv.sort()
        # windows of consecutive closed trains (cyclic)
        n = len(rowsv)
        if not n:
            continue
        wins, k0 = [], None
        start = next((i for i in range(n) if not rowsv[i][2]), None)
        if start is None:
            wins = [(0, n - 1)]
        else:
            run = []
            for i in range(1, n + 1):
                j = (start + i) % n
                if rowsv[j][2]:
                    run.append(j)
                elif run:
                    wins.append((run[0], run[-1]))
                    run = []
        conflicts = []
        for t, no, sym in meets.get(st, []):
            for i0, i1 in wins:
                lo, hi = rowsv[i0][0], rowsv[i1][0]
                if (lo <= hi and lo < t < hi) or (lo > hi and (t > lo or t < hi)):
                    conflicts.append(f"{no} {sym} {hm(t)}")
        totals["meets"] += len(meets.get(st, []))
        totals["conflicts"] += len(conflicts)
        report.append((a, b, pg, st, n, sum(f for _, _, f in rowsv), wins, rowsv, conflicts, unmatched))

for a, b, pg, st, n, nc, wins, rowsv, conflicts, um in report:
    print(f"\n{nm.get(st, st)}  (arrow {nm.get(a, a)} - {nm.get(b, b)}, page {pg}): {nc} of {n} trains pass it closed")
    for i0, i1 in wins:
        before = rowsv[(i0 - 1) % n]
        after = rowsv[(i1 + 1) % n]
        k = (i1 - i0) % n + 1
        print(f"   closed from {hm(before[0])}-{hm(rowsv[i0][0])} to {hm(rowsv[i1][0])}-{hm(after[0])}"
              f"  ({k} closed passes, last open train before {before[1]}, first open after {after[1]})")
    if conflicts:
        print("   crossings inside a closed window:", ", ".join(conflicts))
print(f"\nrule '{RULE}': crossings / overtakes at closable stations {totals['meets']}, inside a closed window "
      f"{totals['conflicts']}")
