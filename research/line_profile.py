"""Extract the infrastructure columns from the left margin of every GAPEKA graph sheet.

Per adjacent station pair (as drawn on the sheet):
  km length (from 'Letak Stasiun di KM'), general speed 'Umum', local restrictions 'Antara KM / Dengan KM / Dibatasi Sampai'
  (with hu/hi track tags), elevation at both ends (profile column, interpolated)  -> line_profile.json
"""
import difflib
import json
import re
import sqlite3
import statistics
import sys

import numpy as np
import pymupdf

KM = re.compile(r"^\d{1,3}\.\d{3}$")
TAG = re.compile(r"^(hu|hi|Hu|Hi|HU|HI)([-/ ]?(hu|hi|Hu|Hi))?$")
INT = re.compile(r"^\d{1,4}$")

db = sqlite3.connect("/home/user/Gapeka-parser/gapeka.db")
NAMES = {}
for code, name in db.execute("select code,name from stations"):
    NAMES.setdefault(re.sub(r"[^a-z]", "", (name or "").lower()), code)


def cy(w):
    return (w[1] + w[3]) / 2


_OCR = None


def match_name(t):
    k = re.sub(r"[^a-z]", "", t.lower())
    if k in NAMES:
        return NAMES[k]
    m = difflib.get_close_matches(k, list(NAMES), n=1, cutoff=0.86)
    return NAMES[m[0]] if m else "?" + k


def ocr_margin(page, x1=430, tile=500, z=4):
    """OCR the left margin (pages whose names/numbers are drawn as outlines); returns word tuples + text lines"""
    global _OCR
    if _OCR is None:
        from rapidocr_onnxruntime import RapidOCR
        _OCR = RapidOCR()
    words, lines, seen = [], [], set()
    H = page.rect.height
    y = 0.0
    while y < H:
        clip = pymupdf.Rect(0, y, x1, min(H, y + tile + 20))
        pix = page.get_pixmap(matrix=pymupdf.Matrix(z, z), clip=clip)
        img = np.frombuffer(pix.samples, np.uint8).reshape(pix.h, pix.w, pix.n)[:, :, :3]
        for box, txt, conf in (_OCR(img)[0] or []):
            bx0, by0 = clip.x0 + box[0][0] / z, clip.y0 + box[0][1] / z
            bx1, by1 = clip.x0 + box[2][0] / z, clip.y0 + box[2][1] / z
            key = (round(bx0), round((by0 + by1) / 2))
            if key in seen or any(abs(key[0] - a) <= 2 and abs(key[1] - b) <= 2 for a, b in seen):
                continue
            seen.add(key)
            lines.append((bx0, by0, bx1, by1, txt.strip()))
            parts = txt.split()
            n = max(len(txt), 1)
            pos = 0
            for p in parts:
                i = txt.index(p, pos)
                pos = i + len(p)
                words.append((bx0 + (bx1 - bx0) * i / n, by0, bx0 + (bx1 - bx0) * pos / n, by1, p))
        y += tile
    return words, lines


