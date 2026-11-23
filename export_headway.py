"""Draw a station's train times as a horizontal time bar (vector SVG for Illustrator).

  py export_headway.py BD 386-431 -o bd_both.svg       -> Bandung, two bars: even trains (to Cicalengka) on top,
                                                           odd trains (to Padalarang) below
  py export_headway.py BD 386-431 --odd -o bd_odd.svg   -> only odd trains (one bar, one direction)
  py export_headway.py BD 386-431 --mixed               -> both directions together in one bar
  py export_headway.py Kiaracondong 350-431 386 388     -> station by name, range plus single trains
  py export_headway.py BD,KAC,CCL 390-431 --even        -> one bar per station, stacked, same time axis
  py export_headway.py BD --service "Commuter Line Bandung Raya"   -> every train of a service (no numbers needed)

The first argument is the station(s): a code or name, or several separated by commas.
Selecting trains:  RANGE (350-400), single numbers, --odd / --even, --service NAME[,NAME] (all trains of that service;
                   with numbers given, it filters them)
Directions:        default: one bar per direction for each station, even trains first, then odd (direction by the
                   parity of the train number; the label names the station most of those trains run to);
                   --odd / --even: only that direction; --mixed: one bar per station with both directions
Time at the station: departure; arrival at the last stop; halt (Berh/Sb) = arrival. Trains passing (Ls) are left out,
                   add --include-pass to draw them too.
Time axis:         --span 4-24        hours shown (default: whole hours around the first and last train of all bars);
                                      5-1 runs past midnight to 01:00
                   --day-start 3      times before 03:00 belong to the previous service day (drawn after 24:00)
Colour:            green (short) to amber to red (long) (SCALE at the top of the script), only between the first and last train of the day; the rest of
                   the bar stays grey.
                   --mode wait        (default) waiting time (waktu tunggu) for the next train at each moment: right
                                      after a train it is the whole gap (reddest), it turns greener as the next train
                                      gets closer and is green just before it; at each train line it starts again
                   --mode headway     each gap flat in the colour of its headway, blending into the next only around
                                      the train line between them
                   --blend 3          minutes of blending on each side of a train line (headway mode; a shorter gap
                                      blends from its middle; --blend 0 = hard edges)
                   --green 10 --red 60   minutes that are fully green / fully red (default 10 and 60)
                   --legend-marks 30     extra minute labels under the legend bar, at their place on the scale
                                         (default 30; several: 20,30,45; "" = none)
Size:              --width 800 (bar width, pt)  --height 18 (bar height, pt)  --tick 0.5 (train line width, pt)  --tick-color "#8a8a8a" (muted grey)
                   --font "IBM Plex Sans"  --no-legend  --no-labels (no station name left of the bar)
Texts:             Indonesian by default (--lang en for English). Title "Waktu Tunggu Kereta di Stasiun <name>" (one
                   station) or "Waktu Tunggu Kereta"; --title "..." / --no-title. Bar labels "Menuju" / "<destination>" on
                   two lines (with one station; with several, the station name above "Menuju <destination>"); legend "Waktu tunggu", minutes "menit".
PNG:               a PNG with the same name is written next to the SVG (bd_both.svg -> bd_both.png), rendered by a
                   headless Edge / Chrome so it uses the installed fonts and real gradients.
                   --png-scale 3 (pixels per pt; 3 = 216 dpi)  --no-png  --browser PATH (Edge / Chrome executable)
Text stays live text; groups are named axis / bar-<code>-even, bar-<code>-odd (or bar-<code> with --mixed)
(headway, trains) / legend.
"""
import math
import os
import pathlib
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
from xml.sax.saxutils import escape


def hm(s):
    s = int(round(s))
    return f"{s // 3600:02d}:{s % 3600 // 60:02d}"


# texts of the drawing (default Indonesian)
TEXT = {
    "id": dict(title_one="Waktu Tunggu Kereta di Stasiun {}", title_many="Waktu Tunggu Kereta", to="Menuju",
               wait="Waktu tunggu", headway="Headway", min="menit"),
    "en": dict(title_one="Waiting Time for Trains at {} Station", title_many="Waiting Time for Trains", to="To",
               wait="Waiting time", headway="Headway", min="min"),
}

# colour scale: green (short) -> amber -> red (long); saturated mid tones, blended in RGB between these stops
SCALE = [(0.0, (0x22, 0xa4, 0x5d)), (0.5, (0xf5, 0xc2, 0x42)), (1.0, (0xe5, 0x39, 0x35))]


