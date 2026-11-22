"""Section symbols in the strip between the station-name column and the graph (graph sheets, left side).

Each section between two station rows is filled with a column of small circles (user):
  open circle with a horizontal bar  = closed automatic block (blok otomatis tertutup)
  open circle with a vertical bar    = open automatic block (blok otomatis terbuka)
  solid black circle                 = electromechanical block
  zigzag "S" inside the circle       = electrified line (combined with the block symbol)

The symbols are read from the vector drawing: a circle path (4+ Bezier pieces, 4-9.5 pt, square box) in the page's
symbol column (the x where most such circles sit), its fill, and the line pieces inside it.
"""
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
    circle (Jakarta Kota-Jayakarta, Bogor-Batutulis).
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
