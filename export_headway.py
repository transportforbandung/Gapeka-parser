"""Draw a station's train times as a horizontal time bar (vector SVG for Illustrator).

  py export_headway.py BD 390-431                       -> Bandung, every train 390..431
  py export_headway.py BD 390-431 --odd -o bd_odd.svg   -> only odd trains (one direction)
  py export_headway.py Kiaracondong 350-431 386 388     -> station by name, range plus single trains
  py export_headway.py BD,KAC,CCL 390-431 --even        -> one bar per station, stacked, same time axis
  py export_headway.py BD --service "Commuter Line Bandung Raya"   -> every train of a service (no numbers needed)

The first argument is the station(s): a code or name, or several separated by commas.
Selecting trains:  RANGE (350-400), single numbers, --odd / --even, --service NAME[,NAME] (all trains of that service;
                   with numbers given, it filters them)
Time at the station: departure; arrival at the last stop; halt (Berh/Sb) = arrival. Trains passing (Ls) are left out,
                   add --include-pass to draw them too.
Time axis:         --span 4-24        hours shown (default: whole hours around the first and last train of all bars);
                                      5-1 runs past midnight to 01:00
                   --day-start 3      times before 03:00 belong to the previous service day (drawn after 24:00)
Headway colour:    between two trains, light green (short) to light red (long); only between the first and last train
                   of the day, the rest of the bar stays grey.
                   --green 10 --red 60   headway in minutes that is fully green / fully red (default 10 and 60)
Size:              --width 800 (bar width, pt)  --height 18 (bar height, pt)  --tick 1.2 (train line width, pt)
                   --font "IBM Plex Sans"  --no-legend  --no-labels (no station name left of the bar)
Text stays live text; groups are named axis / bar-<code> (headway, trains) / legend.
"""
import colorsys
import re
import sqlite3
import sys
from xml.sax.saxutils import escape


def hm(s):
    s = int(round(s))
    return f"{s // 3600:02d}:{s % 3600 // 60:02d}"


def headway_colour(minutes, green, red):
    """light green -> light yellow -> light red, by hue; same lightness so the train lines stay readable"""
    f = min(max((minutes - green) / (red - green), 0.0), 1.0) if red > green else 0.0
    r, g, b = colorsys.hls_to_rgb((1 - f) * 120 / 360, 0.80, 0.70)
    return f"#{round(r * 255):02x}{round(g * 255):02x}{round(b * 255):02x}"


