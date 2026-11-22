"""Section symbols in the strip between the station-name column and the graph (graph sheets, left side).

Each section between two station rows is filled with a column of small circles (user):
  open circle with a horizontal bar  = closed automatic block (blok otomatis tertutup)
  open circle with a vertical bar    = open automatic block (blok otomatis terbuka)
  solid black circle                 = electromechanical block
  zigzag "S" inside the circle       = electrified line (combined with the block symbol)

The symbols are read from the vector drawing: a circle path (4+ Bezier pieces, 4-9.5 pt, square box) in the page's
symbol column (the x where most such circles sit), its fill, and the line pieces inside it.
"""
import re
from collections import Counter

BLOCK_NAMES = {"H": "closed automatic block", "V": "open automatic block", "B": "electromechanical block"}


def read_page(page):
    """[dict(y, block, electrified, sig)] for every symbol in the page's symbol column."""
    drs = page.get_drawings()
    cs = []
    for dr in drs:
        r = dr["rect"]
        if [i[0] for i in dr["items"]].count("c") >= 4 and 4 < r.width < 9.5 and abs(r.width - r.height) < 1:
            cs.append(dr)
    if not cs:
        return []
    col = Counter(round(c["rect"].x0 / 5) * 5 for c in cs).most_common(1)[0][0]
    cs = [c for c in cs if abs(c["rect"].x0 - col) < 8 and c["rect"].x0 < 420]
    near = [dr for dr in drs if col - 10 < dr["rect"].x0 < col + 20 and dr["rect"].width < 12]
    out = []
    for c in cs:
        r = c["rect"]
        big = r + (-0.6, -0.6, 0.6, 0.6)
        fill = c.get("fill")
        dark = fill is not None and sum(fill) < 1.5
        feats = Counter()
        for dr in near:
            if dr is c or not big.contains(dr["rect"]):
                continue
            if dark and not (dr.get("color") and sum(dr["color"]) > 1.5):
                continue         # dark line on a black circle (or drawn under it): not visible (p19, p47)
            for it in dr["items"]:
                if it[0] != "l":
                    continue
                a, b = it[1], it[2]
                dx, dy = abs(a.x - b.x), abs(a.y - b.y)
                if dy < 0.3 and dx > 0.7 * r.width:
                    feats["H"] += 1
                elif dx < 0.3 and dy > 0.7 * r.height:
                    feats["V"] += 1
                elif dx > 0.5 or dy > 0.5:
                    feats["z"] += 1
        bars = [k for k in ("H", "V") if feats[k]]
        if dark:
            block = "B"          # drawn black: some sheets keep a bar / zigzag hidden by the fill (p19, p47, p50)
        elif len(bars) == 1:
            block = bars[0]
        else:
            block = None         # plain open circle or both bars: not a section symbol (station icons)
        elec = feats["z"] >= 2
        if block is None and not elec:
            continue
        sig = ("black" if dark else "open") + "".join(sorted(feats))
        out.append(dict(y=(r.y0 + r.y1) / 2, x=r.x0, block=block, electrified=elec, sig=sig))
    return out


def for_section(symbols, y_a, y_b):
    """Summary of the symbols drawn between two station rows (exclusive of the rows' own lines)."""
    lo, hi = sorted((y_a, y_b))
    s = [x for x in symbols if lo < x["y"] < hi]
    if not s:
        return None
    inner = [x for x in s if lo + 6 < x["y"] < hi - 6]   # symbols at a row line belong to both sections
    s = inner or s
    blocks = Counter(x["block"] for x in s if x["block"])
    elec = sum(x["electrified"] for x in s)
    return dict(n=len(s), blocks=dict(blocks), electrified_n=elec, sigs=dict(Counter(x["sig"] for x in s)))


def summary_fields(sm):
    """(block_system, electrified, mixed note) from for_section output (possibly merged over pages)."""
    if not sm:
        return None, None, None
    blocks = sm["blocks"]
    block = max(blocks, key=blocks.get) if blocks else None
    notes = []
    if len(blocks) > 1:
        notes.append("mixed block symbols " + ", ".join(f"{BLOCK_NAMES[k]} x{v}" for k, v in sorted(blocks.items())))
    e = sm["electrified_n"]
    if 0 < e < sm["n"]:
        notes.append(f"electrified on {e} of {sm['n']} symbols")
    return BLOCK_NAMES.get(block), int(e * 2 >= sm["n"]), "; ".join(notes) or None


