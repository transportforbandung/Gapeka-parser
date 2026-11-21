"""Build the infrastructure database (infra.db) from the GAPEKA graph sheets.

    python build_infra.py [--pdf GAPEKA-JAWA-2026.pdf] [--timetable gapeka.db] [--out infra.db] [--manual manual]

Reads the left margin and the header of every graph sheet (PDF pages 7-51 in the 2026 Java GAPEKA):
  - station rows: name, km position ("Letak Stasiun di KM"), elevation (profile column)
  - per adjacent station pair: length, general speed ("Umum"), ruling gradient ("Lereng Penentu"),
    local restrictions ("Antara KM / Dengan KM / Dibatasi Sampai", with hulu/hilir tags and cell notes),
    block signals (with km, or automatic ones without km, spaced evenly)
  - header: line title, double-track stretches ("JALUR GANDA"), dispatching stations (raw text)
  - stations drawn in dashed boxes = centrally controlled (CTC)
Pages whose margin text is drawn as outlines are read with OCR (rapidocr-onnxruntime, required); OCR results are
cached in .cache/ocr so later runs are fast.

The timetable database (gapeka.db, built by gapeka_pipeline.py) is needed for station codes and names, and to check
section lengths against running times. Manual corrections come from CSV files in manual/ and are stored with
source = 'manual'.

Direction convention: every section is stored in sheet order, a = upper row, b = lower row. The HULU track is used
by trains moving from the top of the sheet to the bottom (a -> b), the HILIR track by trains moving b -> a.
"""
import argparse
import csv
import datetime
import difflib
import hashlib
import json
import os
import re
import sqlite3
import statistics
import sys
from collections import Counter, defaultdict, deque

import numpy as np
import pymupdf

KM = re.compile(r"^\d{1,3}\.\d{3}$")
TAG = re.compile(r"^(hu|hi|Hu|Hi|HU|HI)([-/ ]?(hu|hi|Hu|Hi))?$")
INT = re.compile(r"^\d{1,4}$")
SIGNAL = re.compile(r"^\s*B\s*[.\-]?\s*(hu|hi|\d)", re.I)     # block-signal markers in the name column
CHAIN = re.compile(r"^[A-Za-z]{1,5}(\s*-\s*[A-Za-z]{1,5})+(\s*,\s*[A-Za-z]{1,5}(\s*-\s*[A-Za-z]{1,5})+)*,?$")

SCHEMA = """
CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE lines(line_id TEXT PRIMARY KEY, title TEXT, pages TEXT, header TEXT);
CREATE TABLE stations(code TEXT PRIMARY KEY, name TEXT, kind TEXT, ctc INTEGER, sheet_name TEXT, source TEXT);
CREATE TABLE line_stations(line_id TEXT, seq INTEGER, code TEXT, km REAL, km_alt REAL, elevation_m REAL, page INTEGER,
                           PRIMARY KEY(line_id, seq));
CREATE TABLE sections(a TEXT, b TEXT, line_id TEXT, km REAL, km_a REAL, km_b REAL, km_increasing INTEGER,
                      umum_kmh INTEGER, ruling_gradient_permil REAL, elevation_a_m REAL, elevation_b_m REAL,
                      double_track INTEGER, double_track_source TEXT, via TEXT, source TEXT, page INTEGER,
                      variant TEXT DEFAULT '', context TEXT, PRIMARY KEY(a, b, variant));
CREATE TABLE restrictions(a TEXT, b TEXT, offset_from_km REAL, offset_to_km REAL, km_from REAL, km_to REAL,
                          speed_kmh INTEGER, tracks TEXT, note TEXT, source TEXT, page INTEGER, variant TEXT DEFAULT '');
CREATE TABLE block_signals(a TEXT, b TEXT, offset_km REAL, track TEXT, signal_no TEXT, km REAL, kind TEXT, page INTEGER,
                           variant TEXT DEFAULT '');
CREATE TABLE double_track_header(line_id TEXT, page INTEGER, chain TEXT, codes TEXT, resolved INTEGER);
CREATE TABLE qa(check_name TEXT, item TEXT, detail TEXT);
CREATE VIEW section_lookup AS
  SELECT a AS from_st, b AS to_st, *, 'hu' AS track FROM sections
  UNION ALL SELECT b AS from_st, a AS to_st, *, 'hi' AS track FROM sections;
"""


def cy(w):
    return (w[1] + w[3]) / 2


def key_of(name):
    return re.sub(r"[^a-z]", "", (name or "").lower())


# ------------------------------------------------------------------------------------------------ station names
class Names:
    """sheet name -> timetable station code (timetable names, plus aliases from manual/station_aliases.csv)"""

    def __init__(self, ttdb, manual_dir):
        self.by_key = {}
        self.names = {}
        self.multi = defaultdict(list)        # names used by more than one code (Karangsari KRR / KRAI, Larangan)
        for code, name in ttdb.execute("select code,name from stations"):
            self.by_key.setdefault(key_of(name), code)
            self.multi[key_of(name)].append(code)
            self.names[code] = name
        self.aliases = {}
        p = os.path.join(manual_dir, "station_aliases.csv")
        if os.path.exists(p):
            for r in csv.DictReader(open(p)):
                self.by_key[key_of(r["sheet_name"])] = r["code"]
                self.aliases[r["code"]] = r["sheet_name"]

    def exact(self, t):
        return self.by_key.get(key_of(t))

    def fuzzy(self, t):
        k = key_of(t)
        if k in self.by_key:
            return self.by_key[k]
        m = difflib.get_close_matches(k, list(self.by_key), n=1, cutoff=0.86)
        return self.by_key[m[0]] if m else "?" + k


# ------------------------------------------------------------------------------------------------ OCR (cached)
class OCR:
    def __init__(self, pdf_hash, cache_dir=".cache/ocr"):
        self.engine = None
        self.cache_dir = cache_dir
        self.pdf_hash = pdf_hash

    def _engine(self):
        if self.engine is None:
            try:
                from rapidocr_onnxruntime import RapidOCR
            except ImportError:
                sys.exit("OCR is required for graph pages drawn as outlines: pip install rapidocr-onnxruntime")
            self.engine = RapidOCR()
        return self.engine

    def read(self, page, clip_rect, tile=500, z=4, tag="margin"):
        """OCR a page region in horizontal tiles; returns (words, lines) as (x0, y0, x1, y1, text) in PDF points"""
        os.makedirs(self.cache_dir, exist_ok=True)
        cpath = os.path.join(self.cache_dir, f"{self.pdf_hash[:16]}_p{page.number + 1}_{tag}.json")
        if os.path.exists(cpath):
            lines = [tuple(x) for x in json.load(open(cpath))]
        else:
            eng = self._engine()
            lines, seen = [], []
            y = clip_rect.y0
            while y < clip_rect.y1:
                clip = pymupdf.Rect(clip_rect.x0, y, clip_rect.x1, min(clip_rect.y1, y + tile + 20))
                pix = page.get_pixmap(matrix=pymupdf.Matrix(z, z), clip=clip)
                img = np.frombuffer(pix.samples, np.uint8).reshape(pix.h, pix.w, pix.n)[:, :, :3]
                for box, txt, conf in (eng(img)[0] or []):
                    bx0, by0 = clip.x0 + box[0][0] / z, clip.y0 + box[0][1] / z
                    bx1, by1 = clip.x0 + box[2][0] / z, clip.y0 + box[2][1] / z
                    k = (round(bx0), round((by0 + by1) / 2))
                    if any(abs(k[0] - a) <= 2 and abs(k[1] - b) <= 2 for a, b in seen):
                        continue                         # overlap between tiles
                    seen.append(k)
                    lines.append((bx0, by0, bx1, by1, txt.strip()))
                y += tile
            json.dump(lines, open(cpath, "w"))
        words = []
        for bx0, by0, bx1, by1, txt in lines:
            n, pos = max(len(txt), 1), 0
            for p in txt.split():
                i = txt.index(p, pos)
                pos = i + len(p)
                words.append((bx0 + (bx1 - bx0) * i / n, by0, bx0 + (bx1 - bx0) * pos / n, by1, p))
        return words, lines


