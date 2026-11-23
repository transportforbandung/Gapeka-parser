"""Draw a timetable block (stations across, one train per row) as a vector SVG for Illustrator.

  py export_poster.py 390-430 --even -o arah_cicalengka.svg
  py export_poster.py 391-431 --odd --highlight 349,351 -o arah_padalarang.svg
  py export_poster.py 350-400 --scale 0.8 -o kecil.svg

Selecting trains:  RANGE (350-400), single numbers, --odd / --even
Stations:          --exclude NAME,CODE,...   leave these columns out (default: Cisomang and Lebakjero, inactive; --exclude "" keeps all)
                   --stations CODE,CODE,...   only these columns, in this order (default: every station the trains call at)
                   --bold CODE,CODE,...       stations with bold times (default: Purwakarta, Padalarang, Cimahi, Bandung,
                                              Kiaracondong, Cicalengka, Cibatu, Garut)
Size (taken from the FDTB PDF; 1 pt = 1 px in Illustrator at 72 ppi). Fixed in every table: header row 22 pt high,
                   train-number cells 27.8 x 11.6 pt, time cells 11.6 pt high, row gap 2.3 pt. Only time-cell WIDTHS vary:
                   --max-width 1450   (default) total table width
                   --core PDL,CCL     (default) the block between these two stations has the same x position in every
                                      table that contains it, in both directions: the stations left and right of it
                                      (Purwakarta - Cilame, Nagreg - Garut) get the same total width each, so the side
                                      with more stations gets narrower cells. Such a table is always exactly --max-width
                                      wide. Without both core stations (e.g. Siliwangi): natural width per time cell
                                      (49.84 pt; Siliwangi-only tables 44.5 pt; --cell-width W sets it), narrowed evenly
                                      only if the table would be wider than --max-width.
                                      --core "" = no core block; --max-width 0 = natural widths
                   Header names that do not fit their cell go on two lines (Purwa-karta); the font is reduced only if
                   even two lines do not fit (reported). --wrap NAME,NAME   always on two lines (default: Leuwigoong)
                   The corners above the train-number columns are left empty.
                   --scale 0.8 / --fit-width 1450   resize the whole table uniformly (old behaviour; then the fixed
                                      sizes above are scaled too)
Colours (from the FDTB PDF): Commuter Line Garut = orange, Commuter Line Bandung Raya = blue (also the default), Siliwangi = yellow
                   (dark headers and train-number cells #F89938, light headers and dark times #F5BE1C, light times #FEE569).
                   A train's row colour follows its service. A station header takes the colour of the service that calls there most
                   (so Purwakarta-Cilame and Nagreg-Garut are orange even though one Bandung Raya train runs to Purwakarta);
                   a tie goes to the service that is not Bandung Raya.
                   Header and body columns alternate dark / light. With a core block (--core) the core starts dark and
                   every column alternates counted from it (even table: Purwakarta light ... Cilame light, Padalarang
                   dark); without one, each run of one colour starts dark.
Other:             --highlight 348,350   force these rows orange
                   --sort time|number   rows ordered by time at the bold station most trains call at (default) or by train number
                   --weight 500 (regular times: 400 Regular, 500 Medium)   --font "IBM Plex Sans"   --hfont "IBM Plex Sans"   --no-right-numbers
Cells: stop = departure time, halt (Berh/Sb) = arrival time, last stop = arrival, pass without a time = LS.
Columns alternate dark / light; a station the train does not serve is grey.
Text stays live text; groups are named header / rows / train-<no> / numbers.
"""
import re
import sqlite3
import sys
from xml.sax.saxutils import escape


def hms(s):
    s = int(round(s)) % 86400
    return f"{s // 3600:02d}:{s % 3600 // 60:02d}"