def control_boxes(page):
    """Joint-dispatch boxes (user): a tall box beside the symbol column linking two station rows; the circle with a
    triangle marks the station where the one dispatcher sits. Two drawings: white triangle in an open circle
    (Yogyakarta-Lempuyangan, Cirebon-Cirebon Prujakan) and black triangle in a circle joined by a line to a small
    circle (Jakarta Kota-Jayakarta, Bogor-Batutulis); both mean the same (user), rows inside the box too.
    Returns [dict(y0, y1, members=[dict(y, triangle)])], triangle = 'white' / 'black' / None."""
    drs = page.get_drawings()
    circ = [dr for dr in drs if [i[0] for i in dr["items"]].count("c") >= 4 and 4 < dr["rect"].width < 12
            and abs(dr["rect"].width - dr["rect"].height) < 1.2 and dr["rect"].x0 < 430]
    tris = [dr for dr in drs if dr.get("fill") is not None and sum(1 for i in dr["items"] if i[0] == "l") >= 3
            and dr["rect"].x0 < 430 and 3 < dr["rect"].width < 10 and 3 < dr["rect"].height < 10]
    out, seen = [], set()
    for dr in drs:
        r = dr["rect"]
        if r.x0 > 430 or not (6 < r.width < 16 and 18 < r.height < 150):
            continue
        kinds = [i[0] for i in dr["items"]]
        if not ("re" in kinds or "qu" in kinds or kinds.count("l") >= 4):
            continue
        key = (round(r.x0), round(r.y0), round(r.y1))
        if key in seen:
            continue
        inside = [c for c in circ if (r + (-1, -1, 1, 1)).contains(c["rect"])]
        if len(inside) < 2:
            continue
        mem = []
        for c in inside:
            cr = c["rect"] + (-0.6, -0.6, 0.6, 0.6)
            t = [x for x in tris if cr.contains(x["rect"])]
            tri = None
            if t:
                tri = "black" if sum(t[0]["fill"]) < 1.5 else "white"
            mem.append(dict(y=(c["rect"].y0 + c["rect"].y1) / 2, triangle=tri))
        if sum(1 for m in mem if m["triangle"]) != 1:
            continue
        seen.add(key)
        out.append(dict(y0=r.y0, y1=r.y1, members=sorted(mem, key=lambda m: m["y"])))
    return out


def dinas_tutup(page):
    """Dashed arrows left of the symbol column (user): 'dinas tutup' sections on electromechanical single track. In
    a set period every station between the two arrow ends is closed (no signalling, no crossing) and the end
    stations work the section with each other. Long arrows are split by short horizontal bars (with arrowheads both
    ways) at the stations that stay open. Dashes are drawn as dashed strokes or as one filled shape (page 47).
    Returns [dict(x, stops=[y, ...])]: consecutive stops are the ends of one section."""
    syms = read_page(page)
    if not syms:
        return []
    col = Counter(round(s["x"]) for s in syms).most_common(1)[0][0]
    drs = page.get_drawings()
    lines = []
    for dr in drs:
        r = dr["rect"]
        if not (col - 25 < r.x0 < col + 1) or r.height <= 15:
            continue
        dashed = bool(re.search(r"\[\s*[\d.]", dr.get("dashes") or ""))
        if (dashed and r.width < 1.5) or (dr["type"] == "f" and r.width < 6 and len(dr["items"]) > 12):
            lines.append([(r.x0 + r.x1) / 2, r.y0, r.y1])
    # join pieces of one arrow drawn as several dashed strokes
    lines.sort(key=lambda l: (round(l[0]), l[1]))
    merged = []
    for l in lines:
        if merged and abs(merged[-1][0] - l[0]) < 1.5 and l[1] - merged[-1][2] < 3:
            merged[-1][2] = max(merged[-1][2], l[2])
        else:
            merged.append(list(l))
    out = []
    for x, y0, y1 in merged:
        bars = [(dr["rect"].y0 + dr["rect"].y1) / 2 for dr in drs
                if abs((dr["rect"].x0 + dr["rect"].x1) / 2 - x) < 3 and 2.5 < dr["rect"].width < 9
                and y0 - 4 < dr["rect"].y0 < y1 + 4 and any(
                    it[0] == "l" and abs(it[1].y - it[2].y) < 0.3 and abs(it[1].x - it[2].x) > 2.5 for it in dr["items"])]
        stops = sorted(set([y0, y1] + bars))
        clean = []
        for y in stops:
            if clean and y - clean[-1] < 4:
                continue
            clean.append(y)
        out.append(dict(x=x, stops=clean))
    return out


def hour_axis(page):
    """(a, b): time in hours = a + b * x, fitted to the hour labels above the graph; None if not found."""
    ws = [w for w in page.get_text("words") if w[0] > 360 and w[1] < 600 and w[4].isdigit() and len(w[4]) <= 2
          and w[3] - w[1] > 8]
    rows = Counter(round(w[1]) for w in ws)
    best = None
    for y, n in rows.most_common(5):
        pts = sorted(((w[0] + w[2]) / 2, int(w[4])) for w in ws if abs(round(w[1]) - y) <= 3)
        vals = [v for _, v in pts]
        if len(set(vals)) != len(vals):
            continue
        if len(pts) >= 6 and all(b_ - a_ == 1 for a_, b_ in zip(vals, vals[1:])):
            best = pts
            break
    if not best:
        return None
    n = len(best)
    mx = sum(x for x, _ in best) / n
    mv = sum(v for _, v in best) / n
    b = sum((x - mx) * (v - mv) for x, v in best) / sum((x - mx) ** 2 for x, _ in best)
    return mv - b * mx, b