def make_filler(ocr):
    def fill(page, y, name_x):
        words, lines = ocr.read(page, pymupdf.Rect(name_x - 25, y - 10, name_x + 130, y + 8), tile=40, z=6,
                                tag=f"row{int(y)}")
        t = " ".join(l[4] for l in sorted(lines, key=lambda l: l[0]) if l[4].strip())
        t = re.sub(r"B\s*[.\-]?\s*h[ui]\b", " ", t)        # automatic block-signal markers in the row
        if re.search(r"\bkm\b", t, re.I):
            return None                                       # a block signal with km ("B-2210 km 446.352"): no name
        return t.strip() or None
    return fill


def make_km_filler(ocr):
    """km value of a station row that has a name but no km token (italic km of halts, OCR pages: Karangtengah)"""
    def fill(page, y, letak_x, taken=()):
        words, lines = ocr.read(page, pymupdf.Rect(letak_x - 6, y - 15, letak_x + 40, y + 15), tile=60, z=6,
                                tag=f"kmcol{int(y)}")
        found = []
        for l in lines:
            m = re.search(r"\d{1,3}[.,]\d{3}", l[4])
            if m:
                v = float(m.group(0).replace(",", "."))
                if all(abs(v - t) > 0.0005 for t in taken):     # not the km of a neighbouring row
                    found.append((abs((l[1] + l[3]) / 2 - y), v))
        return min(found)[1] if found else None
    return fill


def clean_ocr_name(t):
    """OCR adds marks from the dashed CTC boxes ("I Sumpiuh !", ": Karanganyar :") and full-width letters"""
    import unicodedata
    t = unicodedata.normalize("NFKC", t).replace("*", " ")
    if SIGNAL.match(t):
        return t.strip()
    t = re.sub(r"[^A-Za-z .()-]", " ", t)
    if re.fullmatch(r"\s*([A-Za-z]\s+){2,}[A-Za-z]\s*", t):
        t = t.replace(" ", "")                   # letters printed with wide spacing ("I j o")
    toks = t.split()
    while toks and len(toks[0]) <= 2 and len(toks) > 1:
        toks.pop(0)
    while toks and len(toks[-1]) <= 1 and len(toks) > 1:
        toks.pop()
    return " ".join(toks)


