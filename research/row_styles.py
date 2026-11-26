"""Row line style on the graph sheets vs our station kinds (user: halts are drawn with DASHED / DOTTED row lines;
Jsp. junctions have no row line). For every row of every graph page (infra.db sheet_pairs) the horizontal strokes in
the graph area at the row's y are summed by style: solid / dashed. Output: per station the majority style over its
pages and our kind (package), listing the disagreements.
    python research/row_styles.py
"""
import collections
import gzip
import json
import sqlite3
import sys

import pymupdf

ROOT = "/home/user/Gapeka-parser"


def page_rows(inf):
    rows = collections.defaultdict(dict)
    for page, a, b, ya, yb in inf.execute("select page, a, b, y_a, y_b from sheet_pairs"):
        rows[page][a] = ya
        rows[page][b] = yb
    return rows


def styles(page, rows, x_min=340.0):
    """{code: {'solid': length, 'dashed': length}} for one page"""
    ys = sorted((y, c) for c, y in rows.items())
    out = {c: collections.Counter() for c in rows}
    for d in page.get_drawings():
        dashed = bool(d.get("dashes")) and d["dashes"] not in ("[] 0", "[] 0.0")
        for it in d["items"]:
            if it[0] == "l":
                p, q = it[1], it[2]
                segs = [(p.x, p.y, q.x, q.y)]
            elif it[0] == "re" and it[1].height < 1.2 and it[1].width > 20:
                r = it[1]
                segs = [(r.x0, (r.y0 + r.y1) / 2, r.x1, (r.y0 + r.y1) / 2)]
            else:
                continue
            for x0, y0, x1, y1 in segs:
                if abs(y0 - y1) > 0.3 or max(x0, x1) < x_min or abs(x1 - x0) < 3:
                    continue
                y = (y0 + y1) / 2
                near = [c for yy, c in ys if abs(yy - y) <= 1.2]
                for c in near:
                    out[c]["dashed" if dashed else "solid"] += abs(x1 - x0)
    return out


def main():
    inf = sqlite3.connect(f"{ROOT}/infra.db")
    pkg = json.load(gzip.open(f"{ROOT}/dist/gapeka-2026/package.json.gz"))
    doc = pymupdf.open(f"{ROOT}/GAPEKA-JAWA-2026.pdf")
    votes = collections.defaultdict(collections.Counter)
    for page, rows in sorted(page_rows(inf).items()):
        for c, cnt in styles(doc[page - 1], rows).items():
            tot = cnt["solid"] + cnt["dashed"]
            v = "none" if tot < 50 else ("dashed" if cnt["dashed"] > cnt["solid"] else "solid")
            votes[c][v] += 1
    used = {r[0] for t in pkg["trains"] for r in t["stops"]}
    kinds = collections.Counter()
    bad = []
    for c, v in sorted(votes.items()):
        style = v.most_common(1)[0][0]
        kind = pkg["stations"].get(c, {}).get("kind")
        kinds[(kind, style)] += 1
        expect = {"station": "solid", "halt": "dashed", "junction": "none", "sheet_only": None}.get(kind)
        if expect and style != expect:
            name = pkg["stations"][c].get("display_name") or pkg["stations"][c].get("name")
            stops = collections.Counter(r[1] for t in pkg["trains"] for r in t["stops"] if r[0] == c)
            bad.append((c, name, kind, style, dict(v), dict(stops), c in used))
    print("kind x style:", dict(kinds))
    print(f"disagreements ({len(bad)}):")
    for b in bad:
        print("  ", b)


if __name__ == "__main__":
    sys.exit(main())