# advance widths (1/1000 em) of IBM Plex Sans SemiBold (600) and Bold (700), read from the font files once, so header
# names can be fitted to their cell without needing the font installed. Other characters count as 600.
_CH = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789 -.()'"
CHAR_W = {
    "600": dict(zip(_CH, [559, 600, 513, 600, 558, 350, 545, 588, 275, 275, 562, 294, 888, 588, 563, 600, 600, 393, 499,
                          374, 588, 524, 819, 544, 524, 502, 672, 663, 642, 689, 600, 577, 712, 719, 423, 545, 678, 521,
                          817, 719, 712, 641, 712, 664, 611, 580, 689, 638, 949, 655, 632, 599, 600, 600, 600, 600, 600,
                          600, 600, 600, 600, 600, 236, 402, 299, 337, 337, 260])),
    "700": dict(zip(_CH, [569, 608, 517, 608, 562, 361, 552, 596, 286, 286, 577, 303, 894, 596, 564, 608, 608, 404, 504,
                          383, 596, 538, 841, 560, 534, 518, 685, 667, 651, 697, 607, 585, 719, 724, 432, 559, 696, 530,
                          819, 724, 714, 656, 714, 674, 624, 584, 694, 650, 973, 673, 649, 607, 600, 600, 600, 600, 600,
                          600, 600, 600, 600, 600, 236, 403, 310, 338, 338, 268])),
}
CELL_W = {"Siliwangi": 44.5}   # natural time-cell width (pt) of a table that has only this service's trains
HEAD_PAD = 0.5         # pt of the cell width kept free beside a header name (both sides together)
VOWELS = set("aeiouAEIOU")


def text_w(s, size, weight):
    w = CHAR_W.get(weight, CHAR_W["600"])
    return sum(w.get(c, 600) for c in s) * size / 1000


def header_lines(n, room, size, weight):
    """the name on one line if it fits in `room` pt, else two lines: split at a space, or hyphenated before a syllable
    (Purwa-karta, Kiara-condong, Pasir-jengkol), choosing the split with the narrower widest line"""
    if text_w(n, size, weight) <= room:
        return [n]
    cands = []
    if " " in n:
        parts = n.split(" ")
        cands = [[" ".join(parts[:i]), " ".join(parts[i:])] for i in range(1, len(parts))]
    else:
        # syllable starts: a consonant + vowel after a vowel (Purwa-karta) or after a consonant (Pasir-jengkol),
        # never inside 'ng' / 'ny'
        syl = [i for i in range(2, len(n) - 2) if n[i] not in VOWELS and n[i + 1] in VOWELS
               and not (n[i - 1] == "n" and n[i] in "gy")]
        cands = [[n[:i] + "-", n[i:]] for i in (syl or range(2, len(n) - 1))]
    return min(cands, key=lambda c: max(text_w(x, size, weight) for x in c))