# ------------------------------------------------------------------------------------------------ one graph page
def page_profile(page, names, ocr=None, filler=None, km_filler=None):
    """Read the left margin of one graph page. Returns dict(rows=[station rows], pairs=[adjacent pairs]) or None."""
    ws = page.get_text("words")
    left = [w for w in ws if w[0] < 420]
    lines = []
    if ocr is not None:
        ow, ol = ocr.read(page, pymupdf.Rect(0, 0, 430, page.rect.height))
        left = ow
        ws = [w for w in ws if w[0] >= 420] + ow
        for x0, y0, x1, y1, t in ol:
            t2 = clean_ocr_name(t)
            if 260 < x0 < 380 and re.match(r"^[A-Za-z][A-Za-z .()-]*$", t2) and len(t2) >= 3 \
                    and "berlaku" not in t2.lower() and not SIGNAL.match(t2):
                lines.append((names.fuzzy(t2), (y0 + y1) / 2, x0, t2))
    else:
        # names from the words of the name column, joined per row (a PDF text line can also hold a km value)
        rowsw = {}
        for w in ws:
            if 260 < w[0] < 380 and w[4] != "*":
                rowsw.setdefault(round(cy(w) / 2.5), []).append(w)
        for k in sorted(rowsw):
            wsr = sorted(rowsw[k], key=lambda w: w[0])
            t = " ".join(w[4] for w in wsr).replace("*", "").strip()
            kk = key_of(t)
            if len(kk) >= 3 and re.match(r"^[A-Za-z][A-Za-z .()-]*$", t) and "berlaku" not in kk and not SIGNAL.match(t):
                code, x0 = names.exact(t), wsr[0][0]
                if not code:
                    # other text can share the row (Bandung: a vertical label "GELANG RAJA ..." in the name
                    # column): take the longest run of words that is a station name
                    for n in range(len(wsr) - 1, 0, -1):
                        hit = [(i, names.exact(" ".join(w[4] for w in wsr[i:i + n]))) for i in range(len(wsr) - n + 1)]
                        hit = [(i, c) for i, c in hit if c]
                        if hit:
                            i, code = hit[0]
                            t, x0 = " ".join(w[4] for w in wsr[i:i + n]), wsr[i][0]
                            break
                lines.append((code or "?" + kk, cy(wsr[0]), x0, t))
    # names printed over two rows ("Bandara" / "Soekarno Hatta"): join unknown neighbours that match together
    lines.sort(key=lambda t: t[1])
    merged = []
    for ln_ in lines:
        if merged and ln_[0].startswith("?") and merged[-1][0].startswith("?") and 0 < ln_[1] - merged[-1][1] < 14:
            t = merged[-1][3] + " " + ln_[3]
            code = names.exact(t)
            if code:
                merged[-1] = (code, merged[-1][1], merged[-1][2], t)
                continue
        merged.append(ln_)
    lines = merged
    known = [x for c, _, x, _ in lines if not c.startswith("?")]
    if len(known) < 2:
        return None
    name_x = statistics.median(known)
    kms = [w for w in left if KM.match(w[4]) and w[2] < name_x - 3]
    if not kms:
        return None
    cnt = Counter(round(w[0] / 4) for w in kms)
    cols = sorted(c for c, n in cnt.items() if n >= max(3, 0.4 * len(known)))
    if not cols:
        return None
    letak_x = statistics.median(w[0] for w in kms if round(w[0] / 4) == cols[-1])
    letak = [w for w in kms if abs(w[0] - letak_x) < 9]
    dist_col = [w for w in kms if 10 < letak_x - w[0] < 40]
    dx = statistics.median(w[0] for w in dist_col) if dist_col else letak_x - 22
    rows, kmless = [], []                 # kmless: station names without a km value beside them (Soka, page 36)
    for code, y, x, t in lines:
        if abs(x - name_x) > 15:
            continue
        c = [float(w[4]) for w in letak if -7 < cy(w) - y < 11]
        if not c and km_filler is not None and not code.startswith("?"):
            k = km_filler(page, y, letak_x, [float(w[4]) for w in letak])
            c = [k] if k is not None else []
        if c:
            rows.append(dict(code=code, sheet_name=t, y=y, km=c))
        elif not code.startswith("?"):
            kmless.append((y, code, t))
    # a km value in the Letak column without a station name beside it: the name is drawn as outlines (Tanjung on
    # page 29, Cilaku on page 28) or OCR missed it (Karangtengah, Ijo): OCR that name cell alone, at high zoom
    if filler is not None:
        for w in sorted(letak, key=cy):
            y = cy(w)
            if any(-11 < y - r["y"] < 7 for r in rows):
                continue
            row_kms = [float(v[4]) for v in letak if abs(cy(v) - y) < 3]
            txt = filler(page, y, name_x)
            t2 = clean_ocr_name(txt) if txt else ""
            ok = len(t2) >= 3 and not SIGNAL.match(t2) and re.match(r"^[A-Za-z][A-Za-z .()-]*$", t2)
            code = names.fuzzy(t2) if ok else "?unnamed"
            if not ok:
                # a station name without km just above this km value (Soka: name, then its signals, then the km)
                above = [k for k in kmless if 0 < y - k[0] < 32 and not any(r["code"] == k[1] for r in rows)]
                if above:
                    _, code, t2 = max(above)
                    ok = True
            near = [r for r in rows if r["code"] == code and abs(r["y"] - y) < 30]
            if near and ok:
                near[0]["km"] = sorted(set(near[0]["km"] + row_kms))     # second km of a junction (Krenceng): same station
                continue
            if ok and code.startswith("?") and not names.exact(t2):
                code = f"?unnamed@{int(y)}"                           # unreadable: resolved from the timetable
            rows.append(dict(code=code if ok else f"?unnamed@{int(y)}", sheet_name=t2 or "", y=y, km=row_kms, ocr_row=True))
        rows.sort(key=lambda r: r["y"])
    rows.sort(key=lambda r: r["y"])
    # a sheet-only row at the same km as the row above is a second name of the same place ("Ssp. Pusri" /
    # "Kaligunting" at km 145.600): keep one row
    merged_rows = []
    for r in rows:
        if merged_rows and r["code"].startswith("?") and merged_rows[-1]["code"].startswith("?") \
                and set(r["km"]) == set(merged_rows[-1]["km"]) and r["y"] - merged_rows[-1]["y"] < 30:
            continue
        merged_rows.append(r)
    rows = merged_rows

    # ---- restrictions: start/end km tokens, speed printed between them, hu/hi tags right of the start km
    tg = sorted([w for w in kms if w[0] < dx - 15], key=cy)
    rx = statistics.median(w[2] for w in tg) if tg else None
    ints = [w for w in left if INT.match(w[4]) and rx is not None and rx < w[0] < dx - 2
            and 10 <= int(w[4]) <= 160 and int(w[4]) % 5 == 0]
    xs = sorted({round(w[0]) for w in ints})               # 'Dibatasi Sampai' (left) and 'Umum' (right) columns
    split = None
    if xs:
        g = max([(b2 - a2, (a2 + b2) / 2) for a2, b2 in zip(xs, xs[1:])], default=(0, None))
        if g[0] >= 6:
            split = g[1]
    umh = [w for w in left if w[4] in ("Umum", "U") and rx is not None and w[0] > rx]
    if split is None and xs:
        split = (rx + 14) if not umh else (umh[0][0] - 3)
    speeds = [w for w in ints if split is not None and w[0] < split]
    umum = [(cy(w), int(w[4])) for w in ints if split is not None and w[0] >= split]
    # numbers of the curve column (the one labelled HILIR / HULU, e.g. "2.000" on page 38) look like km values;
    # they must not be paired as restriction ends
    curve_x = [w[0] for w in left if w[4].upper() in ("HILIR", "HULU")]
    if curve_x:
        cx = statistics.median(curve_x)
        tg = [w for w in tg if abs(w[0] - cx) > 6]
    restr, used = [], set()
    for s2 in sorted(speeds, key=cy):
        # the speed is printed midway between its own start and end km: take the pair of consecutive unused km
        # tokens around it whose midpoint is closest (keeps neighbouring restrictions apart, e.g. Madiun)
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
            # long restriction (e.g. Tarik-Sepanjang 31 km): same track tag at both ends, speed near the middle
            tags_b = [t[4].lower() for t in left if TAG.match(t[4]) and abs(cy(t) - cy(b2)) < 1.6 and 0 <= t[0] - b2[2] < 12]
            mid = (cy(a2) + cy(b2)) / 2
            if not tags or sorted(tags) != sorted(tags_b) or abs(cy(s2) - mid) > 0.2 * (cy(b2) - cy(a2)):
                continue
        note = " ".join(t[4] for t in sorted(left, key=lambda t: (round(cy(t)), t[0]))
                        if cy(a2) - 6 <= cy(t) <= cy(b2) + 6 and a2[0] - 4 <= t[0] <= s2[2] + 4
                        and re.search(r"[A-Za-z]", t[4]) and not TAG.match(t[4]))
        restr.append(dict(y0=cy(a2), y1=cy(b2), km0=float(a2[4]), km1=float(b2[4]), v=int(s2[4]),
                          tags=" ".join(tags), note=note))
        used.update((a2, b2))

    # ---- Umum cells are separated by horizontal rules
    hl = []
    if umum:
        ux0 = min(w[0] for w in ints if w[0] >= split) - 3
        for d in page.get_drawings():
            for it in d["items"]:
                if it[0] == "l" and abs(it[1].y - it[2].y) < 1.0 and min(it[1].x, it[2].x) <= ux0 + 4 \
                        and max(it[1].x, it[2].x) >= ux0 + 8:
                    hl.append(it[1].y)
                elif it[0] == "re" and it[1].x0 <= ux0 + 4 and it[1].x1 >= ux0 + 8:
                    hl.extend((it[1].y0, it[1].y1))       # cells drawn as rectangles (page 41: 120 cell ends at Blitar)
    hl = sorted(set(round(y, 1) for y in hl))

    def umum_at(y):
        lo = max([h for h in hl if h <= y] or [-1e9])
        hi = min([h for h in hl if h > y] or [1e9])
        c = [(abs(yy - y), v) for yy, v in umum if lo <= yy <= hi]
        return min(c)[1] if c else None

    # ---- ruling gradient ("Lereng Penentu", per mil): first column under the first "Lereng" header
    hdr = sorted([w for w in ws if w[4] == "Lereng"], key=lambda w: w[0])
    grad_tok, grad_hl = [], []
    gx = hdr[0][0] if hdr else None
    if gx is None:                                         # OCR pages: no readable header, leftmost number column
        xg = [w[0] for w in left if w[0] < 80 and re.match(r"^\d{1,2}([,.]\d)?$", w[4])]
        if len(xg) >= 3:
            gx = statistics.median(xg)
            hdr = [(gx, 0, gx + 10, 0, "")]
    if gx is not None:
        for w in left:
            if abs(w[0] - gx) < 9 and re.match(r"^\d{1,2}([,.]\d)?$", w[4]) and cy(w) > hdr[0][3] + 20:
                grad_tok.append((cy(w), float(w[4].replace(",", "."))))
        for d in page.get_drawings():
            for it in d["items"]:
                if it[0] == "l" and abs(it[1].y - it[2].y) < 1.0 and min(it[1].x, it[2].x) <= gx + 2 \
                        and max(it[1].x, it[2].x) >= gx + 8:
                    grad_hl.append(round(it[1].y, 1))
                elif it[0] == "re" and it[1].x0 <= gx + 2 and it[1].x1 >= gx + 8:
                    grad_hl.extend((round(it[1].y0, 1), round(it[1].y1, 1)))
        grad_hl = sorted(set(grad_hl))

    def grad_at(y0, y1):
        """length-weighted ruling gradient over the rows y0..y1 (a section can span several gradient cells)"""
        if not grad_tok:
            return None
        cuts = sorted({y0, y1} | {h for h in grad_hl if y0 < h < y1})
        tot = acc = 0.0
        for a_, b_ in zip(cuts, cuts[1:]):
            mid = (a_ + b_) / 2
            lo = max([h for h in grad_hl if h <= mid] or [-1e9])
            hi = min([h for h in grad_hl if h > mid] or [1e9])
            v = [g for yy, g in grad_tok if lo <= yy <= hi]
            if v:
                acc += (b_ - a_) * v[0]
                tot += b_ - a_
        return round(acc / tot, 1) if tot else None

    # ---- elevation (m): integers in the profile column, between the "Lereng" headers (left column group)
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

    # ---- block signals in the name column: "B-0715 km 330.500 hi" or automatic "B.hu - B.hi" (no km)
    blocks, byline = [], {}
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
            for t_ in re.findall(r"\bB\s*[.\-]\s*(hu|hi)\b", txt, re.I):
                blocks.append(dict(y=k * 2.5, no="", km=None, tag=t_.lower()))

    # ---- each line on the sheet has its own margin table; the table's left border is one vertical rule, so two
    #      rows belong to the same line when the same border segment spans both. No section crosses tables.
    vert = []
    for d in page.get_drawings():
        for it in d["items"]:
            if it[0] == "l" and abs(it[1].x - it[2].x) < .3 and abs(it[1].y - it[2].y) > 50 and 40 < it[1].x < 120:
                vert.append((round(it[1].x, 1), min(it[1].y, it[2].y), max(it[1].y, it[2].y)))
    vert = [v for v in vert if v[2] - v[1] < 0.9 * page.rect.height]   # not the page frame
    segs = []
    if vert:
        bx = min(Counter(v[0] for v in vert), key=lambda x: (x, 0))      # leftmost rule = table border
        segs = sorted((v[1], v[2]) for v in vert if abs(v[0] - bx) < 1)

    def table_of(y):
        for n, (y0, y1) in enumerate(segs):
            if y0 - 10 <= y <= y1 + 10:
                return n
        return None
    # ---- adjacent pairs
    pairs = []
    for A, B in zip(rows, rows[1:]):
        if B["y"] - A["y"] > 400:
            continue
        if segs and table_of(A["y"]) != table_of(B["y"]):
            continue
        # km candidates whose difference matches a distance-column value; the distance column skips halts, so it
        # only decides between two km values at a chainage change
        dd = [float(w[4]) for w in dist_col if A["y"] - 2 < cy(w) < B["y"] + 2]
        best = None
        for ka in A["km"]:
            for kb in B["km"]:
                L = abs(kb - ka)
                err = min((abs(L - x) for x in dd), default=0 if L < 30 else 99)
                if 0.2 < L < 60 and (best is None or (err, L) < (best[0], abs(best[2] - best[1]))):
                    best = (err, ka, kb)
        if best is not None and best[0] > 0.05 and (len(A["km"]) > 1 or len(B["km"]) > 1):
            # a row with two km values where one is 0.000 is a chainage restart (Kabat after Banyuwangi Kota):
            # the line continues from km 0 on this side
            zero = [(abs(kb - ka), ka, kb) for ka in A["km"] for kb in B["km"] if (kb == 0.0 or ka == 0.0) and 0.2 < abs(kb - ka) < 60]
            best = (0.0, zero[0][1], zero[0][2]) if zero else None
            if best is None and (A["code"].startswith("?") or B["code"].startswith("?")):
                # the distance column skips sheet-only stops, so it cannot check this pair: nearest km values
                kas = [A["km_in"]] if A.get("km_in") is not None else A["km"]   # keep the km from the previous pair
                near = sorted((abs(kb - ka), ka, kb) for ka in kas for kb in B["km"] if 0.2 < abs(kb - ka) < 30)
                best = (0.0, near[0][1], near[0][2]) if near else None
        if best is None:
            continue
        _, ka, kb = best
        sg = 1 if kb > ka else -1
        Lp = abs(kb - ka)
        rs = []
        for r in restr:
            if r["y0"] <= B["y"] + 4 and r["y1"] >= A["y"] - 4:
                o0, o1 = sorted(((r["km0"] - ka) * sg, (r["km1"] - ka) * sg))
                o0, o1 = max(o0, 0.0), min(o1, Lp)
                if o1 - o0 < 0.02 or o1 - o0 > 60:
                    continue
                rs.append(dict(o0=round(o0, 3), o1=round(o1, 3), v=r["v"], tags=r["tags"], note=r["note"]))
        bl = []
        for b in blocks:
            if A["y"] - 6 <= b["y"] <= B["y"] + 6:
                frac = min(max((b["y"] - A["y"]) / max(B["y"] - A["y"], 1e-6), 0.0), 1.0)
                if b["km"] is not None:
                    o = (b["km"] - ka) * sg
                    if -0.05 <= o <= Lp + 0.05:
                        bl.append(dict(o=round(o, 3), tag=b["tag"], no=b["no"], km=b["km"], kind="km on sheet"))
                elif 0 < frac < 1:
                    bl.append(dict(o=round(frac * Lp, 3), tag=b["tag"], no="", km=None, kind="sheet position"))
        for t_ in ("hu", "hi"):          # automatic block signals: N signals -> N+1 equal blocks per track (user)
            auto = sorted((b for b in bl if b["kind"] == "sheet position" and b["tag"] == t_), key=lambda b: b["o"])
            for i, b in enumerate(auto, 1):
                b["o"] = round(Lp * i / (len(auto) + 1), 3)
                b["kind"] = "automatic, evenly spaced"
        A.setdefault("km_out", ka)
        B.setdefault("km_in", kb)
        A["elev"], B["elev"] = elev_at(A["y"]), elev_at(B["y"])
        tno = table_of(A["y"]) if segs else None
        ctx = " ".join(r["code"] for r in rows if (table_of(r["y"]) if segs else None) == tno)
        pairs.append(dict(table=tno, context=ctx, a=A["code"], b=B["code"], km=round(Lp, 3), km_a=ka, km_b=kb, umum=umum_at((A["y"] + B["y"]) / 2),
                          restr=rs, h_a=A["elev"], h_b=B["elev"], blocks=bl, ruling=grad_at(A["y"], B["y"])))
    return dict(rows=rows, pairs=pairs)


