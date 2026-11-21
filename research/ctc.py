"""Stations in dashed boxes on the graph sheets = centrally controlled (CTC) stations (user)."""
import json, re, sys
import pymupdf
sys.argv = ["x"]
import line_profile as lp
d = pymupdf.open("/home/user/Gapeka-parser/GAPEKA-JAWA-2026.pdf")
ctc, solid = {}, {}
for pg in range(6, 51):
    p = d[pg]
    boxes = []
    for x in p.get_drawings():
        r = x["rect"]
        if 270 < r.x0 < 360 and 15 < r.width < 120 and 6 < r.height < 25:
            dashed = bool(x.get("dashes") and x["dashes"] not in ("[] 0", "[] 0.0"))
            boxes.append((r, dashed))
    # dashed outlines are often drawn as many short line segments: collect them per row
    segs = []
    for x in p.get_drawings():
        for it in x["items"]:
            if it[0] == "l":
                a, b = it[1], it[2]
                if 270 < min(a.x, b.x) < 360 and (abs(a.x - b.x) < 6 and abs(a.y - b.y) < 6):
                    segs.append(((a.x + b.x) / 2, (a.y + b.y) / 2))
    words = [w for w in p.get_text("words") if 280 < w[0] < 360 and re.match(r"^[A-Za-z]", w[4])]
    for w in words:
        code = lp.NAMES.get(re.sub(r"[^a-z]", "", w[4].lower()))
        if not code: continue
        inside = [dsh for r, dsh in boxes if r.x0 < w[0] + 2 and r.x1 > w[2] - 2 and r.y0 < w[1] + 2 and r.y1 > w[3] - 2]
        nseg = sum(1 for sx, sy in segs if w[0] - 8 < sx < w[2] + 8 and w[1] - 6 < sy < w[3] + 6)
        if any(inside) or (not inside and nseg >= 8):
            ctc[code] = pg + 1
        elif inside:
            solid[code] = pg + 1
print(f"stations in dashed boxes (CTC): {len(ctc)};  in solid boxes: {len(solid)}")
names = dict(lp.db.execute("select code,name from stations"))
print("  CTC:", ", ".join(f"{names.get(c, c)} ({c})" for c in sorted(ctc, key=lambda c: (ctc[c], c))))
json.dump(ctc, open("ctc_stations.json", "w"))
