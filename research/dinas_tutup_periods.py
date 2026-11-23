"""Dinas tutup periods from the '=' marks on the graph (user). KAI does not publish when dinas tutup stations close;
a train line marked '=' passes stations that are closed at that time.

Marks sit on sub-sections (between two sheet rows). Each mark is matched to the train whose line is at that spot:
the train's position is interpolated by km between its timed rows. Per closable station the trains passing it are
listed in time order with a flag (closed / open for this train), and the possible closing windows are the runs of
'closed' trains. Rules tested for which station a mark belongs to:
  either  - the station is closed for the train if a mark sits on either sub-section next to it
  both    - only if the train is marked on both sub-sections next to it, or on one side whose far end is an
            arrow end (open boundary station: the mark can only mean this station; user)
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
            if dd <= 300 and (best is None or dd < best[0]):
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
            if RULE == "both":
                # marked on both sides; or on one side whose far end is an arrow end (an open boundary station):
                # that mark can only mean this station (user: Karangkandri, the only closable station between
                # Gumilir and Kasugihan; Cireungas is closed only when the Gandasoli side is marked)
                pm, nmk = bool(prev_ & m), bool(next_ & m)
                flag = (pm and nmk) or (pm and i0 == 0) or (nmk and i1 == len(seq) - 1)
            else:
                flag = bool((prev_ | next_) & m)
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


# ---- predicted closing hours (user): a closing period of 8 h (one shift; 16 h = two shifts if 8 h cannot hold the
#      closed passes), start / end on 10-minute steps (5 min if no 10-minute start fits). Constraints: every closed
#      pass inside; every open pass before the start or at least 10 min after the end (PD 19: open 10 min before
#      the first open-period train); crossings at the station outside the closed period.
def fit_hours(rowsv, meets_st):
    closed = [t / 60 for t, _, f in rowsv if f]
    opens = [t / 60 for t, _, f in rowsv if not f] + [t / 60 for t, _, _ in meets_st]
    if not closed:
        return None
    for dur in (480, 960, 720):
        for step in (10, 5):
            ok = []
            for s in range(0, 1440, step):
                def inside(t, lo, ln):
                    return (t - lo) % 1440 < ln
                if all(inside(t, s, dur) for t in closed) and not any(inside(t, s, dur + 10) for t in opens):
                    ok.append(s)
            if ok:
                # the middle of the longest run of feasible starts (cyclic)
                runs, cur = [], [ok[0]]
                for s in ok[1:]:
                    if s - cur[-1] == step:
                        cur.append(s)
                    else:
                        runs.append(cur); cur = [s]
                runs.append(cur)
                if len(runs) > 1 and runs[0][0] == 0 and runs[-1][-1] == 1440 - step:
                    runs = [runs[-1] + runs[0]] + runs[1:-1]
                best = max(runs, key=len)
                s = best[len(best) // 2]
                return dict(start=s, end=(s + dur) % 1440, dur=dur, step=step,
                            start_range=(best[0], best[-1]), dropped=0)
    return None


def hhmm(m):
    m = int(m) % 1440
    return f"{m // 60:02d}:{m % 60:02d}"


print("\nPredicted closing hours (8 h shifts, 10 / 5 min steps)")
rows_out = []
for a, b, pg, st, n, nc, wins, rowsv, conflicts, um in report:
    if not nc:
        continue
    f = fit_hours(rowsv, meets.get(st, []))
    note = ""
    if f is None:
        # isolated closed passes outside the main window are taken as reading noise: keep the main window only
        if wins:
            main = max(wins, key=lambda w: (w[1] - w[0]) % n)
            keep = {(main[0] + i) % n for i in range((main[1] - main[0]) % n + 1)}
            rv = [(t, no, fl and i in keep) for i, (t, no, fl) in enumerate(rowsv)]
            f = fit_hours(rv, meets.get(st, []))
            note = f"main window only ({nc - len(keep)} isolated closed passes ignored)"
    if f is None:
        # short working hours (user: Lampegan): the station is open only briefly; fit the OPEN window instead = the
        # widest window on 10-minute steps between the closed passes that holds every open pass (open >= 10 min
        # before each) -> closed = the rest of the day
        cl = sorted(t / 60 for t, _, fl in rowsv if fl)
        op = sorted(t / 60 for t, _, fl in rowsv if not fl) + [t / 60 for t, _, _ in meets.get(st, [])]
        if cl and op:
            o = op[0]
            before = max([c for c in cl if c < o] or [max(cl) - 1440])
            after = min([c for c in cl if c > o] or [min(cl) + 1440])
            o0 = int(-(-(before + 1) // 10) * 10)           # first 10-min step after the last closed pass
            o1 = int((after - 1) // 10 * 10)                # last 10-min step before the next closed pass
            if all((t - o0) % 1440 >= 10 and (t - o0) % 1440 <= (o1 - o0) % 1440 for t in op):
                print(f"  {nm.get(st, st):14s} short working hours: open {hhmm(o0)} - {hhmm(o1)}, "
                      f"closed {hhmm(o1)} - {hhmm(o0)} ({((o0 - o1) % 1440) / 60:.1f} h)")
                rows_out.append((st, a, b, hhmm(o1), hhmm(o0), round(((o0 - o1) % 1440) / 60, 1), "",
                                 "short working hours (user): open window fitted between the closed passes"))
                continue
        print(f"  {nm.get(st, st):14s} no 8 / 16 h window fits")
        rows_out.append((st, a, b, "", "", "", "", "no 8 / 16 h window fits the marks"))
        continue
    s0, s1 = f["start_range"]
    print(f"  {nm.get(st, st):14s} closed {hhmm(f['start'])} - {hhmm(f['end'])} ({f['dur'] // 60} h, step {f['step']} min;"
          f" possible starts {hhmm(s0)}-{hhmm(s1)}) {note}")
    rows_out.append((st, a, b, hhmm(f["start"]), hhmm(f["end"]), f["dur"] // 60,
                     f"{hhmm(s0)}-{hhmm(s1)}", note))

import csv
with open(f"{REPO}/rules/dinas_tutup_hours.csv", "w", newline="") as fh:
    w = csv.writer(fh)
    w.writerow(["station", "end_a", "end_b", "closed_from", "closed_to", "hours", "possible_starts", "source", "note"])
    for st, a, b, c0, c1, h, rng, note in rows_out:
        w.writerow([st, a, b, c0, c1, h, rng, "predicted from GAPEKA '=' marks (research/dinas_tutup_periods.py)",
                    note])