def closed_pass_marks(page, x_min=375):
    """'=' marks on train lines (user): two short parallel strokes across a train line = the train passes a station
    that is closed (dinas tutup). Returns [(x, y)] mark centres."""
    segs = []
    for dr in page.get_drawings():
        w = dr.get("width") or 0
        if dr["type"] == "s" and 0.6 < w < 1.2 and dr["rect"].x0 >= x_min and dr["rect"].x1 <= 1640:
            for it in dr["items"]:
                if it[0] != "l":
                    continue
                a, b = it[1], it[2]
                dx, dy = b.x - a.x, b.y - a.y
                L = (dx * dx + dy * dy) ** 0.5
                if 4 < L < 13 and abs(dy) < 0.6 * L:
                    segs.append(((a.x + b.x) / 2, (a.y + b.y) / 2, dy / dx if dx else 99))
    segs.sort()
    out, used = [], set()
    for i, s in enumerate(segs):
        if i in used:
            continue
        for j in range(i + 1, len(segs)):
            t = segs[j]
            if t[0] - s[0] > 4.5:
                break
            if j not in used and abs(t[1] - s[1]) < 3.5 and abs(t[2] - s[2]) < 0.15 and (t[0] - s[0]) ** 2 + (t[1] - s[1]) ** 2 > 1:
                used |= {i, j}
                out.append(((s[0] + t[0]) / 2, (s[1] + t[1]) / 2))
                break
    # outlined pages (35-37): the marks are rotated '=' characters (ArialMT) on the line, not drawings
    for w in page.get_text("words"):
        if w[4] == "=" and w[0] >= x_min and w[2] <= 1640:
            c = ((w[0] + w[2]) / 2, (w[1] + w[3]) / 2)
            if all(abs(c[0] - o[0]) > 3 or abs(c[1] - o[1]) > 3 for o in out):
                out.append(c)
    return out


GROUPS = [(7, 12), (13, 18), (19, 24), (25, 27), (28, 28), (29, 31), (32, 34), (35, 37), (38, 40), (41, 43), (44, 46),
          (47, 49), (50, 51)]


def grid_axes(page, pno):
    """Time axes of a graph page from its hour grid lines: [dict(y0, y1, x0, spacing, h0)], time in hours at x =
    h0 + (x - x0) / spacing within the band y0..y1. A page covers 24 h / pages in its group; page 28 holds two graphs
    (top 0-12 h, bottom 12-24 h)."""
    import statistics
    g = next(g for g in GROUPS if g[0] <= pno <= g[1])
    n = g[1] - g[0] + 1
    lines = [dr["rect"] for dr in page.get_drawings() if dr["type"] == "s" and dr["rect"].width < 1.5
             and dr["rect"].height > 200 and 340 < dr["rect"].x0 < 1700]
    bands = {}
    for r in lines:
        key = next((k for k in bands if abs(k[0] - r.y0) < 5 and abs(k[1] - r.y1) < 5), None)
        bands.setdefault(key or (r.y0, r.y1), []).append(r.x0)
    bands = sorted((k, sorted(set(round(x, 1) for x in v))) for k, v in bands.items() if len(set(round(x) for x in v)) >= 4)
    out = []
    for bi, ((y0, y1), xs) in enumerate(bands):
        diffs = [b - a for a, b in zip(xs, xs[1:]) if b - a > 40]
        s = statistics.median(diffs)
        offs = [x - s * round((x - 370) / s) for x in xs]
        x0 = statistics.median(offs)
        if len(bands) == 2 and n == 1:
            h0 = 12 * bi
        else:
            h0 = (pno - g[0]) * 24 / n
        out.append(dict(y0=y0, y1=y1, x0=x0, spacing=s, h0=h0))
    return out


def ctc_rows(page, rows):
    """CTC stations (user: station names in dashed boxes) by geometry only, for pages whose names are outlines (the row
    names come from OCR): a dashed box in the name column on a station row's line. rows = [(code, y)]. (Loose dash runs
    are not used: on the text pages every CTC station has a real dashed box, and dash runs hit halts' dashed rows.)"""
    boxes, segs = [], []
    for x in page.get_drawings():
        rr = x["rect"]
        if x["type"] == "s" and 270 < rr.x0 < 360 and 15 < rr.width < 120 and 6 < rr.height < 25 \
                and x.get("dashes") and re.search(r"\[\s*[\d.]", x["dashes"]):
            boxes.append(rr)
        if x["type"] != "s":
            continue                      # outlined glyphs are fills: their edges are not dashes
        for it in x["items"]:
            if it[0] == "l":
                a, b = it[1], it[2]
                if 270 < min(a.x, b.x) < 360 and abs(a.x - b.x) < 6 and abs(a.y - b.y) < 6:
                    segs.append(((a.x + b.x) / 2, (a.y + b.y) / 2))
    out = {}
    for code, y in rows:
        if code.startswith("?"):
            continue
        if any(rr.y0 - 4 < y < rr.y1 + 4 for rr in boxes):
            out[code] = page.number + 1
            continue
    return out