def page_profile(page, use_ocr=False):
    ws = page.get_text("words")
    left = [w for w in ws if w[0] < 420]
    # ---- station rows: any text line in the name column that has a km value beside it
    lines = []
    if use_ocr:
        ow, ol = ocr_margin(page)
        left = ow
        ws = [w for w in ws if w[0] >= 420] + ow
        for x0, y0, x1, y1, t in ol:
            t = t.replace("*", "").strip()
            if 260 < x0 < 380 and re.match(r"^[A-Za-z][A-Za-z .()-]*$", t) and len(t) >= 3 and "berlaku" not in t.lower():
                lines.append((match_name(t), (y0 + y1) / 2, x0))
    else:
        for b in page.get_text("dict")["blocks"]:
            for l in b.get("lines", []):
                t = "".join(s["text"] for s in l["spans"]).replace("*", "").strip()
                k = re.sub(r"[^a-z]", "", t.lower())
                x0 = l["bbox"][0]
                if 260 < x0 < 380 and len(k) >= 3 and re.match(r"^[A-Za-z][A-Za-z .()-]*$", t) and "berlaku" not in k:
                    lines.append((NAMES.get(k, "?" + k), (l["bbox"][1] + l["bbox"][3]) / 2, x0))
    known = [x for c, _, x in lines if not c.startswith("?")]
    if len(known) < 2:
        return None
    name_x = statistics.median(known)
    kms = [w for w in left if KM.match(w[4]) and w[2] < name_x - 3]
    if not kms:
        return None
    from collections import Counter
    cnt = Counter(round(w[0] / 4) for w in kms)
    cols = sorted(c for c, n in cnt.items() if n >= max(3, 0.4 * len(known)))
    if not cols:
        return None
    letak_x = statistics.median(w[0] for w in kms if round(w[0] / 4) == cols[-1])
    letak = [w for w in kms if abs(w[0] - letak_x) < 6]
    dist_col = [w for w in kms if 10 < letak_x - w[0] < 40]
    dx = statistics.median(w[0] for w in dist_col) if dist_col else letak_x - 22
    # station rows with their km candidates
    rows = []
    for code, y, x in lines:
        if abs(x - name_x) > 15:
            continue
        c = [float(w[4]) for w in letak if -7 < cy(w) - y < 11]
        if c:
            rows.append(dict(code=code, y=y, km=c))
    rows.sort(key=lambda r: r["y"])
    # ---- restrictions: km tokens left of the distance column, paired by order; speed to their right
    tg = sorted([w for w in kms if w[0] < dx - 15], key=cy)
    rx = statistics.median(w[2] for w in tg) if tg else None
    ints = [w for w in left if INT.match(w[4]) and rx is not None and rx < w[0] < dx - 2 and 10 <= int(w[4]) <= 160 and int(w[4]) % 5 == 0]
    # two columns between the restriction km and the distance column: 'Dibatasi Sampai' (left) and 'Umum' (right)
    xs = sorted({round(w[0]) for w in ints})
    split = None
    if xs:
        gaps = [(b2 - a2, (a2 + b2) / 2) for a2, b2 in zip(xs, xs[1:])]
        g = max(gaps, default=(0, None))
        if g[0] >= 6:
            split = g[1]
    dib = [w for w in left if w[4] == "Dibatasi" and rx is not None and w[0] > rx - 5]
    umh = [w for w in left if w[4] in ("Umum", "U") and rx is not None and w[0] > rx]
    if split is None and xs:
        # one column only: decide by distance to the restriction km column
        split = (rx + 14) if not umh else (umh[0][0] - 3)
    speeds = [w for w in ints if split is not None and w[0] < split]
    umum = [(cy(w), int(w[4])) for w in ints if split is not None and w[0] >= split]
    restr = []
    used = set()
    # anchor on each speed value: the restriction's start km is the nearest unused km printed above it,
    # the end km the nearest unused km printed below it (works for short and for long, e.g. 10 km, restrictions)
    for s2 in sorted(speeds, key=cy):
        # candidate (start, end) = consecutive unused km tokens around the speed; the speed is printed midway
        # between its own start and end, so take the pair whose midpoint is closest to it (Madiun: 166.100/165.900
        # at 40 and 160.000/158.000 at 90 must not cross-pair)
        free = sorted((w for w in tg if w not in used), key=cy)
        cands = [(free[i], free[i + 1]) for i in range(len(free) - 1)
                 if cy(free[i]) <= cy(s2) + 3 and cy(free[i + 1]) >= cy(s2) - 3]
        if not cands:
            continue
        a2, b2 = min(cands, key=lambda p: abs((cy(p[0]) + cy(p[1])) / 2 - cy(s2)) + 0.05 * (cy(p[1]) - cy(p[0])))
        if cy(b2) - cy(a2) > 600:
            continue
        tags = [t[4].lower() for t in left if TAG.match(t[4]) and abs(cy(t) - cy(a2)) < 1.6 and 0 <= t[0] - a2[2] < 12]
        if cy(b2) - cy(a2) > 150:
            # long restriction (e.g. Tarik-Sepanjang, 31 km): speed printed near the middle, same track tag at both ends
            tags_b = [t[4].lower() for t in left if TAG.match(t[4]) and abs(cy(t) - cy(b2)) < 1.6 and 0 <= t[0] - b2[2] < 12]
            mid = (cy(a2) + cy(b2)) / 2
            if not tags or sorted(tags) != sorted(tags_b) or abs(cy(s2) - mid) > 0.2 * (cy(b2) - cy(a2)):
                continue
        # small printed note inside the restriction cell, e.g. "Jalur IV, 5, 6 Empl. MLK" (station tracks only) or "Empl. Bd"
        note = " ".join(t[4] for t in sorted(left, key=lambda t: (round(cy(t)), t[0]))
                        if cy(a2) - 6 <= cy(t) <= cy(b2) + 6 and a2[0] - 4 <= t[0] <= s2[2] + 4
                        and re.search(r"[A-Za-z]", t[4]) and not TAG.match(t[4]))
        restr.append(dict(y=(cy(a2) + cy(b2)) / 2, y0=cy(a2), y1=cy(b2), km0=float(a2[4]), km1=float(b2[4]), v=int(s2[4]),
                          tags=" ".join(tags), note=note))
        used.add(a2)
        used.add(b2)
    hl = []
    if umum:
        ux0 = min(w[0] for w in ints if w[0] >= split) - 3
        for d in page.get_drawings():
            for it in d["items"]:
                if it[0] == "l" and abs(it[1].y - it[2].y) < .3 and min(it[1].x, it[2].x) <= ux0 + 4 and max(it[1].x, it[2].x) >= ux0 + 8:
                    hl.append(it[1].y)
    hl = sorted(set(round(y, 1) for y in hl))

    def umum_at(y):
        lo = max([h for h in hl if h <= y] or [-1e9])
        hi = min([h for h in hl if h > y] or [1e9])
        c = [v for yy, v in umum if lo <= yy <= hi]
        return c[0] if c else None
    # ---- elevation: profile column, integer tokens between the first gradient column and the curve column
    hdr = sorted([w for w in ws if w[4] == "Lereng"], key=lambda w: w[0])
    elev = []
    if len(hdr) >= 2:
        x_lo, x_hi = hdr[0][2] + 4, hdr[-1][0] - 2
        elev = sorted((cy(w), int(w[4])) for w in left if INT.match(w[4]) and x_lo < w[0] < x_hi and int(w[4]) < 2500)

    def elev_at(y):
        if len(elev) < 2:
            return None
        below = [p for p in elev if p[0] <= y]
        above = [p for p in elev if p[0] > y]
        if not below or not above:
            e = min(elev, key=lambda p: abs(p[0] - y))
            return e[1] if abs(e[0] - y) < 12 else None
        (y0, h0), (y1, h1) = below[-1], above[0]
        if y1 - y0 > 300:
            return None
        return round(h0 + (h1 - h0) * (y - y0) / (y1 - y0), 1)
    # ---- block signals: "B-0715 km 330.500 hi" in the station-name column (signal number, km, track)
    blocks = []
    byline = {}
    for w in left:
        if 265 < w[0] < 420:
            byline.setdefault(round(cy(w) / 2.5), []).append(w)
    for k, ws_ in byline.items():
        txt = " ".join(t[4] for t in sorted(ws_, key=lambda t: t[0]))
        found = False
        for m in re.finditer(r"B[-.]?\s*(\d{1,5})\s*km\s*(\d{1,3}\.\d{3})\s*(hu|hi)", txt, re.I):
            blocks.append(dict(y=k * 2.5, no=m.group(1), km=float(m.group(2)), tag=m.group(3).lower()))
            found = True
        if not found:
            # block signal without a km value ("B.hu", "B.hi", "B.hu - B.hi"): position from its place on the sheet
            tags = re.findall(r"\bB\s*[.\-]\s*(hu|hi)\b", txt, re.I)
            for tg in tags:
                blocks.append(dict(y=k * 2.5, no="", km=None, tag=tg.lower()))
    # ---- pairs of adjacent stations
    out = []
    for A, B in zip(rows, rows[1:]):
        if B["y"] - A["y"] > 400:
            continue
        # choose km candidates whose difference best matches a distance-column value between the rows
        dd = [float(w[4]) for w in dist_col if A["y"] - 2 < cy(w) < B["y"] + 2]
        best = None
        for ka in A["km"]:
            for kb in B["km"]:
                L = abs(kb - ka)
                err = min((abs(L - x) for x in dd), default=0 if L < 30 else 99)
                if 0.2 < L < 60 and (best is None or (err, L) < (best[0], abs(best[2] - best[1]))):
                    best = (err, ka, kb)
        # the distance column skips halts (station-to-station), so it only decides between two km values at a
        # chainage change; when both rows have a single km value the km positions are trusted directly
        if best is None or (best[0] > 0.05 and (len(A["km"]) > 1 or len(B["km"]) > 1)):
            continue
        _, ka, kb = best
        rs = []
        for r in restr:
            if r["y0"] <= B["y"] + 4 and r["y1"] >= A["y"] - 4:
                sg = 1 if kb > ka else -1
                L = abs(kb - ka)
                o0, o1 = sorted(((r["km0"] - ka) * sg, (r["km1"] - ka) * sg))
                o0, o1 = max(o0, 0.0), min(o1, L)
                if o1 - o0 < 0.02 or o1 - o0 > 60:
                    continue
                rs.append(dict(o0=round(o0, 3), o1=round(o1, 3), v=r["v"], tags=r["tags"], up=sg > 0, note=r.get("note", "")))
        u = umum_at((A["y"] + B["y"]) / 2)
        sg = 1 if kb > ka else -1
        bl = []
        Lp = abs(kb - ka)
        for b in blocks:
            if A["y"] - 6 <= b["y"] <= B["y"] + 6:
                frac = min(max((b["y"] - A["y"]) / max(B["y"] - A["y"], 1e-6), 0.0), 1.0)
                o_y = frac * Lp                                  # offset from the sheet position
                if b["km"] is not None:
                    o = (b["km"] - ka) * sg
                    if -0.05 <= o <= Lp + 0.05:
                        bl.append(dict(o=round(o, 3), tag=b["tag"], no=b["no"], o_from_y=round(o_y, 3), src="km"))
                elif 0 < frac < 1:
                    bl.append(dict(o=round(o_y, 3), tag=b["tag"], no="", src="sheet position"))
        # automatic block signals have no km on the sheet (user): space them evenly over the section, per track
        for tg in ("hu", "hi"):
            auto = sorted((b for b in bl if b["src"] == "sheet position" and b["tag"] == tg), key=lambda b: b["o"])
            for i, b in enumerate(auto, 1):
                b["o_sheet"] = b["o"]
                b["o"] = round(Lp * i / (len(auto) + 1), 3)
                b["src"] = "automatic, evenly spaced"
        out.append(dict(a=A["code"], b=B["code"], up=kb > ka, km=round(abs(kb - ka), 3), umum=u, restr=rs,
                        h_a=elev_at(A["y"]), h_b=elev_at(B["y"]), blocks=bl))
    return out