# ------------------------------------------------------------------------------------------------ header
def header_info(page, ocr=None):
    """title text, raw header text, double-track chains listed under "JALUR GANDA" """
    r = page.rect
    ws = [w for w in page.get_text("words") if w[1] < r.height * 0.15 and w[0] > r.width * 0.3]
    if not any("GANDA" in w[4] for w in ws) and ocr is not None:
        ws, _ = ocr.read(page, pymupdf.Rect(r.width * 0.3, 0, r.width, r.height * 0.15), tile=2000, z=3, tag="header")
    raw_rows = defaultdict(list)
    for w in ws:
        raw_rows[round(cy(w) / 3)].append(w)
    raw = " / ".join(" ".join(t[4] for t in sorted(v, key=lambda t: t[0])) for k, v in sorted(raw_rows.items()))
    chains = []
    gs = [w for w in ws if "GANDA" in w[4].upper()]
    if gs:
        g = gs[0]
        jal = [w for w in ws if "JALUR" in w[4].upper() and abs(cy(w) - cy(g)) < 4]
        x0 = (jal[0][0] if jal else g[0]) - 70
        x1 = g[2] + 70
        rows_ = defaultdict(list)
        for w in ws:
            if x0 <= w[0] <= x1 and cy(g) + 4 < cy(w) < cy(g) + 70:
                rows_[round(cy(w) / 3)].append(w)
        last_y = cy(g)
        for k in sorted(rows_):
            v = sorted(rows_[k], key=lambda t: t[0])
            t = " ".join(x[4] for x in v).strip()
            if cy(v[0]) - last_y > 26:
                break
            if not CHAIN.match(t):
                break
            for part in t.split(","):
                part = part.strip()
                if part:
                    chains.append([c.strip().upper() for c in part.split("-")])
            last_y = cy(v[0])
    return raw, chains