def headway_colour(minutes, green, red):
    """colour for a wait / headway in minutes: SCALE from `green` minutes (fully green) to `red` (fully red)"""
    f = min(max((minutes - green) / (red - green), 0.0), 1.0) if red > green else 0.0
    for (fa, ca), (fb, cb) in zip(SCALE, SCALE[1:]):
        if f <= fb:
            u = (f - fa) / (fb - fa)
            return "#" + "".join(f"{round(a + (b - a) * u):02x}" for a, b in zip(ca, cb))
    return "#" + "".join(f"{c:02x}" for c in SCALE[-1][1])


def main(argv):
    opt = dict(out="headway.svg", db="gapeka.db", parity=None, service=None, span=None, day_start=3.0,
               green=10.0, red=60.0, width=800.0, height=18.0, tick=0.5, tick_color="#8a8a8a", blend=3.0, mode="wait", font="IBM Plex Sans",
               legend=True, labels=True, include_pass=False, mixed=False, lang="id", title=None, marks=[30.0],
               png=True, png_scale=3.0, browser=None)
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
        elif a in ("--day-start", "--green", "--red", "--width", "--height", "--tick", "--blend"):
            opt[a[2:].replace("-", "_")] = float(next(it))
        elif a == "--mode":
            opt["mode"] = next(it)
            if opt["mode"] not in ("wait", "headway"):
                sys.exit("--mode is wait or headway")
        elif a == "--tick-color":
            opt["tick_color"] = next(it)
        elif a == "--font":
            opt["font"] = next(it)
        elif a == "--no-legend":
            opt["legend"] = False
        elif a == "--no-labels":
            opt["labels"] = False
        elif a == "--lang":
            opt["lang"] = next(it)
            if opt["lang"] not in TEXT:
                sys.exit("--lang is id or en")
        elif a == "--title":
            opt["title"] = next(it)
        elif a == "--no-title":
            opt["title"] = ""
        elif a == "--no-png":
            opt["png"] = False
        elif a == "--png-scale":
            opt["png_scale"] = float(next(it))
        elif a == "--browser":
            opt["browser"] = next(it)
        elif a == "--legend-marks":
            opt["marks"] = [float(x) for x in next(it).split(",") if x.strip()]
        elif a == "--mixed":
            opt["mixed"] = True
        elif a == "--include-pass":
            opt["include_pass"] = True
        else:
            args.append(a)
    if not args or (len(args) < 2 and not opt["service"]):
        sys.exit(__doc__)
    T = TEXT[opt["lang"]]
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

    # bars: by default one per direction (even = Cicalengka-bound first, then odd = Padalarang-bound) for each station;
    # --odd / --even: that direction only; --mixed: both directions in one bar
    def parity(no):
        m = re.match(r"\d+", no)
        return int(m.group()) % 2 if m else None

    def destination(nos):
        """the station most of these trains run to (their last stop), for the bar label"""
        ends = {}
        for no in nos:
            r = db.execute("select station from stops where train_no=? order by seq desc limit 1", (no,)).fetchone()
            if r:
                ends[r[0]] = ends.get(r[0], 0) + 1
        return name.get(max(ends, key=ends.get), "") if ends else ""

    bars = []                                # (id, station, direction label, [(t, train)])
    for st in stations:
        if opt["mixed"]:
            bars.append((st, st, "", times[st]))
            continue
        for par in ((0, 1) if opt["parity"] is None else (opt["parity"],)):
            sub = [(t, no) for t, no in times[st] if parity(no) == par]
            if sub:
                dest = destination([no for _, no in sub])
                bars.append((f"{st}-{'even' if par == 0 else 'odd'}", st, f"{T['to']} {dest}" if dest else "", sub))
    for bid, st, lab, lst in bars:
        print(f"{name[st]} ({st}){' ' + lab if lab else ''}: {len(lst)} trains"
              + (f", {hm(lst[0][0] % 86400)} - {hm(lst[-1][0] % 86400)}" if lst else ""))
    allt = [t for *_, v in bars for t, _ in v]
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
    if opt["title"] is None:                  # default title names the station when there is only one
        opt["title"] = T["title_one"].format(name[stations[0]]) if len(stations) == 1 else T["title_many"]
    titleh = 22.0 if opt["title"] else 0.0
    pad, gap, axish = 6.0, 10.0, 16.0 + titleh    # pad = bottom margin
    rpad = 16.0                                   # right margin: room for half of the last hour label
    legh = 30.0 if opt["legend"] else 0.0
    x0 = lw
    X = lambda t: x0 + (t - t0) / (t1 - t0) * W
    H = axish + len(bars) * (bh + gap) - gap + pad + legh
    out = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{lw + W + rpad:.2f}" height="{H:.2f}" '
           f'viewBox="0 0 {lw + W + rpad:.2f} {H:.2f}">']

    def text(x, y, s, size, weight="400", anchor="middle", fill="#1a1a1a"):
        out.append(f'<text x="{x:.2f}" y="{y + size * 0.35:.2f}" font-family="{escape(font)}" font-size="{size:.2f}" '
                   f'font-weight="{weight}" fill="{fill}" text-anchor="{anchor}">{escape(s)}</text>')

    def rect(x, y, w, h, fill, extra=""):
        out.append(f'<rect x="{x:.2f}" y="{y:.2f}" width="{max(w, 0):.2f}" height="{h:.2f}" fill="{fill}"{extra}/>')

    if opt["title"]:
        out.append('<g id="title">')
        text(x0, 8, opt["title"], 11.0, "700", "start")
        out.append("</g>")

    # hour axis on top
    out.append('<g id="axis">')
    for h in range(int(h0), int(h1) + 1):
        x = X(h * 3600)
        out.append(f'<line x1="{x:.2f}" y1="{axish - 4:.2f}" x2="{x:.2f}" y2="{axish + len(bars) * (bh + gap) - gap:.2f}" '
                   f'stroke="#9a9a9a" stroke-width="0.4"/>')
        text(x, axish - 9, f"{h % 24:02d}:00", 7.0, "500", fill="#555555")
    out.append("</g>")

    for i, (bid, st, lab, lst) in enumerate(bars):
        y = axish + i * (bh + gap)
        out.append(f'<g id="bar-{escape(bid)}">')
        if opt["labels"]:
            if lab and len(stations) == 1:    # one station (named in the title): the direction only, on two lines
                to, dest = lab.split(" ", 1)  # "Menuju" / "Padalarang"
                text(lw - 6, y + bh / 2 - 4.6, to, 8.5, "600", "end")
                text(lw - 6, y + bh / 2 + 4.6, dest, 8.5, "600", "end")
            elif lab:                         # several stations: station name over the direction
                text(lw - 6, y + bh / 2 - 4.2, name[st], 8.5, "600", "end")
                text(lw - 6, y + bh / 2 + 5.0, lab, 7.0, "400", "end", fill="#555555")
            else:
                text(lw - 6, y + bh / 2, name[st], 8.5, "600", "end")
        rect(x0, y, W, bh, "#e6e6e6")                       # outside first..last train: grey
        out.append('<g id="headway">')
        # one gradient from the first to the last train: each gap is flat in its own colour and blends into the next
        # gap's colour only within --blend minutes on each side of the train line between them (a gap shorter than
        # 2 x blend blends from its middle)
        gaps = [(ta, tb, (tb - ta) / 60, na, nb) for (ta, na), (tb, nb) in zip(lst, lst[1:]) if tb > ta]
        if gaps:
            xa, xb = max(X(lst[0][0]), x0), min(X(lst[-1][0]), x0 + W)
            gid = f"headway-{bid}"
            out.append(f'<defs><linearGradient id="{escape(gid)}" gradientUnits="userSpaceOnUse" '
                       f'x1="{xa:.2f}" y1="0" x2="{xb:.2f}" y2="0">')
            off = lambda t: min(max((X(t) - xa) / (xb - xa), 0.0), 1.0) if xb > xa else 0.0
            if opt["mode"] == "wait":
                # wait time for the next train: right after a train the whole gap (reddest), falling to 0 (green) at
                # the next train line, where it jumps back to the next gap's full wait. Stops every 5 min of wait so
                # the blend follows the same hue path as the legend.
                for ta, tb, mins, na, nb in gaps:
                    ws = sorted({mins, 0.0, opt["green"], opt["red"]} | {float(w) for w in range(0, int(mins), 5)},
                                reverse=True)
                    for w in [w for w in ws if w <= mins]:
                        out.append(f'<stop offset="{off(tb - w * 60):.4f}" '
                                   f'stop-color="{headway_colour(w, opt["green"], opt["red"])}">'
                                   f'<title>{escape(na)}-{escape(nb)}: {w:.0f} min to {escape(nb)}</title></stop>')
                gaps = []
            for ta, tb, mins, na, nb in gaps:
                b = min(opt["blend"] * 60, (tb - ta) / 2)
                col = headway_colour(mins, opt["green"], opt["red"])
                for t in (ta + b, tb - b):      # flat between these two stops; the blend lies outside them
                    out.append(f'<stop offset="{off(t):.4f}" stop-color="{col}">'
                               f'<title>{escape(na)}-{escape(nb)} {mins:.0f} min</title></stop>')
            out.append("</linearGradient></defs>")
            if xb > xa:
                rect(xa, y, xb - xa, bh, f"url(#{escape(gid)})")
        out.append("</g>")
        out.append('<g id="trains">')
        for t, no in lst:
            if t0 <= t <= t1:
                x = X(t)
                out.append(f'<line id="train-{escape(no)}" x1="{x:.2f}" y1="{y:.2f}" x2="{x:.2f}" y2="{y + bh:.2f}" '
                           f'stroke="{escape(opt["tick_color"])}" stroke-width="{opt["tick"]:.2f}"><title>{escape(no)} {hm(t % 86400)}</title></line>')
        out.append("</g>")
        out.append(f'<rect x="{x0:.2f}" y="{y:.2f}" width="{W:.2f}" height="{bh:.2f}" fill="none" stroke="#595a5a" stroke-width="0.6"/>')
        out.append("</g>")

    if opt["legend"]:
        out.append('<g id="legend">')
        ly = axish + len(bars) * (bh + gap) - gap + 10
        lx, lwid, steps = x0, 160.0, 11
        out.append('<defs><linearGradient id="headway-legend" x1="0" y1="0" x2="1" y2="0">')
        for k in range(steps):            # several stops so the blend follows the same hue path as the bars
            m = opt["green"] + (opt["red"] - opt["green"]) * k / (steps - 1)
            out.append(f'<stop offset="{k / (steps - 1):.2f}" stop-color="{headway_colour(m, opt["green"], opt["red"])}"/>')
        out.append("</linearGradient></defs>")
        rect(lx, ly, lwid, 7, "url(#headway-legend)")
        text(lx, ly + 13, f"≤{opt['green']:.0f} {T['min']}", 6.5, anchor="start", fill="#555555")
        text(lx + lwid, ly + 13, f"≥{opt['red']:.0f} {T['min']}", 6.5, anchor="end", fill="#555555")
        for mk in opt["marks"]:            # labels between the ends, at their place on the scale (default 30 min)
            if opt["green"] < mk < opt["red"]:
                mx = lx + lwid * (mk - opt["green"]) / (opt["red"] - opt["green"])
                out.append(f'<line x1="{mx:.2f}" y1="{ly:.2f}" x2="{mx:.2f}" y2="{ly + 7:.2f}" '
                           f'stroke="#555555" stroke-width="0.6"/>')     # mark on the bar
                text(mx, ly + 13, f"{mk:g} {T['min']}", 6.5, fill="#555555")
        text(lx + lwid + 8, ly + 3.5, T["wait"] if opt["mode"] == "wait" else T["headway"], 6.5, anchor="start",
             fill="#555555")
        out.append("</g>")
    out.append("</svg>")
    with open(opt["out"], "w", encoding="utf-8") as f:
        f.write("\n".join(out))
    print(f"wrote {opt['out']}: {len(bars)} bar(s), {hm(t0 % 86400)}-{hm(t1 % 86400)}, {lw + W + rpad:.0f} x {H:.0f} pt")
    if opt["png"]:
        png = os.path.splitext(opt["out"])[0] + ".png"
        if svg_to_png("\n".join(out), lw + W + rpad, H, png, opt["png_scale"], opt["browser"]):
            print(f"wrote {png}: {round((lw + W + rpad) * opt['png_scale'])} x {round(H * opt['png_scale'])} px")


