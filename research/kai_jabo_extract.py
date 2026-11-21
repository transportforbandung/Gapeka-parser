"""Extract KAI Commuter station timetables (Jabodetabek, Nambo, Basoetta; update 1 Desember 2026) to CSV.

    python -I kai_jabo_extract.py OUT.csv FILE.pdf [FILE.pdf ...]

Each page holds one or more tables (a table starts at its "NOMOR KA" header): one row per train, one column per station.
Columns are found from the x positions of the time cells; the header text above a column is a station code (most
sheets) or a station name (Basoetta), matched to gapeka.db codes. Cells: HH.MM / HH:MM = time, "Ls" = passes without
stopping (langsung), empty = not served. Output rows: file, page, table, train, train2, relasi, seq, station, header,
time, note (keterangan).
"""
import csv
import difflib
import os
import re
import sqlite3
import sys
from collections import defaultdict

import pymupdf

TIME = re.compile(r"^\d{1,2}[.:]\d{2}$")
TRAIN = re.compile(r"^\d{3,5}[A-Z]?$")
# names on the Basoetta sheet that differ from the timetable names
EXTRA = {"bnicity": "SUDB", "bandarasoekarnohatta": "BST"}

db = sqlite3.connect("/home/user/Gapeka-parser/gapeka.db")
CODES = {c for (c,) in db.execute("select code from stations")}
BYNAME = {re.sub(r"[^a-z]", "", n.lower()): c for c, n in db.execute("select code,name from stations")}
BYNAME.update(EXTRA)


def to_code(text):
    t = text.strip()
    if t.upper() in CODES:
        return t.upper()
    k = re.sub(r"[^a-z]", "", t.lower())
    if k in BYNAME:
        return BYNAME[k]
    m = difflib.get_close_matches(k, list(BYNAME), n=1, cutoff=0.85)
    return BYNAME[m[0]] if m else None


def cy(w):
    return (w[1] + w[3]) / 2