def find_ctc(page, names):
    """station names drawn inside dashed boxes (CTC, user); text pages only"""
    boxes, segs, out = [], [], {}
    for x in page.get_drawings():
        rr = x["rect"]
        if 270 < rr.x0 < 360 and 15 < rr.width < 120 and 6 < rr.height < 25:
            boxes.append((rr, bool(x.get("dashes") and x["dashes"] not in ("[] 0", "[] 0.0"))))
        for it in x["items"]:
            if it[0] == "l":
                a, b = it[1], it[2]
                if 270 < min(a.x, b.x) < 360 and abs(a.x - b.x) < 6 and abs(a.y - b.y) < 6:
                    segs.append(((a.x + b.x) / 2, (a.y + b.y) / 2))
    for w in page.get_text("words"):
        if not (280 < w[0] < 360 and re.match(r"^[A-Za-z]", w[4])):
            continue
        code = names.exact(w[4])
        if not code:
            continue
        inside = [dsh for rr, dsh in boxes if rr.x0 < w[0] + 2 and rr.x1 > w[2] - 2 and rr.y0 < w[1] + 2 and rr.y1 > w[3] - 2]
        nseg = sum(1 for sx, sy in segs if w[0] - 8 < sx < w[2] + 8 and w[1] - 6 < sy < w[3] + 6)
        if any(inside) or (not inside and nseg >= 8):
            out[code] = page.number + 1
    return out


# ------------------------------------------------------------------------------------------------ timetable checks
def timetable_runs(ttdb):
    """unordered station pair -> [(running time s, vmax)] from adjacent timed rows"""
    obs = defaultdict(list)
    for (no,) in ttdb.execute("select no from trains"):
        rows = ttdb.execute("select station,arr_s,dep_s,vmax from stops where train_no=? order by seq", (no,)).fetchall()
        for x, z in zip(rows, rows[1:]):
            t0, t1 = x[2], (z[1] if z[1] is not None else z[2])
            if t0 is None or t1 is None or not x[3]:
                continue
            T = t1 - t0
            if 0 < T < 3600:
                obs[frozenset((x[0], z[0]))].append((T, x[3]))
    return obs