BROWSERS = [r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
            r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
            r"C:\Program Files\Google\Chrome\Application\chrome.exe",
            r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
            "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
            "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge"]


def svg_to_png(svg, w, h, png, scale, browser=None):
    """render the SVG with a headless Edge / Chrome: it uses the installed fonts (IBM Plex Sans) and draws the
    gradients exactly like Illustrator / a web page. Returns False (with a message) if no browser is found."""
    exe = browser or next((b for b in BROWSERS if os.path.exists(b)), None) \
        or shutil.which("msedge") or shutil.which("google-chrome") or shutil.which("chromium") or shutil.which("chrome")
    if not exe:
        print("  no PNG: Edge / Chrome not found (give one with --browser PATH, or use --no-png)")
        return False
    with tempfile.TemporaryDirectory() as tmp:
        page = os.path.join(tmp, "page.html")
        with open(page, "w", encoding="utf-8") as f:
            f.write('<!doctype html><html><head><meta charset="utf-8"><style>html,body{margin:0;background:#fff}'
                    f'svg{{display:block}}</style></head><body>{svg}</body></html>')
        shot = os.path.join(tmp, "shot.png")
        r = subprocess.run([exe, "--headless=new", "--disable-gpu", "--hide-scrollbars", f"--user-data-dir={tmp}",
                            f"--force-device-scale-factor={scale:g}", f"--window-size={math.ceil(w)},{math.ceil(h)}",
                            f"--screenshot={shot}", pathlib.Path(page).as_uri()],
                           capture_output=True, text=True, timeout=120)
        if r.returncode != 0 or not os.path.exists(shot):
            print("  no PNG: the browser failed:", (r.stderr or "").strip()[-300:])
            return False
        shutil.copyfile(shot, png)
    return True


main(sys.argv)