def main(argv):
    opt = dict(out="headway.svg", db="gapeka.db", parity=None, service=None, span=None, day_start=3.0,
               green=10.0, red=60.0, width=800.0, height=18.0, tick=1.2, font="IBM Plex Sans",
               legend=True, labels=True, include_pass=False)
    args = []
    it = iter(argv[1:])
    for a in it:
        if a == "-o":
            opt["out"] = next(it)
        elif a == "--db":
            opt["db"] = next(it)
        elif a == "--odd":
            opt["parity"] = 1
        elif a == "--even":
            opt["parity"] = 0
        elif a == "--service":
            opt["service"] = [s.strip() for s in next(it).split(",") if s.strip()]
        elif a == "--span":
            lo, hi = next(it).split("-")
            opt["span"] = (float(lo), float(hi))
        elif a in ("--day-start", "--green", "--red", "--width", "--height", "--tick"):
            opt[a[2:].replace("-", "_")] = float(next(it))
        elif a == "--font":
            opt["font"] = next(it)
        elif a == "--no-legend":
            opt["legend"] = False
        elif a == "--no-labels":
            opt["labels"] = False
        elif a == "--include-pass":
            opt["include_pass"] = True
        else:
            args.append(a)
    if not args or (len(args) < 2 and not opt["service"]):
        sys.exit(__doc__)
    db = sqlite3.connect(opt["db"])
    name = dict(db.execute("select code,name from stations"))
    byname = {v.lower(): k for k, v in name.items()}
    stations = []
    for s in args[0].split(","):
        s = s.strip()
        code = s if s in name else byname.get(s.lower())
        if not code:
            sys.exit(f"unknown station: {s}")
        stations.append(code)

    # trains
    wanted = []
    for a in args[1:]:
        if "-" in a:
            lo, hi = (int(x) for x in a.split("-"))
            wanted += [r[0] for r in db.execute(
                "select no from trains where no GLOB '[0-9]*' and cast(no as integer) between ? and ?", (lo, hi))]
        else:
            wanted.append(a)
    if opt["service"]:
        q = ",".join("?" * len(opt["service"]))
        insvc = {r[0] for r in db.execute(f"select no from trains where service in ({q})", opt["service"])}
        wanted = [n for n in wanted if n in insvc] if args[1:] else sorted(insvc)
    if opt["parity"] is not None:            # parity of the leading digits, so 2501F counts as odd
        wanted = [n for n in wanted if re.match(r"\d+", n) and int(re.match(r"\d+", n).group()) % 2 == opt["parity"]]
    wanted = sorted(set(wanted))
    if not wanted:
        sys.exit("no trains selected")

    # time of each train at each station
    ds = opt["day_start"] * 3600
    times = {}                               # station -> [(t, train)]
    for st in stations:
        lst = []
        for no in wanted:
            for kind, a, d in db.execute("select kind,arr_s,dep_s from schedule where train_no=? and station=? order by seq",
                                         (no, st)):
                if kind == "pass" and not opt["include_pass"]:
                    continue
                t = a if kind == "halt" else (d if d is not None else a)
                if t is None:
                    continue
                t = t % 86400
                if t < ds:
                    t += 86400                # after midnight: end of the same service day
                lst.append((t, no))
                break                         # a train counts once per station
        times[st] = sorted(lst)
        print(f"{name[st]} ({st}): {len(lst)} trains" + (f", {hm(lst[0][0] % 86400)} - {hm(lst[-1][0] % 86400)}" if lst else ""))
    allt = [t for v in times.values() for t, _ in v]
    if not allt:
        sys.exit("none of the selected trains calls at these stations")
    if opt["span"]:
        h0, h1 = opt["span"]
        if h1 <= h0:                          # e.g. 5-1: runs past midnight
            h1 += 24
    else:
        h0, h1 = int(min(allt) // 3600), -(-int(max(allt)) // 3600)
        if h1 == h0:
            h1 += 1
    t0, t1 = h0 * 3600, h1 * 3600

    # layout
    font = opt["font"]
    W, bh = opt["width"], opt["height"]
    lw = 90.0 if opt["labels"] else 0.0       # station label column
    pad, gap, axish = 6.0, 10.0, 16.0
    legh = 30.0 if opt["legend"] else 0.0
    x0 = lw
    X = lambda t: x0 + (t - t0) / (t1 - t0) * W
    H = axish + len(stations) * (bh + gap) - gap + pad + legh
    out = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{lw + W + pad:.2f}" height="{H:.2f}" '
           f'viewBox="0 0 {lw + W + pad:.2f} {H:.2f}">']

    def text(x, y, s, size, weight="400", anchor="middle", fill="#1a1a1a"):
        out.append(f'<text x="{x:.2f}" y="{y + size * 0.35:.2f}" font-family="{escape(font)}" font-size="{size:.2f}" '
                   f'font-weight="{weight}" fill="{fill}" text-anchor="{anchor}">{escape(s)}</text>')

    def rect(x, y, w, h, fill, extra=""):
        out.append(f'<rect x="{x:.2f}" y="{y:.2f}" width="{max(w, 0):.2f}" height="{h:.2f}" fill="{fill}"{extra}/>')

    # hour axis on top
    out.append('<g id="axis">')
    for h in range(int(h0), int(h1) + 1):
        x = X(h * 3600)
        out.append(f'<line x1="{x:.2f}" y1="{axish - 4:.2f}" x2="{x:.2f}" y2="{axish + len(stations) * (bh + gap) - gap:.2f}" '
                   f'stroke="#9a9a9a" stroke-width="0.4"/>')
        text(x, axish - 9, f"{h % 24:02d}:00", 7.0, "500", fill="#555555")
    out.append("</g>")

    for i, st in enumerate(stations):
        y = axish + i * (bh + gap)
        lst = times[st]
        out.append(f'<g id="bar-{escape(st)}">')
        if opt["labels"]:
            text(lw - 6, y + bh / 2, name[st], 8.5, "600", "end")
        rect(x0, y, W, bh, "#e6e6e6")                       # outside first..last train: grey
        out.append('<g id="headway">')
        for (ta, na), (tb, nb) in zip(lst, lst[1:]):
            if tb <= ta:
                continue
            xa, xb = max(X(ta), x0), min(X(tb), x0 + W)
            if xb <= xa:
                continue
            mins = (tb - ta) / 60
            rect(xa, y, xb - xa, bh, headway_colour(mins, opt["green"], opt["red"]),
                 f' data-headway="{mins:.0f} min" data-trains="{escape(na)}-{escape(nb)}"')
        out.append("</g>")
        out.append('<g id="trains">')
        for t, no in lst:
            if t0 <= t <= t1:
                x = X(t)
                out.append(f'<line id="train-{escape(no)}" x1="{x:.2f}" y1="{y:.2f}" x2="{x:.2f}" y2="{y + bh:.2f}" '
                           f'stroke="#1a1a1a" stroke-width="{opt["tick"]:.2f}"><title>{escape(no)} {hm(t % 86400)}</title></line>')
        out.append("</g>")
        out.append(f'<rect x="{x0:.2f}" y="{y:.2f}" width="{W:.2f}" height="{bh:.2f}" fill="none" stroke="#595a5a" stroke-width="0.6"/>')
        out.append("</g>")

    if opt["legend"]:
        out.append('<g id="legend">')
        ly = axish + len(stations) * (bh + gap) - gap + 10
        lx, lwid, steps = x0, 160.0, 40
        for k in range(steps):
            m = opt["green"] + (opt["red"] - opt["green"]) * k / (steps - 1)
            rect(lx + k * lwid / steps, ly, lwid / steps + 0.05, 7, headway_colour(m, opt["green"], opt["red"]))
        text(lx, ly + 13, f"≤{opt['green']:.0f} min", 6.5, anchor="start", fill="#555555")
        text(lx + lwid, ly + 13, f"≥{opt['red']:.0f} min", 6.5, anchor="end", fill="#555555")
        text(lx + lwid + 8, ly + 3.5, "headway", 6.5, anchor="start", fill="#555555")
        out.append("</g>")
    out.append("</svg>")
    with open(opt["out"], "w", encoding="utf-8") as f:
        f.write("\n".join(out))
    print(f"wrote {opt['out']}: {len(stations)} bar(s), {hm(t0 % 86400)}-{hm(t1 % 86400)}, {lw + W + pad:.0f} x {H:.0f} pt")


main(sys.argv)