def plausible(L, o):
    """length L (km) fits the timetable: not faster than 1.15 x vmax on the fastest run, median speed >= 8 km/h"""
    if not o:
        return None
    fast = min(o)
    med = sorted(t for t, _ in o)[len(o) // 2]
    return L / (fast[0] / 3600) <= 1.15 * fast[1] and L / (med / 3600) >= 8


# ------------------------------------------------------------------------------------------------ main build
def build(pdf_path, tt_path, out_path, manual_dir):
    t_start = datetime.datetime.now()
    ttdb = sqlite3.connect(tt_path)
    names = Names(ttdb, manual_dir)
    doc = pymupdf.open(pdf_path)
    pdf_hash = hashlib.sha256(open(pdf_path, "rb").read()).hexdigest()
    ocr = OCR(pdf_hash)
    qa = []

    graph_pages = [i for i in range(doc.page_count) if doc[i].rect.width > 1000]
    per_page, headers, ctc = {}, {}, {}
    for i in graph_pages:
        page = doc[i]
        filler, km_filler = make_filler(ocr), make_km_filler(ocr)
        prof = page_profile(page, names, filler=filler, km_filler=km_filler)
        used_ocr = False
        if prof is None or len(prof["pairs"]) < 5:
            prof = page_profile(page, names, ocr=ocr, filler=filler, km_filler=km_filler)
            used_ocr = True
        prof = prof or dict(rows=[], pairs=[])
        prof["ocr"] = used_ocr
        per_page[i + 1] = prof
        headers[i + 1] = header_info(page, ocr if used_ocr else None)
        if not used_ocr:
            for c, pg in find_ctc(page, names).items():
                ctc.setdefault(c, pg)
        print(f"  page {i + 1:3d}: {len(prof['rows']):3d} station rows, {len(prof['pairs']):3d} pairs, "
              f"{sum(len(p['restr']) for p in prof['pairs']):3d} restrictions{'  (OCR)' if used_ocr else ''}")

    # ---- rows whose name cannot be read (Ijo: spaced letters in a dashed box): the station the timetable has
    #      between the neighbouring rows, if there is exactly one
    tt_adj = defaultdict(set)
    for (no,) in ttdb.execute("select no from trains"):
        sts = [r[0] for r in ttdb.execute("select station from stops where train_no=? order by seq", (no,))]
        for x, y in zip(sts, sts[1:]):
            tt_adj[x].add(y)
            tt_adj[y].add(x)
    for pg, prof in per_page.items():
        rows = prof["rows"]
        for i, r in enumerate(rows):
            if not r["code"].startswith("?unnamed"):
                continue
            a = rows[i - 1]["code"] if i > 0 else None
            b = rows[i + 1]["code"] if i + 1 < len(rows) else None
            cand = (tt_adj.get(a, set()) & tt_adj.get(b, set())) - {a, b}
            new = next(iter(cand)) if len(cand) == 1 else "?" + (key_of(r["sheet_name"]) or f"row{int(r['y'])}")
            if len(cand) == 1:
                qa.append(("station name from timetable neighbours", new, f"page {pg}, between {a} and {b}, OCR '{r['sheet_name']}'"))
            for p in prof["pairs"]:
                if p["a"] == r["code"]:
                    p["a"] = new
                if p["b"] == r["code"]:
                    p["b"] = new
            r["code"], r["sheet_name"] = new, (names.names.get(new) or r["sheet_name"])

    # ---- names shared by several stations: take the code the timetable runs next to the neighbouring rows
    adjacent = defaultdict(set)
    for (no,) in ttdb.execute("select no from trains"):
        sts = [r[0] for r in ttdb.execute("select station from stops where train_no=? order by seq", (no,))]
        for x, y in zip(sts, sts[1:]):
            adjacent[x].add(y)
            adjacent[y].add(x)
    for pg, prof in per_page.items():
        rows = prof["rows"]
        for i, r in enumerate(rows):
            cands = names.multi.get(key_of(r["sheet_name"]), [])
            if len(cands) > 1:
                nb = {rows[j]["code"] for j in (i - 1, i + 1) if 0 <= j < len(rows)}
                best = max(cands, key=lambda c: len(adjacent[c] & nb))
                if best != r["code"]:
                    for p in prof["pairs"]:
                        if p["a"] == r["code"] and abs(p["km_a"] - r.get("km_out", -1)) < 1e-6 and rows[i + 1]["code"] == p["b"] if i + 1 < len(rows) else False:
                            p["a"] = best
                        if p["b"] == r["code"] and abs(p["km_b"] - r.get("km_in", -1)) < 1e-6 and i > 0 and rows[i - 1]["code"] == p["a"]:
                            p["b"] = best
                    r["code"] = best

    # ---- lines: consecutive pages showing the same stations are the time pages of one line group
    lines, cur = [], None
    for pg in graph_pages:
        pg += 1
        codes = {r["code"] for r in per_page[pg]["rows"]}
        if cur and codes and len(codes & cur["codes"]) >= 0.7 * min(len(codes), len(cur["codes"])):
            cur["pages"].append(pg)
            cur["codes"] |= codes
        else:
            cur = dict(pages=[pg], codes=set(codes))
            lines.append(cur)
    for n, ln in enumerate(lines, 1):
        best = max(ln["pages"], key=lambda p: len(per_page[p]["rows"]))
        ln["best"] = best
        ln["id"] = f"L{n:02d}"
        rows = per_page[best]["rows"]
        ln["title"] = f"{names.names.get(rows[0]['code'], rows[0]['sheet_name']) if rows else '?'} - " \
                      f"{names.names.get(rows[-1]['code'], rows[-1]['sheet_name']) if rows else '?'}"

    # ---- sections: first page of a line that has the pair wins; others on later pages are consistency checks
    #      A pair drawn in two tables of the same sheet with different lengths is two different tracks (Kasugihan:
    #      main line via the junction north-east of the station, km 388.355-390.078 = 1.723 km; Cilacap branch from
    #      the station itself, 1.966 km): kept as a variant with the stations of its table as context.
    sections, variants = {}, {}
    for ln in lines:
        for pg in sorted(ln["pages"], key=lambda p: (p != ln["best"], p)):
            for p in per_page[pg]["pairs"]:
                k = (p["a"], p["b"])
                if k in sections or (p["b"], p["a"]) in sections:
                    old = sections.get(k) or sections.get((p["b"], p["a"]))
                    if old["page"] == pg and old.get("table") != p.get("table") and abs(old["km"] - p["km"]) > 0.05:
                        vk = (p["a"], p["b"], f"table {p['table']}")
                        variants.setdefault(vk, dict(p, line=ln["id"], page=pg, source="sheet (second table)", via=None))
                        continue
                    if any(v["a"] in k and v["b"] in k and abs(v["km"] - p["km"]) <= 0.05 for v in variants.values()):
                        continue
                    if abs(old["km"] - p["km"]) > 0.05:
                        qa.append(("length differs between pages", f"{p['a']}-{p['b']}",
                                   f"{old['km']} (page {old['page']}) vs {p['km']} (page {pg})"))
                    continue
                sections[k] = dict(p, line=ln["id"], page=pg, source="sheet", via=None)

    # ---- join through stops that are on the sheet but not in the timetable (Bangsal, Kumendung, ...), also through
    #      several in a row (Indro - Karangkering - Kalitangi - Rumah - Kandangan)
    def joined(p1, p2, a, b, via):
        L1 = p1["km"]
        rs = [dict(r) for r in p1["restr"]] + [dict(r, o0=round(r["o0"] + L1, 3), o1=round(r["o1"] + L1, 3)) for r in p2["restr"]]
        u1, u2 = p1["umum"], p2["umum"]
        umum = max(x for x in (u1, u2) if x) if (u1 or u2) else None
        if u1 and umum and u1 < umum:
            rs.append(dict(o0=0.0, o1=L1, v=u1, tags="", note="Umum of the first part"))
        if u2 and umum and u2 < umum:
            rs.append(dict(o0=L1, o1=L1 + p2["km"], v=u2, tags="", note="Umum of the second part"))
        bl = [dict(b_) for b_ in p1["blocks"]] + [dict(b_, o=round(b_["o"] + L1, 3)) for b_ in p2["blocks"]]
        r1, r2 = p1.get("ruling"), p2.get("ruling")
        ruling = round((r1 * L1 + r2 * p2["km"]) / (L1 + p2["km"]), 1) if r1 is not None and r2 is not None else None
        return dict(a=a, b=b, km=round(L1 + p2["km"], 3), km_a=p1["km_a"], km_b=p2["km_b"], umum=umum, restr=rs,
                    h_a=p1["h_a"], h_b=p2["h_b"], blocks=bl, ruling=ruling, line=p1["line"], page=p1["page"],
                    source="sheet (joined)", via=via, table=p1.get("table"), context=p1.get("context"))
    partial = {k: dict(p, via=p.get("via")) for k, p in sections.items() if k[1].startswith("?") and not k[0].startswith("?")}
    for _ in range(6):
        new = {}
        for (a, m), p1 in partial.items():
            for (m2, b), p2 in list(sections.items()):
                if m2 != m or (a, b) in sections or (b, a) in sections or b == a:
                    continue
                via = " ".join(x for x in ((p1.get("via") or ""), m[1:]) if x)
                j = joined(p1, p2, a, b, via)
                if b.startswith("?"):
                    new[(a, b)] = j
                else:
                    sections[(a, b)] = j
        partial = new
        if not partial:
            break

    # ---- check lengths against the timetable; replace implausible ones by gapeka.db, else drop
    obs = timetable_runs(ttdb)
    dbkm = {}
    for a, b, km in ttdb.execute("select a,b,km from station_links"):
        dbkm.setdefault(frozenset((a, b)), km)
    for k, p in list(sections.items()):
        pair = frozenset(k)
        ok = plausible(p["km"], obs.get(pair))
        if ok is False:
            alt = dbkm.get(pair)
            if alt and plausible(alt, obs.get(pair)):
                scale = alt / p["km"]
                for r in p["restr"]:
                    r["o0"], r["o1"] = round(r["o0"] * scale, 3), round(r["o1"] * scale, 3)
                qa.append(("length replaced (implausible vs timetable)", f"{k[0]}-{k[1]}", f"sheet {p['km']} -> gapeka.db {alt}"))
                p["km"], p["source"] = alt, p["source"] + "; length from gapeka.db"
            else:
                qa.append(("section dropped (implausible vs timetable)", f"{k[0]}-{k[1]}", f"sheet {p['km']} km"))
                del sections[k]

    # ---- bypasses: a row not read on one time page joins its neighbours (Sawah Besar-Gambir without Juanda).
    #      Drop a section when other sections connect its two stations with about the same length.
    gg = defaultdict(dict)
    for (a, b), p in sections.items():
        gg[a][b] = gg[b][a] = p["km"]
    for (a, b), p in sorted(sections.items(), key=lambda kv: -kv[1]["km"]):
        if b in adjacent[a] or p.get("via"):
            continue                          # a train runs it directly (Andir-Bandung), or joined through a stop
        best, frontier = None, [(a, 0.0, {a})]
        for _ in range(4):
            nxt = []
            for u, d, seen in frontier:
                for v, L in gg[u].items():
                    if (u, v) in ((a, b), (b, a)) or v in seen or v.startswith("?"):
                        continue
                    if v == b:
                        best = d + L if best is None else min(best, d + L)
                    elif d + L < p["km"] * 1.05:
                        nxt.append((v, d + L, seen | {v}))
            frontier = nxt
        if best is not None and abs(best - p["km"]) <= 0.03 * p["km"] + 0.05:
            qa.append(("bypass section dropped (rows between not read on one page)", f"{a}-{b}",
                       f"{p['km']} km vs {round(best, 3)} km through other sections, page {p['page']}"))
            del sections[(a, b)]
            del gg[a][b], gg[b][a]

    # ---- manual pairs and restriction overrides
    pth = os.path.join(manual_dir, "pair_overrides.csv")
    if os.path.exists(pth):
        for r in csv.DictReader(open(pth)):
            a, b = r["from"], r["to"]
            old = sections.pop((a, b), None) or sections.pop((b, a), None)
            base = old or dict(umum=None, restr=[], h_a=None, h_b=None, blocks=[], ruling=None, line=None, page=None,
                               via=None, km_a=None, km_b=None)
            sections[(a, b)] = dict(base, a=a, b=b, km=float(r["km"]), source=f"manual: {r['source']}")
    pth = os.path.join(manual_dir, "restriction_overrides.csv")
    if os.path.exists(pth):
        for r in csv.DictReader(open(pth)):
            a, b = r["from"], r["to"]
            p, flip = (sections.get((a, b)), False) if (a, b) in sections else (sections.get((b, a)), True)
            if p is None:
                qa.append(("restriction override not applied (no section)", f"{a}-{b}", r["note"]))
                continue
            o0, o1 = float(r["offset_from_km"]), float(r["offset_to_km"])
            if flip:                                    # offsets in the CSV are measured from 'from'
                o0, o1 = p["km"] - o1, p["km"] - o0
            hit = [x for x in p["restr"] if abs(x["o0"] - o0) < 0.05 and abs(x["o1"] - o1) < 0.05]
            if (r.get("action") or "set") == "delete":
                p["restr"] = [x for x in p["restr"] if x not in hit or (r.get("speed_kmh") and x["v"] != int(r["speed_kmh"]))]
                if not hit:
                    qa.append(("restriction delete override matched nothing", f"{a}-{b}", r["note"]))
                continue
            for x in hit:
                x["tags"], x["v"], x["source"] = r["set_tags"], int(r["speed_kmh"]), f"manual: {r['source']}"
            if not hit:
                p["restr"].append(dict(o0=o0, o1=o1, v=int(r["speed_kmh"]), tags=r["set_tags"], note=r["note"],
                                       source=f"manual: {r['source']}"))

    pth = os.path.join(manual_dir, "section_overrides.csv")
    if os.path.exists(pth):
        conv = dict(umum_kmh=("umum", int), ruling_gradient_permil=("ruling", float), km=("km", float),
                    double_track=("double_track_manual", int))
        for r in csv.DictReader(open(pth)):
            a, b = r["from"], r["to"]
            p = sections.get((a, b)) or sections.get((b, a))
            if p is None or r["field"] not in conv:
                qa.append(("section override not applied", f"{a}-{b}", f"{r['field']}={r['value']}"))
                continue
            f, cv = conv[r["field"]]
            p[f] = cv(r["value"])
            p["source"] += f"; {r['field']} manual: {r['source']}"

    # ---- double track: header lists ("JALUR GANDA"), followed along the station rows of the same sheet;
    #      sections of pages without such a list are inferred from hu/hi tags
    sec_key = {frozenset(k): k for k in sections}

    def row_path(pg, x, y):
        """sections between stations x and y along the rows of page pg (closest pair of occurrences)"""
        codes = [r["code"] for r in per_page[pg]["rows"]]
        best = None
        for i, cx in enumerate(codes):
            if cx != x:
                continue
            for j, cy_ in enumerate(codes):
                if cy_ != y or i == j:
                    continue
                lo, hi = sorted((i, j))
                steps = [frozenset((codes[k], codes[k + 1])) for k in range(lo, hi)]
                # steps between two sheet-only rows (sidings, "?name") may have no section of their own
                if all(st in sec_key or any(c.startswith("?") for c in st) for st in steps):
                    if best is None or hi - lo < len(best):
                        best = steps
        return best
    dt_rows, dbl = [], {}
    for ln in lines:
        seen_chains = set()
        for pg in ln["pages"]:
            for ch in headers[pg][1]:
                t = "-".join(ch)
                if t in seen_chains:
                    continue
                seen_chains.add(t)
                done = all(c in names.names for c in ch)
                for x, y in zip(ch, ch[1:]):
                    steps = row_path(pg, x, y) if done else None
                    if not steps:
                        done = False
                        continue
                    for st in steps:
                        dbl[st] = f"sheet header (page {pg}: {t})"
                dt_rows.append((ln["id"], pg, t, " ".join(ch), int(done)))
                if not done:
                    qa.append(("double-track header chain not resolved", t, f"page {pg}"))
    for k, p in sections.items():                  # joined sections: double if both parts are
        if p.get("via"):
            m = "?" + p["via"]
            if frozenset((k[0], m)) in dbl and frozenset((m, k[1])) in dbl:
                dbl[frozenset(k)] = dbl[frozenset((k[0], m))]
    header_pages = {pg for pg in per_page if headers[pg][1]}

    # ---- stations
    kinds = defaultdict(Counter)
    for st, kind in ttdb.execute("select station, kind from stops"):
        kinds[st][kind] += 1
    st_rows = {}
    for code, name in names.names.items():
        k = kinds.get(code)
        kind = "halt" if k and k.get("halt", 0) > 0 and k.get("stop", 0) == 0 and k.get("origin_or_end", 0) == 0 else "station"
        st_rows[code] = [code, name, kind, int(code in ctc), names.aliases.get(code), "timetable"]
    line_station_rows = []
    for ln in lines:
        rows = per_page[ln["best"]]["rows"]
        for n, r in enumerate(rows, 1):
            if r["code"].startswith("?") and r["code"] not in st_rows:
                st_rows[r["code"]] = [r["code"], r["sheet_name"], "sheet only", 0, r["sheet_name"], f"sheet (page {ln['best']})"]
            elif not r["code"].startswith("?") and st_rows[r["code"]][4] is None and key_of(r["sheet_name"]) != key_of(st_rows[r["code"]][1]):
                st_rows[r["code"]][4] = r["sheet_name"]
            km = r.get("km_in", r.get("km_out", r["km"][0]))
            alt = r.get("km_out") if r.get("km_out") is not None and abs(r.get("km_out") - km) > 0.001 else None
            line_station_rows.append((ln["id"], n, r["code"], km, alt, r.get("elev"), ln["best"]))

    # ---- write
    if os.path.exists(out_path):
        os.remove(out_path)
    out = sqlite3.connect(out_path)
    out.executescript(SCHEMA)
    meta = dict(pdf=os.path.basename(pdf_path), pdf_sha256=pdf_hash, built=t_start.isoformat(timespec="seconds"),
                builder="build_infra.py", timetable_db=os.path.basename(tt_path),
                direction="sections stored in sheet order a (upper row) -> b (lower row); hulu track = trains a -> b, "
                          "hilir track = trains b -> a; restriction offsets are measured from a")
    first = doc[graph_pages[0]].get_text()
    m = re.search(r"NOMOR\s*:\s*(.+)", first)
    if m:
        meta["decree"] = m.group(1).strip()
    m = re.search(r"Berlaku mulai tanggal\s+(.+)", first)
    if m:
        meta["valid_from"] = m.group(1).strip()
    out.executemany("insert into meta values(?,?)", meta.items())
    out.executemany("insert into lines values(?,?,?,?)",
                    [(ln["id"], ln["title"], " ".join(map(str, ln["pages"])), headers[ln["best"]][0]) for ln in lines])
    out.executemany("insert into stations values(?,?,?,?,?,?)", st_rows.values())
    out.executemany("insert into line_stations values(?,?,?,?,?,?,?)", line_station_rows)
    allsec = [((a, b), p, "") for (a, b), p in sections.items()] + [((a, b), p, v) for (a, b, v), p in variants.items()]
    for (a, b), p, var in allsec:
        key = frozenset((a, b))
        tags = [x["tag"] for x in p["blocks"]] + [t for r in p["restr"] for t in r["tags"].split()]
        has_tags = any(t.startswith(("hu", "hi")) for t in tags)
        # the header lists are not complete (e.g. Jatinegara-Bekasi, quadruple track, is not listed): a section is
        # double track when the header lists it OR its restrictions / block signals are tagged hu / hi
        if p.get("double_track_manual") is not None:
            d, src = p["double_track_manual"], "manual (user)"
        elif key in dbl:
            d, src = 1, dbl[key]
        elif has_tags:
            d, src = 1, "hu/hi tags on the sheet" + (" (not in the header list)" if p.get("page") in header_pages else "")
        else:
            d, src = 0, "header list (not listed)" if p.get("page") in header_pages else "no header list, no hu/hi tags"
        up = None if p.get("km_a") is None else int(p["km_b"] > p["km_a"])
        out.execute("insert into sections values(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (a, b, p.get("line"), p["km"], p.get("km_a"), p.get("km_b"), up, p.get("umum"), p.get("ruling"),
                     p.get("h_a"), p.get("h_b"), d, src, p.get("via"), p["source"], p.get("page"), var, p.get("context")))
        for r in p["restr"]:
            tracks = " ".join(sorted({"hu" if "hu" in t else "hi" for t in re.split(r"[-/ ]", r["tags"]) if t})) or "both"
            if tracks == "hi hu":
                tracks = "both"
            kf = kt = None
            if p.get("km_a") is not None:
                sg = 1 if p["km_b"] > p["km_a"] else -1
                kf, kt = round(p["km_a"] + sg * r["o0"], 3), round(p["km_a"] + sg * r["o1"], 3)
            out.execute("insert into restrictions values(?,?,?,?,?,?,?,?,?,?,?,?)",
                        (a, b, r["o0"], r["o1"], kf, kt, r["v"], tracks, r.get("note") or None,
                         r.get("source", p["source"]), p.get("page"), var))
        for b_ in p["blocks"]:
            out.execute("insert into block_signals values(?,?,?,?,?,?,?,?,?)",
                        (a, b, b_["o"], b_["tag"], b_["no"] or None, b_.get("km"), b_["kind"], p.get("page"), var))
    out.executemany("insert into double_track_header values(?,?,?,?,?)", dt_rows)

    # ---- QA
    covered = {frozenset(k) for k in sections}
    tt_pairs = Counter()
    for (no,) in ttdb.execute("select no from trains"):
        sts = [r[0] for r in ttdb.execute("select station from stops where train_no=? order by seq", (no,))]
        for x, y in zip(sts, sts[1:]):
            tt_pairs[frozenset((x, y))] += 1
    g = defaultdict(dict)
    for (a, b), p in sections.items():
        g[a][b] = g[b][a] = p["km"]

    def chain(x, y, max_hops=10):
        """shortest chain of sheet sections x..y with at most max_hops sections (stations the train does not
        list in between, e.g. Cakung-Rawabebek-Kranji); returns (hops, km) or None"""
        best, frontier = {x: 0.0}, [(x, 0.0)]
        for h in range(1, max_hops + 1):
            nxt = []
            for u, d in frontier:
                for v, L in g[u].items():
                    if v == y:
                        return h, round(d + L, 3)
                    if v not in best or best[v] > d + L:
                        best[v] = d + L
                        nxt.append((v, d + L))
            frontier = nxt
        return None
    via_chain = set()
    for pair, n in tt_pairs.most_common():
        if pair in covered:
            continue
        x, y = sorted(pair)
        c = chain(x, y)
        if c:
            via_chain.add(pair)
            qa.append(("timetable pair covered by a chain of sections", f"{x}-{y}", f"{c[0]} sections, {c[1]} km, {n} trains"))
        else:
            qa.append(("timetable pair without a sheet section", f"{x}-{y}", f"{n} trains"))
    for (a, b), p in sections.items():
        for r in p["restr"]:
            if p.get("umum") and r["v"] >= p["umum"]:
                qa.append(("restriction not below Umum (check the sheet)", f"{a}-{b}",
                           f"{r['v']} km/h at {r['o0']}-{r['o1']} km, Umum {p['umum']}, page {p.get('page')}"))
    for code, row in st_rows.items():
        if code.startswith("?"):
            qa.append(("sheet station not in the timetable", row[1], row[5]))
    for p in sections.values():
        if p.get("umum") is None:
            qa.append(("section without Umum", f"{p['a']}-{p['b']}", f"page {p.get('page')}"))
    tagged = Counter()
    for (a, b), p in sections.items():
        tags = [x["tag"] for x in p["blocks"]] + [t for r in p["restr"] for t in r["tags"].split()]
        inf = any(t.startswith(("hu", "hi")) for t in tags)
        if frozenset((a, b)) in dbl and not inf:
            tagged["header double, no hu/hi tags"] += 1
        if frozenset((a, b)) not in dbl and inf and p.get("page") in header_pages and p.get("double_track_manual") is None:
            tagged["header single, has hu/hi tags"] += 1
            qa.append(("double track from hu/hi tags, not in the header list", f"{a}-{b}", f"page {p.get('page')}"))
    out.executemany("insert into qa values(?,?,?)", qa)
    out.commit()

    n_tt = sum(tt_pairs.values())
    n_cov = sum(n for pr, n in tt_pairs.items() if pr in covered)
    print(f"\ninfra.db: {len(lines)} lines, {len(st_rows)} stations ({sum(1 for c in st_rows if c.startswith('?'))} sheet-only), "
          f"{len(sections)} sections, {sum(len(p['restr']) for p in sections.values())} restrictions, "
          f"{sum(len(p['blocks']) for p in sections.values())} block signals, {len(ctc)} CTC stations")
    print(f"  double track: {sum(1 for k in sections if frozenset(k) in dbl)} sections from the sheet headers; "
          f"{len(dt_rows)} header chains ({sum(1 for r in dt_rows if not r[4])} unresolved)")
    print(f"  elevation: {sum(1 for r in line_station_rows if r[5] is not None)} of {len(line_station_rows)} station rows; "
          f"ruling gradient: {sum(1 for p in sections.values() if p.get('ruling') is not None)} sections")
    n_ch = sum(n for pr, n in tt_pairs.items() if pr in via_chain)
    print(f"  timetable legs covered: {n_cov / n_tt:.1%} by one section, {(n_cov + n_ch) / n_tt:.1%} incl. chains of sections; "
          f"{len(tt_pairs) - len([p for p in tt_pairs if p in covered or p in via_chain])} station pairs not covered")
    for k, v in Counter(q[0] for q in qa).most_common():
        print(f"  QA {k}: {v}")
    for k, v in tagged.items():
        print(f"  check {k}: {v}")
    print(f"  ({(datetime.datetime.now() - t_start).seconds} s)")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--pdf", default="GAPEKA-JAWA-2026.pdf")
    ap.add_argument("--timetable", default="gapeka.db")
    ap.add_argument("--out", default="infra.db")
    ap.add_argument("--manual", default="manual")
    a = ap.parse_args(argv)
    build(a.pdf, a.timetable, a.out, a.manual)


if __name__ == "__main__":
    main()