if __name__ == "__main__":
    doc = pymupdf.open("/home/user/Gapeka-parser/GAPEKA-JAWA-2026.pdf")
    pages = [int(a) for a in sys.argv[1:]] or [i for i in range(doc.page_count) if doc[i].rect.width > 1000]
    prof = {}
    for i in pages:
        pr = page_profile(doc[i]) or []
        if len(pr) < 5:
            pr = page_profile(doc[i], use_ocr=True) or []
            print(f"   (OCR used on PDF page {i + 1})")
        for p in pr:
            p["page"] = i + 1
            prof.setdefault(f"{p['a']}-{p['b']}", p)
        print(f"PDF page {i + 1}: {len(pr)} station pairs, {sum(len(p['restr']) for p in pr)} restrictions, "
              f"{sum(p['umum'] is not None for p in pr)} with Umum, {sum(p['h_a'] is not None and p['h_b'] is not None for p in pr)} with elevation")
    # join pairs through stops that are on the sheet but not in the timetable (e.g. Bangsal, Kumendung)
    added = 0
    for k1, p1 in list(prof.items()):
        a, m = k1.split("-")
        if not m.startswith("?"):
            continue
        for k2, p2 in list(prof.items()):
            m2, b = k2.split("-")
            if m2 != m or b.startswith("?") or f"{a}-{b}" in prof or f"{b}-{a}" in prof:
                continue
            L1, L2 = p1["km"], p2["km"]
            u1, u2 = p1["umum"], p2["umum"]
            rs = list(p1["restr"]) + [dict(r, o0=round(r["o0"] + L1, 3), o1=round(r["o1"] + L1, 3)) for r in p2["restr"]]
            umum = max(x for x in (u1, u2) if x) if (u1 or u2) else None
            if u1 and umum and u1 < umum:
                rs.append(dict(o0=0.0, o1=L1, v=u1, tags="", up=p1["up"]))
            if u2 and umum and u2 < umum:
                rs.append(dict(o0=L1, o1=L1 + L2, v=u2, tags="", up=p1["up"]))
            prof[f"{a}-{b}"] = dict(a=a, b=b, up=p1["up"], km=round(L1 + L2, 3), umum=umum, restr=rs,
                                    h_a=p1["h_a"], h_b=p2["h_b"], page=p1.get("page"), via=m[1:])
            added += 1
    print("pairs joined through stops not in the timetable:", added)
    json.dump(prof, open("line_profile.json", "w"), indent=0)
    print("unique pairs", len(prof))
