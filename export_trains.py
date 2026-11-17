"""Export a station x trip matrix (stations across, one row per train) to CSV.

  py export_trains.py 350-400                      -> arrival/departure shown as dep (arr at the last stop)
  py export_trains.py 350-400 1608 -o out.csv      -> range plus single trains, custom file name
  py export_trains.py 400-410 --odd                -> only odd-numbered trains (401, 403, ...); --even for even ones
  py export_trains.py 350-400 --split              -> two header rows: station name (twice, once for a terminus) over Arr/Dep
  py export_trains.py 350-400 --arr                -> arrival time only (default is departure only)
  py export_trains.py 350-400 --both               -> cells as "arr,dep" when they differ (change the separator with --sep " / ")
  py export_trains.py 350-400 --codes              -> station codes instead of names in the header
  py export_trains.py 350-400 --db other.db
Cells are HH:MM:SS; times after midnight wrap to 00:xx. Stations the train does not serve are left empty.
Halts (Berh/Sb) show one time, the arrival, as HH:MM like the KAI ticket (not in --split/--both);
estimated halt times (also those from halt_offsets.csv) are marked with a trailing '*'.
"""
import csv
import re
import sqlite3
import sys


def hms(s):
    s = int(round(s)) % 86400
    return f"{s // 3600:02d}:{s % 3600 // 60:02d}:{s % 60:02d}"


def main(argv):
    args, out, dbf, both, codes, sep, arr_only, split, parity = [], "matrix.csv", "gapeka.db", False, False, ",", False, False, None
    it = iter(argv[1:])
    for a in it:
        if a == "-o":
            out = next(it)
        elif a == "--db":
            dbf = next(it)
        elif a == "--sep":
            sep = next(it)
        elif a == "--odd":
            parity = 1
        elif a == "--even":
            parity = 0
        elif a == "--split":
            split = True
        elif a == "--arr":
            arr_only = True
        elif a == "--both":
            both = True
        elif a == "--codes":
            codes = True
        else:
            args.append(a)
    if not args:
        sys.exit(__doc__)
    db = sqlite3.connect(dbf)
    wanted = []
    for a in args:
        if "-" in a:
            lo, hi = (int(x) for x in a.split("-"))
            wanted += [r[0] for r in db.execute(
                "select no from trains where no GLOB '[0-9]*' and cast(no as integer) between ? and ?", (lo, hi))]
        else:
            wanted.append(a)
    if parity is not None:           # parity of the leading digits, so 2501F counts as odd
        def par(n):
            d = re.match(r"\d+", n)
            return int(d.group()) % 2 if d else None
        wanted = [n for n in wanted if par(n) == parity]
    wanted = sorted(set(wanted), key=lambda n: (not n.isdigit(), int(n) if n.isdigit() else 0, n))
    name = dict(db.execute("select code,name from stations"))
    trains = {}
    for no in wanted:
        rows = db.execute("select station,kind,arr_s,dep_s,estimated from schedule where train_no=? order by seq", (no,)).fetchall()
        if rows:
            first = db.execute("select min(coalesce(dep_s,arr_s)) from schedule where train_no=?", (no,)).fetchone()[0]
            trains[no] = (rows, first if first is not None else 0)
    if not trains:
        sys.exit("no trains found")

    # column order: start from the longest train, then slot the others in beside the stations they share
    order = []
    for no, (rows, _) in sorted(trains.items(), key=lambda kv: -len(kv[1][0])):
        seq = []
        for r in rows:
            if r[0] not in seq:
                seq.append(r[0])
        if not order:
            order = seq[:]
            continue
        pos = {s: i for i, s in enumerate(order)}
        shared = [pos[s] for s in seq if s in pos]
        if len(shared) >= 2 and shared[0] > shared[-1]:       # runs the other way round
            seq.reverse()
        prev = None
        for s in seq:
            if s in pos:
                prev = s
                continue
            at = (order.index(prev) + 1) if prev is not None else 0
            order.insert(at, s)
            pos = {x: i for i, x in enumerate(order)}
            prev = s

    label = [s if codes else name.get(s, s) for s in order]
    cols = []                       # split mode: (station, "arr"/"dep", label)
    heads = []
    if split:
        has = {}
        for no, (rows, _) in trains.items():
            for st, kind, a, d, est in rows:
                h = has.setdefault(st, [False, False, False])
                h[0] |= a is not None
                h[1] |= d is not None
                h[2] |= a is not None and d is not None
        for st, lab in zip(order, label):
            a, d, both_ = has.get(st, [False, False, False])
            if both_:
                cols += [(st, "arr", lab), (st, "dep", lab)]          # name written twice
            else:
                cols.append((st, "arr" if a and not d else "dep", lab))   # terminus: once
        heads.append(["train", "service", "route"] + [c[2] for c in cols])
        heads.append(["", "", ""] + [("Arr" if c[1] == "arr" else "Dep") for c in cols])
    else:
        heads.append(["train", "service", "route"] + label)

    data = []                        # cells are text, or (seconds, estimated) for a single time
    for no, (rows, _) in trains.items():
        svc, route = db.execute("select service,route from trains where no=?", (no,)).fetchone()
        cell = {}
        for st, kind, a, d, est in rows:
            if st in cell:
                continue
            if split:
                cell[st] = {"arr": (a, est) if a is not None else "", "dep": (d, est) if d is not None else ""}
            elif a is None and d is None:
                cell[st] = ""
            elif both and a is not None and d is not None and int(a) != int(d):
                cell[st] = f"{hms(a)}{sep}{hms(d)}" + ("*" if est else "")
            elif kind == "halt":             # Berh/Sb: one time, the arrival, in whole minutes as on the KAI ticket
                cell[st] = (a if a is not None else d, est, True)
            elif arr_only:
                cell[st] = (a if a is not None else d, est)
            else:
                cell[st] = (d if d is not None else a, est)
        if split:
            data.append([no, svc, route] + [cell.get(st, {}).get(which, "") for st, which, _ in cols])
        else:
            data.append([no, svc, route] + [cell.get(st, "") for st in order])

    if True:
        def txt(c):
            if not isinstance(c, tuple):
                return c
            if len(c) > 2:                   # halt: HH:MM, rounded to the minute
                return hms(round(c[0] / 60) * 60)[:5] + ("*" if c[1] else "")
            return hms(c[0]) + ("*" if c[1] else "")
        with open(out, "w", newline="", encoding="utf-8-sig") as f:
            w = csv.writer(f)
            w.writerows(heads)
            for row in data:
                w.writerow([txt(c) for c in row])
    print(f"wrote {out}: {len(trains)} trains x {len(order)} stations")


main(sys.argv)