def page_tables(page):
    ws = page.get_text("words")
    heads = sorted([w for w in ws if w[4] == "NOMOR"], key=lambda w: w[0])
    out = []
    for n, h in enumerate(heads):
        x_lo = h[0] - 40
        right = [g[0] for g in heads if g[0] > h[0] + 100]
        x_hi = min(right) - 40 if right else 1e9
        # tables can also be stacked (Nambo: one table per direction): stop at the next header below
        below = [g[1] for g in heads if g[1] > h[3] + 5 and abs(g[0] - h[0]) < 40]
        y_hi = min(below) - 12 if below else 1e9
        tw = [w for w in ws if x_lo <= w[0] < x_hi and w[1] < y_hi and w[3] > h[1] - 60]
        cells = [w for w in tw if (TIME.match(w[4]) or w[4] == "Ls") and cy(w) > h[3]]
        if not cells:
            continue
        first_row = min(cy(w) for w in cells)
        # columns from the header words (codes, or names over one or two lines): words on one line with a small
        # gap, or overlapping in x on different lines, belong to the same column
        band = sorted([w for w in tw if h[1] - 3 <= cy(w) < first_row - 3 and w[0] > h[2] + 20
                       and w[4] not in ("NO.", "NOMOR", "KA", "RELASI", "KETERANGAN", "STASIUN")], key=lambda w: w[0])
        groups = []
        for w in band:
            g = next((g for g in groups if any((abs(cy(v) - cy(w)) < 3 and -1 < w[0] - v[2] < 6) or
                                               (abs(cy(v) - cy(w)) >= 3 and w[0] < v[2] - 2 and w[2] > v[0] + 2) for v in g)), None)
            if g is None:
                groups.append([w])
            else:
                g.append(w)
        groups = [g for g in groups if not all(TIME.match(v[4]) for v in g)]
        groups.sort(key=lambda g: min(v[0] for v in g))
        cols = [(min(v[0] for v in g) + max(v[2] for v in g)) / 2 for g in groups]
        names = []
        for g in groups:
            t = " ".join(v[4] for v in sorted(g, key=lambda v: (round(cy(v)), v[0])))
            names.append((t, to_code(t)))
        ket_x = [w[0] for w in tw if w[4] == "KETERANGAN"]
        # rows: train number in the NOMOR column (right of the row-number column "NO.")
        no_x = min([w[2] for w in tw if w[4] == "NO." and abs(cy(w) - cy(h)) < 4] or [h[0] - 20])
        trains = sorted([w for w in tw if TRAIN.match(w[4]) and abs(w[0] - h[0]) < 15 and w[0] > no_x + 4
                         and cy(w) > h[3]], key=cy)
        allcells = [w for w in tw if (TIME.match(w[4]) or w[4] == "Ls") and cy(w) > h[3]]
        mine = defaultdict(list)                      # each time cell belongs to the nearest train number (by y)
        for w in allcells:
            if trains:
                t = min(trains, key=lambda t: abs(cy(t) - cy(w)))
                if abs(cy(t) - cy(w)) < 6:
                    mine[id(t)].append(w)
        # a number without cells, just below another, is that train's second number (Basoetta "809" / "810")
        rows_ = []
        for tr in trains:
            if not mine[id(tr)] and rows_ and 0 < cy(tr) - cy(rows_[-1][0]) < 12:
                rows_[-1][1] = tr[4]
                continue
            if mine[id(tr)] and rows_ and not mine[id(rows_[-1][0])] and 0 < cy(tr) - cy(rows_[-1][0]) < 12:
                rows_[-1] = [tr, rows_[-1][0][4]]
                continue
            side = [w for w in tw if TRAIN.match(w[4]) and abs(cy(w) - cy(tr)) < 2 and 5 < w[0] - tr[2] < 30]
            rows_.append([tr, side[0][4] if side else ""])   # Basoetta: "809 810" = number changes at Duri
        for k, (tr, second) in enumerate(rows_):
            y = cy(tr)
            same = mine[id(tr)] + [w for w in tw if abs(cy(w) - y) < 2.5 and not (TIME.match(w[4]) or w[4] == "Ls")]
            nxt = cy(rows_[k + 1][0]) if k + 1 < len(rows_) else y + 20
            prv = cy(rows_[k - 1][0]) if k > 0 else y - 20
            rel = " ".join(w[4] for w in same if h[2] + 5 < w[0] < (cols[0] - 15) and not TIME.match(w[4]))
            note = " ".join(w[4] for w in tw if ket_x and w[0] >= ket_x[0] - 10 and (prv + y) / 2 < cy(w) <= (y + nxt) / 2
                            and w[4] != "KETERANGAN")
            for w in same:
                if not (TIME.match(w[4]) or w[4] == "Ls"):
                    continue
                xc = (w[0] + w[2]) / 2
                j = min(range(len(cols)), key=lambda j: abs(cols[j] - xc))
                if abs(cols[j] - xc) > 10:
                    continue
                out.append(dict(table=n + 1, train=tr[4], train2=second, relasi=rel, seq=j + 1,
                                station=names[j][1] or "", header=names[j][0], time=w[4].replace(".", ":"), note=note))
    return out


def main():
    out_path, files = sys.argv[1], sys.argv[2:]
    rows = []
    for f in files:
        d = pymupdf.open(f)
        for i, page in enumerate(d):
            for r in page_tables(page):
                rows.append(dict(file=os.path.basename(f), page=i + 1, **r))
    with open(out_path, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=["file", "page", "table", "train", "train2", "relasi", "seq", "station", "header",
                                           "time", "note"])
        w.writeheader()
        w.writerows(rows)
    trains = {(r["file"], r["page"], r["table"], r["train"]) for r in rows}
    unk = sorted({r["header"] for r in rows if not r["station"]})
    print(f"{len(rows)} cells, {len(trains)} train rows, {len({r['train'] for r in rows})} train numbers; "
          f"headers not matched: {unk}")


if __name__ == "__main__":
    main()