def main(argv):
    opt = dict(out="poster.svg", db="gapeka.db", scale=1.0, font="IBM Plex Sans", hfont="IBM Plex Sans",
               weight="500", sort="time", parity=None, stations=None, bold=None,
               highlight=set(), right=True, exclude=["Cisomang", "Lebakjero","Rajamandala","Selajambe","Tipar","Maleber","Cilaku","Sindangresmi","Ranji"], fit=None, maxw=1450.0, core=["PDL", "CCL"],
               wrap={"Leuwigoong"}, cellw=None)
    args = []
    it = iter(argv[1:])
    for a in it:
        if a == "-o":
            opt["out"] = next(it)
        elif a == "--db":
            opt["db"] = next(it)
        elif a == "--fit-width":
            opt["fit"] = float(next(it))
        elif a == "--cell-width":
            opt["cellw"] = float(next(it))
        elif a == "--max-width":
            opt["maxw"] = float(next(it))
        elif a == "--wrap":
            opt["wrap"] = {x.strip() for x in next(it).split(",") if x.strip()}
        elif a == "--core":
            opt["core"] = [x.strip() for x in next(it).split(",") if x.strip()]
        elif a == "--scale":
            opt["scale"] = float(next(it))
        elif a in ("--font", "--hfont", "--color", "--sort", "--weight"):
            opt[a[2:]] = next(it)
        elif a == "--odd":
            opt["parity"] = 1
        elif a == "--even":
            opt["parity"] = 0
        elif a == "--stations":
            opt["stations"] = next(it).split(",")
        elif a == "--bold":
            opt["bold"] = next(it).split(",")
        elif a == "--highlight":
            opt["highlight"] = set(next(it).split(","))
        elif a == "--exclude":
            opt["exclude"] = [x for x in next(it).split(",") if x]
        elif a == "--no-right-numbers":
            opt["right"] = False
        else:
            args.append(a)
    if not args:
        sys.exit(__doc__)
    db = sqlite3.connect(opt["db"])
    wanted = []
    for a in args:
        if "-" in a:
            lo, hi = (int(x) for x in a.split("-"))
            wanted += [r[0] for r in db.execute(
                "select no from trains where no GLOB '[0-9]*' and cast(no as integer) between ? and ?", (lo, hi))]
        else:
            wanted.append(a)
    if opt["parity"] is not None:
        wanted = [n for n in wanted if re.match(r"\d+", n) and int(re.match(r"\d+", n).group()) % 2 == opt["parity"]]
    wanted = sorted(set(wanted))
    name = dict(db.execute("select code,name from stations"))
    trains = {}
    for no in wanted:
        rows = db.execute("select station,kind,arr_s,dep_s from schedule where train_no=? order by seq", (no,)).fetchall()
        if rows:
            trains[no] = rows
    if not trains:
        sys.exit("no trains found")
    service = {no: (db.execute("select service from trains where no=?", (no,)).fetchone() or [""])[0] for no in trains}
    # palettes: (header dark, header light, cell dark, cell light, grey dark, grey light, number box)
    BLUE = ("#2bace2", "#47c4f1", "#6dcff6", "#b0e3fa", "#c0bfbf", "#cccccc", "#717171")
    ORANGE = ("#d54727", "#f26c50", "#f79779", "#fabba8", "#c0bfbf", "#cccccc", "#d54727")
    YELLOW = ("#f89938", "#f5be1c", "#f5be1c", "#fee569", "#c0bfbf", "#cccccc", "#f89938")
    PAL = {"Commuter Line Bandung Raya": BLUE, "Commuter Line Garut": ORANGE, "Siliwangi": YELLOW}
    pal_of = lambda svc: PAL.get(svc, BLUE)

    # column order: longest train first, others slotted in beside shared stations
    order = []
    for no, rows in sorted(trains.items(), key=lambda kv: -len(kv[1])):
        seq = []
        for r in rows:
            if r[0] not in seq:
                seq.append(r[0])
        if not order:
            order = seq[:]
            continue
        pos = {s: i for i, s in enumerate(order)}
        shared = [pos[s] for s in seq if s in pos]
        if len(shared) >= 2 and shared[0] > shared[-1]:
            seq.reverse()
        prev = None
        for s in seq:
            if s in pos:
                prev = s
                continue
            order.insert((order.index(prev) + 1) if prev is not None else 0, s)
            pos = {x: i for i, x in enumerate(order)}
            prev = s
    skip = {c for c, nm in name.items() if nm in opt["exclude"] or c in opt["exclude"]}   # inactive stations
    order = [s_ for s_ in order if s_ not in skip]
    if opt["stations"]:
        order = [s for s in opt["stations"] if s in name or s in order]
    if opt["bold"]:
        bold = set(opt["bold"])
    else:
        byname = {v: k for k, v in name.items()}
        bold = {byname[n] for n in ("Purwakarta", "Padalarang", "Cimahi", "Bandung", "Kiaracondong", "Cicalengka",
                                    "Cibatu", "Garut") if n in byname}

    # cells
    cells = {}
    for no, rows in trains.items():
        c = {}
        for st, kind, a, d in rows:
            if st in c:
                continue
            if kind == "pass":                       # Ls: the train does not stop, even if a passing time is printed
                c[st] = "LS"
                continue
            if kind == "halt":                       # Berh / Sb: arrival time
                t = a if a is not None else d
            else:                                    # stop: departure (arrival at the last stop)
                t = d if d is not None else a
            c[st] = hms(t) if t is not None else ("LS" if kind == "pass" else "")
        cells[no] = c
    served = {st: sum(1 for c in cells.values() if c.get(st)) for st in order}
    ref = max((s_ for s_ in order if s_ in bold), key=lambda s_: (served[s_], -order.index(s_)), default=order[0])   # rows are sorted by the time here
    # (halts: ref station is a real stop in practice)

    def key(no):
        if opt["sort"] == "number":
            m = re.match(r"\d+", no)
            return (int(m.group()) if m else 0, no)
        for st, kind, a, d in trains[no]:
            if st == ref and (d is not None or a is not None):
                return ((d if d is not None else a) % 86400, no)
        t0 = next((x for r in trains[no] for x in (r[3], r[2]) if x is not None), 0)
        return (t0 % 86400, no)
    nos = sorted(trains, key=key)

    n = len(order)
    # header palette per station = palette of the service with most trains calling there
    hpal = {}
    for st in order:
        cnt = {}
        for no, rows in trains.items():
            if any(r[0] == st and r[1] != "pass" and (r[2] is not None or r[3] is not None) for r in rows):
                cnt[service[no]] = cnt.get(service[no], 0) + 1
        # a tie goes to the other service, not Bandung Raya (odd Purwakarta side: one Garut + one Bandung Raya train)
        top = max(cnt.items(), key=lambda kv: (kv[1], kv[0] != "Commuter Line Bandung Raya"))[0] if cnt else ""
        hpal[st] = pal_of(top)
    cidx = sorted(order.index(c) for c in opt["core"] if c in order)
    gidx = {}                         # even = dark, odd = light
    if len(cidx) == 2:
        # shading anchored on the core block: its first column is dark, and every column alternates counted from
        # there, so the wings continue from their neighbouring core column (even table: Cilame light, Padalarang dark)
        for i, st in enumerate(order):
            gidx[st] = abs(i - cidx[0])
    else:
        run = 0                       # position inside a run of same-coloured stations: dark, light, dark, ...
        for i, st in enumerate(order):
            run = run + 1 if i and hpal[st] is hpal[order[i - 1]] else 0
            gidx[st] = run
    k = opt["scale"]
    if opt["fit"]:                   # scale so the whole table is this many pt wide (cell ratios stay the same)
        k = opt["fit"] / ((2 if opt["right"] else 1) * 27.8 + n * 49.84)
    # natural time-cell width: --cell-width, else per service when every train is of that service, else 49.84
    svcs = set(service.values())
    base_w = opt["cellw"] or (CELL_W.get(next(iter(svcs))) if len(svcs) == 1 else None) or 49.84
    colw, cellh, gap, numw, headh = base_w * k, 11.6 * k, 2.3 * k, 27.8 * k, 22.0 * k
    rowh = cellh + gap
    fs, fsb = 8.0 * k, 9.5 * k
    nnum = 2 if opt["right"] else 1
    # column widths. Header row height, train-number width and cell height are fixed; only time-cell widths change.
    ws, core_info = [colw] * n, ""
    if opt["maxw"] and not opt["fit"]:
        avail = opt["maxw"] - nnum * numw
        idx = sorted(order.index(c) for c in opt["core"] if c in order)
        nl, nc, nr = (idx[0], idx[1] - idx[0] + 1, n - idx[1] - 1) if len(idx) == 2 else (0, n, 0)
        if len(idx) == 2:
            # core block (e.g. Padalarang - Cicalengka) at the same x in every table of this core: both wings get the
            # same total width: the wing with fewer stations (Nagreg - Garut) has cells as wide as the core, the wing
            # with more stations (Purwakarta - Cilame) shares that width, so its cells are narrower
            u = min(nl, nr) if nl and nr else 0
            cw = avail / (nc + 2 * u) if u else avail / n
            wing = u * cw
            ws = ([wing / nl] * nl if u else [cw] * nl) + [cw] * nc + ([wing / nr] * nr if u else [cw] * nr)
            x_core = numw + sum(ws[:nl])
            core_info = (f"; core {name.get(order[idx[0]], order[idx[0]])} - {name.get(order[idx[1]], order[idx[1]])}: "
                         f"{nc} cells of {cw:.2f} pt at x {x_core:.2f}-{x_core + nc * cw:.2f}"
                         + (f", wings {wing:.2f} pt each ({nl} x {wing / nl:.2f} / {nr} x {wing / nr:.2f} pt)" if u else ""))
        elif n * colw > avail:
            ws = [avail / n] * n
    xs = [numw + sum(ws[:i]) for i in range(n)]
    W = nnum * numw + sum(ws)
    H = headh + gap + len(nos) * rowh - gap
    font, hfont = opt["font"], opt["hfont"]
    out = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{W:.2f}" height="{H:.2f}" viewBox="0 0 {W:.2f} {H:.2f}">']

    def text(x, y, s, size, weight="400", fill="#1a1a1a", fam=None):
        out.append(f'<text x="{x:.2f}" y="{y + size * 0.35:.2f}" font-family="{escape(fam or font)}" font-size="{size:.2f}" '
                   f'font-weight="{weight}" fill="{fill}" text-anchor="middle">{escape(s)}</text>')

    def rect(x, y, w, h, fill):
        out.append(f'<rect x="{x:.2f}" y="{y:.2f}" width="{w:.2f}" height="{h:.2f}" fill="{fill}"/>')


    out.append('<g id="header">')                  # the corners above the train-number columns stay empty
    shrunk = []
    for i, st in enumerate(order):
        x, cw = xs[i], ws[i]
        P = hpal[st]
        rect(x, 0, cw, headh, P[0] if gidx[st] % 2 == 0 else P[1])
        wt, size = ("700" if st in bold else "600"), 8.0 * k
        nm = name.get(st, st)
        lines = header_lines(nm, 0 if nm in opt["wrap"] else cw - HEAD_PAD * k, size, wt)
        widest = max(text_w(ln, size, wt) for ln in lines)
        if widest > cw - HEAD_PAD * k:              # even two lines do not fit: smaller font for this name only
            size *= (cw - HEAD_PAD * k) / widest
            shrunk.append(f"{name.get(st, st)} {size:.2f} pt")
        lh = 8.4 * k
        for j, ln in enumerate(lines):
            # dark text on the light palettes (blue = same colour as the times, yellow), white on orange
            text(x + cw / 2, headh / 2 + (j - (len(lines) - 1) / 2) * lh, ln, size,
                 wt, "#231f20" if P is YELLOW else ("#1a1a1a" if P is BLUE else "#ffffff"), hfont)
    out.append("</g>")

    out.append('<g id="rows">')
    for r, no in enumerate(nos):
        y = headh + gap + r * rowh
        P = ORANGE if no in opt["highlight"] else pal_of(service[no])
        out.append(f'<g id="train-{escape(no)}">')
        for i, st in enumerate(order):
            x, cw = xs[i], ws[i]
            v = cells[no].get(st)
            dark = gidx[st] % 2 == 0
            if not v:
                fill = P[4] if dark else P[5]
            else:
                fill = P[2] if dark else P[3]
            rect(x, y, cw, cellh, fill)
            if v:
                isb = st in bold
                text(x + cw / 2, y + cellh / 2, v, fsb if isb else fs, "700" if isb else opt["weight"])
        out.append("</g>")
    out.append("</g>")

    out.append('<g id="numbers">')
    for r, no in enumerate(nos):
        y = headh + gap + r * rowh
        fill = (ORANGE if no in opt["highlight"] else pal_of(service[no]))[6]
        for x in ([0, W - numw] if opt["right"] else [0]):
            rect(x, y, numw, cellh, fill)
            text(x + numw / 2, y + cellh / 2, no, fsb, "700", "#ffffff")
    out.append("</g>")
    out.append("</svg>")
    with open(opt["out"], "w", encoding="utf-8") as f:
        f.write("\n".join(out))
    print(f"wrote {opt['out']}: {len(nos)} trains x {n} stations, {W:.0f} x {H:.0f} pt{core_info}")
    if shrunk:
        print("  header names too wide even on two lines, font reduced:", ", ".join(shrunk))


main(sys.argv)