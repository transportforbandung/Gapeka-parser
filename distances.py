"""'JARAK ANTAR STASIUN' (Lampiran IV, PDF pages 1875-1887): per line an O/D matrix of distances (km, '9,241')
between its stations; small two-station boxes ('BPR - BST' over '12,300', 'KEN-CGD' over '6,439') for short branches.

read(doc) -> [dict(page, matrix, a, b, km, adjacent)]: every pair of a matrix once (a before b in matrix order);
adjacent = 1 for consecutive stations of the matrix (the cells just above the diagonal)."""
import re

import pymupdf

NUM = re.compile(r"^\d{1,4},\d{3}$")
CODE = re.compile(r"^[A-Z]{1,5}$")
ALIAS = {"MGA": "MAG"}      # Magetan: MGA in the distance table, MAG in the timetable and infra.db


def _km(t):
    return float(t.replace(",", "."))


def find_pages(doc, first=1860):
    out = []
    for pno in range(first, len(doc) + 1):
        t = doc[pno - 1].get_text()
        if "JARAK ANTAR STASIUN" in t:
            out.append(pno)
            continue
        if out and NUM.search(t.split()[0] if t.split() else "") is None and "O/D" in t:
            out.append(pno)
    return out


def read_page(page, pno):
    words = page.get_text("words")
    cy = lambda w: (w[1] + w[3]) / 2
    cx = lambda w: (w[0] + w[2]) / 2
    # header rows: a line of the numbers 1..n (n >= 2), each with a station code just above it (codes may be
    # staggered by a few points, p1881 'KEJ')
    by_line = {}
    for w in words:
        if re.fullmatch(r"\d+", w[4]):
            by_line.setdefault(round(cy(w) / 2), []).append(w)
    headers = []
    for ws in by_line.values():
        ws = sorted(ws, key=lambda w: w[0])
        cols = []
        for i, n in enumerate(ws, 1):
            if n[4] != str(i):
                break
            c = [w for w in words if CODE.match(w[4]) and 3 < cy(n) - cy(w) < 18 and abs(cx(w) - cx(n)) < 9]
            if not c:
                break
            c = min(c, key=lambda w: cy(n) - cy(w))
            cols.append((c[4], cx(n), cy(n)))
        if len(cols) >= 2:
            headers.append(cols)
    out = []
    for cols in headers:
        hy = cols[0][2]
        x0, x1 = cols[0][1], cols[-1][1]
        sp = (x1 - x0) / (len(cols) - 1)
        names = [c[0] for c in cols]
        # row labels: the code, left of the first column, followed by its index number
        rows = {}
        for w in words:
            if w[4] in names and cy(w) > hy + 3 and x0 - 150 < w[0] < x0 - sp * 0.4:
                i = names.index(w[4])
                ix = [v for v in words if v[4] == str(i + 1) and v[0] > w[2] and v[0] < x0 - sp * 0.3
                      and abs(cy(v) - cy(w)) < 8]
                if ix and i not in rows and cy(w) - hy < 25 * (len(cols) + 2):
                    rows[i] = cy(w)
        if len(rows) < len(cols) - 1:
            continue
        for i, ry in rows.items():
            for w in words:
                if NUM.match(w[4]) and abs(cy(w) - ry) < 4:
                    j = round((cx(w) - x0) / sp)
                    if 0 <= j < len(cols) and abs(cx(w) - (x0 + j * sp)) < sp * 0.5 and j > i:
                        out.append(dict(page=pno, matrix="-".join(dict.fromkeys([names[0], names[1], names[-1]])), a=names[i], b=names[j],
                                        km=_km(w[4]), adjacent=int(j == i + 1)))
    # two-station boxes: 'BPR - BST' / 'KEN-CGD' with a number right below
    lines = sorted(words, key=lambda w: (round(cy(w)), w[0]))
    for k, w in enumerate(lines):
        pair = None
        m = re.fullmatch(r"([A-Z]{1,5})-([A-Z]{1,5})", w[4])
        if m:
            pair, box = (m.group(1), m.group(2)), (w[0], w[2], cy(w))
        elif CODE.match(w[4]) and k + 2 < len(lines) and lines[k + 1][4] == "-" and CODE.match(lines[k + 2][4]) \
                and abs(cy(lines[k + 2]) - cy(w)) < 2:
            pair, box = (w[4], lines[k + 2][4]), (w[0], lines[k + 2][2], cy(w))
        if not pair:
            continue
        num = [v for v in words if NUM.match(v[4]) and ((2 < cy(v) - box[2] < 12 and box[0] - 40 < cx(v) < box[1] + 40)
                                                         or (abs(cy(v) - box[2]) < 3 and 0 <= v[0] - box[1] < 60))]
        if num and not any(o["a"] == pair[0] and o["b"] == pair[1] for o in out):
            v = min(num, key=lambda v: abs(cx(v) - (box[0] + box[1]) / 2))
            out.append(dict(page=pno, matrix="-".join(pair), a=pair[0], b=pair[1], km=_km(v[4]), adjacent=1))
    return out


def read(doc, first=1875, last=None):
    last = last or len(doc)
    out = []
    for pno in range(first, last + 1):
        page = doc[pno - 1]
        if pno > first and "O/D" not in page.get_text():
            continue
        out += read_page(page, pno)
    for r in out:
        r["a"], r["b"] = ALIAS.get(r["a"], r["a"]), ALIAS.get(r["b"], r["b"])
    return out


if __name__ == "__main__":
    import sys
    from collections import Counter
    rows = read(pymupdf.open(sys.argv[1] if len(sys.argv) > 1 else "GAPEKA-JAWA-2026.pdf"))
    print(len(rows), "pairs,", sum(r["adjacent"] for r in rows), "adjacent;", Counter(r["page"] for r in rows))
    for m in dict.fromkeys(r["matrix"] for r in rows):
        adj = [r for r in rows if r["matrix"] == m and r["adjacent"]]
        print(m, len(adj), " ".join(f"{r['a']}-{r['b']} {r['km']:.3f}" for r in adj)[:200])
