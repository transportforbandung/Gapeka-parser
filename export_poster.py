"""Draw a timetable block (stations across, one train per row) as a vector SVG for Illustrator.

  py export_poster.py 390-430 --even -o arah_cicalengka.svg
  py export_poster.py 391-431 --odd --highlight 349,351 -o arah_padalarang.svg
  py export_poster.py 350-400 --scale 0.8 -o kecil.svg

Selecting trains:  RANGE (350-400), single numbers, --odd / --even
Stations:          --exclude NAME,CODE,...   leave these columns out (default: Cisomang and Lebakjero, inactive; --exclude "" keeps all)
                   --stations CODE,CODE,...   only these columns, in this order (default: every station the trains call at)
                   --bold CODE,CODE,...       stations with bold times (default: Purwakarta, Padalarang, Cimahi, Bandung,
                                              Kiaracondong, Cicalengka, Cibatu, Garut)
Size (same for every table, taken from the FDTB PDF; 1 pt = 1 px in Illustrator at 72 ppi):
                   time cell 49.84 x 11.6 pt, row gap 2.3 pt, train-number cell 27.8 x 11.6 pt, header row 22 pt high
                   --scale 0.8   resizes the whole table uniformly (cell ratios stay the same)
                   --max-width 1450   (default) a table wider than this gets narrower time cells so it is exactly
                                      this wide; heights, number cells and fonts stay the same; --max-width 0 = off
                   --fit-width 1450   scales the table so its total width is 1450 pt
Colours (from the FDTB PDF): Commuter Line Garut = orange, Commuter Line Bandung Raya = blue (also the default), Siliwangi = yellow.
                   A train's row colour follows its service. A station header takes the colour of the service that calls there most
                   (so Purwakarta-Cilame and Nagreg-Garut are orange even though one Bandung Raya train runs to Purwakarta).
                   Header and body columns alternate dark / light, starting again with dark when the colour changes.
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


def split_name(n, maxc):
    if len(n) <= maxc:
        return [n]
    if " " in n:
        parts = n.split(" ")
        best, cut = None, 0
        for i in range(1, len(parts)):
            a, b = " ".join(parts[:i]), " ".join(parts[i:])
            if best is None or abs(len(a) - len(b)) < best:
                best, cut = abs(len(a) - len(b)), i
        return [" ".join(parts[:cut]), " ".join(parts[cut:])]
    h = (len(n) + 1) // 2
    return [n[:h] + "-", n[h:]]


def main(argv):
    opt = dict(out="poster.svg", db="gapeka.db", scale=1.0, font="IBM Plex Sans", hfont="IBM Plex Sans",
               weight="500", sort="time", parity=None, stations=None, bold=None,
               highlight=set(), right=True, exclude=["Cisomang", "Lebakjero","Rajamandala","Selajambe","Tipar","Maleber","Cilaku","Sindangresmi","Ranji"], fit=None, maxw=1450.0)
    args = []
    it = iter(argv[1:])
    for a in it:
        if a == "-o":
            opt["out"] = next(it)
        elif a == "--db":
            opt["db"] = next(it)
        elif a == "--fit-width":
            opt["fit"] = float(next(it))
        elif a == "--max-width":
            opt["maxw"] = float(next(it))
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
    YELLOW = ("#fecd03", "#fce669", "#fee36a", "#fdf2b0", "#c0bfbf", "#cccccc", "#717171")
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
        top = max(cnt.items(), key=lambda kv: (kv[1], kv[0] == "Commuter Line Bandung Raya"))[0] if cnt else ""
        hpal[st] = pal_of(top)
    gidx, run = {}, 0                 # position inside a run of same-coloured stations: dark, light, dark, ...
    for i, st in enumerate(order):
        run = run + 1 if i and hpal[st] is hpal[order[i - 1]] else 0
        gidx[st] = run
    k = opt["scale"]
    if opt["fit"]:                   # scale so the whole table is this many pt wide (cell ratios stay the same)
        k = opt["fit"] / ((2 if opt["right"] else 1) * 27.8 + n * 49.84)
    colw, cellh, gap, numw, headh = 49.84 * k, 11.6 * k, 2.3 * k, 27.8 * k, 22.0 * k
    rowh = cellh + gap
    fs, fsb = 8.0 * k, 9.5 * k
    nnum = 2 if opt["right"] else 1
    if opt["maxw"] and not opt["fit"] and nnum * numw + n * colw > opt["maxw"]:
        colw = (opt["maxw"] - nnum * numw) / n    # narrower time cells only; heights, number cells and fonts unchanged
    W = nnum * numw + n * colw
    H = headh + gap + len(nos) * rowh - gap
    x0 = numw
    font, hfont = opt["font"], opt["hfont"]
    out = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{W:.2f}" height="{H:.2f}" viewBox="0 0 {W:.2f} {H:.2f}">']

    def text(x, y, s, size, weight="400", fill="#1a1a1a", fam=None):
        out.append(f'<text x="{x:.2f}" y="{y + size * 0.35:.2f}" font-family="{escape(fam or font)}" font-size="{size:.2f}" '
                   f'font-weight="{weight}" fill="{fill}" text-anchor="middle">{escape(s)}</text>')

    def rect(x, y, w, h, fill):
        out.append(f'<rect x="{x:.2f}" y="{y:.2f}" width="{w:.2f}" height="{h:.2f}" fill="{fill}"/>')


    out.append('<g id="header">')
    rect(0, 0, numw, headh, "#595a5a")
    if opt["right"]:
        rect(W - numw, 0, numw, headh, "#595a5a")
    maxc = 11                      # names longer than this are split over two lines (Kiara-condong, Gado-bangkong)
    for i, st in enumerate(order):
        x = x0 + i * colw
        P = hpal[st]
        rect(x, 0, colw, headh, P[0] if gidx[st] % 2 == 0 else P[1])
        lines = split_name(name.get(st, st), maxc)
        lh = 8.4 * k
        for j, ln in enumerate(lines):
            text(x + colw / 2, headh / 2 + (j - (len(lines) - 1) / 2) * lh, ln, 8.0 * k,
                 "700" if st in bold else "600", "#231f20" if P is YELLOW else "#ffffff", hfont)
    out.append("</g>")

    out.append('<g id="rows">')
    for r, no in enumerate(nos):
        y = headh + gap + r * rowh
        P = ORANGE if no in opt["highlight"] else pal_of(service[no])
        out.append(f'<g id="train-{escape(no)}">')
        for i, st in enumerate(order):
            x = x0 + i * colw
            v = cells[no].get(st)
            dark = gidx[st] % 2 == 0
            if not v:
                fill = P[4] if dark else P[5]
            else:
                fill = P[2] if dark else P[3]
            rect(x, y, colw, cellh, fill)
            if v:
                isb = st in bold
                text(x + colw / 2, y + cellh / 2, v, fsb if isb else fs, "700" if isb else opt["weight"])
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
    print(f"wrote {opt['out']}: {len(nos)} trains x {n} stations, {W:.0f} x {H:.0f} pt")


main(sys.argv)