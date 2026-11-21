"""For every KAI-timed halt, read the halt crossing time off the GAPEKA graph sheet -> graph_halts.csv"""
import csv
import re
import sqlite3
import sys

import pymupdf

sys.path.insert(0, "/home/user/Gapeka-parser")
import gapeka_pipeline as gp  # noqa: E402

db = sqlite3.connect("/home/user/Gapeka-parser/gapeka.db")
doc = pymupdf.open("/home/user/Gapeka-parser/GAPEKA-JAWA-2026.pdf")
names = {}
for code, name in db.execute("select code,name from stations"):
    names.setdefault(re.sub(r"[^a-z]", "", (name or "").lower()), code)
PAGES = {"bdg": (24, 25, 26), "sby": (43, 44, 45)}


def page_info(pn):
    p = doc[pn]
    ax = gp.hour_axes(p, 16.5)[0]
    hl = []
    segs = []
    for d in p.get_drawings():
        for it in d["items"]:
            if it[0] != "l":
                continue
            a, b = it[1], it[2]
            if abs(a.y - b.y) < .3 and abs(a.x - b.x) > 30:
                hl.append((round(a.y, 1), min(a.x, b.x), max(a.x, b.x)))
            elif abs(a.y - b.y) > 2 and abs(a.x - b.x) > .5:
                segs.append((a.x, a.y, b.x, b.y))
    # station label (left column) -> nearest horizontal line y
    ys = sorted({h[0] for h in hl if h[2] - h[1] > 30 or True})
    st = {}
    for w in p.get_text("words"):
        if 280 < w[0] < 330:
            k = re.sub(r"[^a-z]", "", w[4].lower())
            if k in names:
                cy = (w[1] + w[3]) / 2
                y = min(ys, key=lambda v: abs(v - cy))
                if abs(y - cy) < 12:
                    st.setdefault(names[k], []).append(y)
    return ax, st, segs


info = {pn: page_info(pn) for v in PAGES.values() for pn in v}
H = list(csv.DictReader(open("halts.csv")))
out = []
for h in H:
    src = "bdg" if h["service"].endswith(("Bandung Raya", "Garut")) else "sby"
    t0, t1 = float(h["dep_prev"]), float(h["arr_next"])
    best = None
    for pn in PAGES[src]:
        ax, st, segs = info[pn]
        if not all(c in st for c in (h["prev"], h["halt"], h["next"])):
            continue
        for yp in st[h["prev"]]:
            for yh in st[h["halt"]]:
                for yn in st[h["next"]]:
                    if not (min(yp, yn) < yh < max(yp, yn)):
                        continue
                    for x0, y0, x1, y1 in segs:
                        if (y0 - yh) * (y1 - yh) > 0:
                            continue
                        k = (x1 - x0) / (y1 - y0)

                        def tt(y):
                            return ((x0 + (y - y0) * k) - ax["x_at_0h"]) / ax["pt_per_hour"] * 3600
                        err = abs(tt(yp) - t0) + abs(tt(yn) - t1)
                        if best is None or err < best[0]:
                            best = (err, tt(yh), pn, (yh - yp) / (yn - yp))
    row = dict(h)
    if best and best[0] < 90:
        row.update(graph_err=round(best[0]), graph_off=(best[1] - t0) / 60, graph_page=best[2], graph_frac=best[3])
    else:
        row.update(graph_err=round(best[0]) if best else "", graph_off="", graph_page="", graph_frac="")
    out.append(row)
w = csv.DictWriter(open("graph_halts.csv", "w", newline=""), fieldnames=list(out[0]))
w.writeheader()
w.writerows(out)
print("found", sum(1 for r in out if r["graph_off"] != ""), "of", len(out))
