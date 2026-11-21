"""Station track diagrams on the right of the graph sheets -> tracks per station.

Each diagram: horizontal track lines with a label "<number>-<effective length m>" (Roman = main / siding track,
Arabic = stabling / yard only), chevrons at the line ends showing the normal running direction (Indonesia runs on
the right track), neighbour station codes at the ends, a box with a dot and a line = PPKA post, a box with a dot
only = PAP.  Speeds are not drawn.

    python station_layouts.py GAPEKA-JAWA-2026.pdf infra.db        # prints a summary
"""
import re
import sqlite3
import sys
from collections import defaultdict

import pymupdf

GROUP_FIRST_PAGES = [7, 13, 19, 25, 28, 29, 32, 35, 38, 41, 44, 47, 50]
LABEL = re.compile(r"^([IVXL]+|\d{1,2}|Y\d)\s*-\s*(\d{2,4}(?:[,.]\d+)?)$")
is_roman = lambda no: bool(re.fullmatch(r"[IVXL]+", no))       # Arabic and Y-numbered tracks: yard / stabling
ROM = {"I": 1, "V": 5, "X": 10, "L": 50}


def roman(s):
    v = 0
    for i, ch in enumerate(s):
        x = ROM[ch]
        v += -x if i + 1 < len(s) and ROM[s[i + 1]] > x else x
    return v


class UF:
    def __init__(self, n):
        self.p = list(range(n))

    def find(self, a):
        while self.p[a] != a:
            self.p[a] = self.p[self.p[a]]
            a = self.p[a]
        return a

    def union(self, a, b):
        self.p[self.find(a)] = self.find(b)


def page_items(page, x_min):
    """segments (x0,y0,x1,y1) of drawn track lines, chevrons, small boxes, filled dots, right of x_min"""
    global dashed, solid
    segs, chev, boxes, dots, pointers = [], [], [], [], []
    dashed, solid = [], []
    for dr in page.get_drawings():
        r = dr["rect"]
        if r.x1 < x_min:
            continue
        items = dr["items"]
        w = dr.get("width") or 0
        if dr.get("fill") and all(it[0] == "c" for it in items) and r.width < 3 and r.height < 3:
            dots.append(((r.x0 + r.x1) / 2, (r.y0 + r.y1) / 2))
            continue
        if len(items) == 1 and items[0][0] == "re" and 5 < r.width < 14 and 5 < r.height < 14:
            boxes.append(r)
            continue
        if dr["type"] == "s" and len(items) == 2 and all(it[0] == "l" for it in items) and r.width < 6 and r.height < 6:
            (_, a, b), (_, c, e) = items
            if abs(b.x - c.x) < 0.2 and abs(b.y - c.y) < 0.2:          # a -> vertex -> e
                chev.append((b.x, b.y, "L" if b.x < min(a.x, e.x) else "R"))
                continue
        if dr.get("fill") and dr["type"] == "f" and r.width < 20 and r.height > 14 and len(items) >= 5:
            pointers.append(r)                       # arrow from a station name to its displaced diagram
            continue
        if dr.get("fill") and dr["type"] == "f" and all(it[0] in ("l", "c") for it in items) and r.width > 15 and r.height < 60:
            items = [(it[0], it[1], it[-1]) for it in items]           # curves: their end points
            # main tracks drawn as one filled outline with their arrowheads (and crossovers): every pair of close
            # horizontal edges is one track; its arrow orientation comes from the outline's end vertices
            pts = [(it[1].x, it[1].y) for it in items] + [(it[2].x, it[2].y) for it in items]
            # long edges can be drawn as many short pieces: join collinear horizontal pieces into runs
            pieces = sorted(((min(it[1].x, it[2].x), max(it[1].x, it[2].x), (it[1].y + it[2].y) / 2) for it in items
                             if it[0] == "l" and abs(it[1].y - it[2].y) < 0.4 and abs(it[1].x - it[2].x) > 0.8), key=lambda q: (round(q[2], 1), q[0]))
            runs = []
            for a, b, y in pieces:
                if runs and abs(runs[-1][2] - y) < 0.2 and a <= runs[-1][1] + 1.0:
                    runs[-1][1] = max(runs[-1][1], b)
                else:
                    runs.append([a, b, y])
            hor = []
            for a, b, y in runs:
                if b - a > 4:
                    segs.append((a, y, b, y))
                    hor.append((None, pymupdf.Point(a, y), pymupdf.Point(b, y)))
            for it in items:
                a, b = it[1], it[2]
                if abs(a.y - b.y) > 0.1 and abs(a.x - b.x) > 0.1:
                    solid.append((a.x, a.y, b.x, b.y))           # crossover / switch edges (also tiny curve steps)
            levels = []
            for it in sorted(hor, key=lambda it: it[1].y):
                if levels and it[1].y - levels[-1][-1][1].y < 1.6:
                    levels[-1].append(it)
                else:
                    levels.append([it])
            for lv in levels:
                yc = sum(it[1].y for it in lv) / len(lv)
                lo = min(min(it[1].x, it[2].x) for it in lv)
                hi = max(max(it[1].x, it[2].x) for it in lv)
                for end in ("lo", "hi"):
                    side = [q for q in pts if abs(q[1] - yc) < 5 and (q[0] <= lo + 0.5 if end == "lo" else q[0] >= hi - 0.5)]
                    if not side:
                        continue
                    xe = min(q[0] for q in side) if end == "lo" else max(q[0] for q in side)
                    head = [q for q in pts if abs(q[1] - yc) < 5 and abs(q[0] - xe) < 5]
                    if not (any(q[1] < yc - 1.5 for q in head) and any(q[1] > yc + 1.5 for q in head)):
                        continue                         # no arrowhead (barbs above and below): a curve, a stub
                    ext = [q for q in side if abs(q[0] - xe) < 0.6]
                    tip = any(abs(q[1] - yc) < 1.2 for q in ext) and len({round(q[1]) for q in ext}) <= 2
                    if end == "lo":
                        chev.append((lo, yc, "L" if tip else "R"))
                    else:
                        chev.append((hi, yc, "R" if tip else "L"))
            continue
        if dr["type"] not in ("s", "fs") or w < 0.15:
            continue
        is_dashed = bool(re.search(r"\[\s*\d", dr.get("dashes") or ""))
        for it in items:
            if it[0] == "l":
                seg = (it[1].x, it[1].y, it[2].x, it[2].y)
            elif it[0] == "c":
                seg = (it[1].x, it[1].y, it[4].x, it[4].y)
            else:
                continue
            segs.append(seg)
            ln_ = abs(seg[0] - seg[2]) + abs(seg[1] - seg[3])
            if (abs(seg[1] - seg[3]) > 0.4 and abs(seg[0] - seg[2]) > 0.2) or \
                    (len(items) > 2 and ln_ < 4 and abs(seg[0] - seg[2]) > 0.2):   # switch curves (also tiny flat steps)
                (dashed if is_dashed else solid).append(seg)
    return segs, chev, boxes, dots, pointers


def usable_directions(ts, pieces, x0, x1):
    """per track: the directions ('L' / 'R' on the sheet) in which a train can use it, from the switch geometry.
    A train only moves forward: it takes a connection (switch curve / crossover, a chain of pieces from a point on
    one track to a point on another) only in the direction of travel. Usable in R = reachable moving right from an
    entry main (a through track drawn with an R arrow, or any through track when no arrows are drawn) and able to
    continue right to an exit main; likewise L. Tracks with a drawn arrow are used only in their arrow direction
    (user: main straight through tracks are one direction only; Ciroyom: III to Andir, I to Bandung)."""
    pts = [(q[0], q[1]) for q in pieces] + [(q[2], q[3]) for q in pieces]
    uf = UF(len(pieces))
    ends = defaultdict(list)
    for k, q in enumerate(pieces):
        ends[(round(q[0]), round(q[1]))].append(k)
        ends[(round(q[2]), round(q[3]))].append(k)
    for k, q in enumerate(pieces):            # joined where an end meets another piece (also mid-way: ladders)
        for (px, py) in ((q[0], q[1]), (q[2], q[3])):
            for j, r in enumerate(pieces):
                if j != k and min(r[0], r[2]) - 1 <= px <= max(r[0], r[2]) + 1 and \
                        min(r[1], r[3]) - 1 <= py <= max(r[1], r[3]) + 1 and pt_seg(px, py, r) < 0.9:
                    uf.union(k, j)
    groups = defaultdict(list)
    for k in range(len(pieces)):
        groups[uf.find(k)].append(k)
    conns = []                                     # (track a, x on a, track b, x on b)
    for g in groups.values():
        att = []
        for k in g:
            for (px, py) in ((pieces[k][0], pieces[k][1]), (pieces[k][2], pieces[k][3])):
                for i, t in enumerate(ts):
                    if abs(py - t["line_y"]) < 2.5 and t["lo"] - 20 <= px <= t["hi"] + 20:
                        att.append((px, i))
        att = sorted(set((round(px, 1), i) for px, i in att))
        for k1 in range(len(att)):                 # every pair of tracks one switch group touches
            for k2 in range(k1 + 1, len(att)):
                (xa, a), (xb, b) = att[k1], att[k2]
                if a != b:
                    conns.append((a, xa, b, xb))
    def edge(i, side):
        return ts[i]["lo"] - x0 < 6 if side == "left" else x1 - ts[i]["hi"] < 6
    def mains(d, role):
        """entry / exit tracks of direction d: at that edge of the diagram, carrying a d arrow; when no track at
        that edge carries one (single-track side of a transition, no arrows drawn): every track reaching it"""
        side = ("left" if d == "R" else "right") if role == "entry" else ("right" if d == "R" else "left")
        at = [i for i in range(len(ts)) if edge(i, side) and not ts[i]["no"].isdigit()]
        with_arrow = [i for i in at if d in ts[i]["arrows"]]
        return with_arrow or at
    def run(d, start):                             # forward reachability: {track: first position reached}
        fwd = (lambda a, b: b >= a - 0.5) if d == "R" else (lambda a, b: b <= a + 0.5)
        reach = dict(start)
        changed = True
        while changed:
            changed = False
            for a, xa, b, xb in conns:
                for (p, xp, q, xq) in ((a, xa, b, xb), (b, xb, a, xa)):
                    if p in reach and fwd(reach[p], xp) and fwd(xp, xq):
                        if q not in reach or (xq < reach[q] if d == "R" else xq > reach[q]):
                            reach[q] = xq
                            changed = True
        return reach
    for t in ts:
        t["usable"] = t["enter"] = ""
    for d in ("L", "R"):
        entry = {i: (x0 if d == "R" else x1) for i in mains(d, "entry")}
        exits = set(mains(d, "exit"))
        reach = run(d, entry)
        for i, t in enumerate(ts):
            if i not in reach:
                continue
            t["enter"] += d                        # a train moving d can get onto it (to end / stand there)
            if t["arrows"] and d not in t["arrows"]:
                ok = False                         # a track with a drawn arrow: its normal direction only (user)
            elif t["through"] and t["arrows"]:
                ok = True
            else:
                onward = run(d, {i: reach[i]})
                ok = any(j in onward for j in exits)
            if ok:
                t["usable"] += d


def pt_seg(px, py, s):
    x0, y0, x1, y1 = s
    dx, dy = x1 - x0, y1 - y0
    L = dx * dx + dy * dy
    t = 0 if L == 0 else max(0, min(1, ((px - x0) * dx + (py - y0) * dy) / L))
    return ((px - x0 - t * dx) ** 2 + (py - y0 - t * dy) ** 2) ** 0.5


def components(segs, tol=2.2):
    """connected groups of segments (an endpoint within tol of another segment)"""
    uf = UF(len(segs))
    cell = defaultdict(list)
    for i, s in enumerate(segs):
        for x in range(int(min(s[0], s[2]) // 20), int(max(s[0], s[2]) // 20) + 1):
            for y in range(int(min(s[1], s[3]) // 20), int(max(s[1], s[3]) // 20) + 1):
                cell[(x, y)].append(i)
    for i, s in enumerate(segs):
        for (px, py) in ((s[0], s[1]), (s[2], s[3])):
            for j in cell[(int(px // 20), int(py // 20))]:
                if j != i and pt_seg(px, py, segs[j]) < tol:
                    uf.union(i, j)
    return uf


def read_page(page, codes, extra_words=()):
    r = page.rect
    words = page.get_text("words")
    # the layout column: right of the graph's right-hand station-name axis
    W = [w for w in words if w[0] > r.x1 - 430]
    lines = defaultdict(list)
    for w in W:
        lines[(w[5], w[6])].append(w)
    labels = []
    for ws in lines.values():
        ws.sort(key=lambda w: w[0])
        # join "I - 401" pieces; a text line can hold one label
        txt = " ".join(w[4] for w in ws)
        m = LABEL.match(txt.strip())
        if m:
            labels.append(dict(x0=ws[0][0], x1=ws[-1][2], y=(ws[0][1] + ws[0][3]) / 2, no=m.group(1),
                               length=float(m.group(2).replace(",", "."))))
        else:
            for w in ws:
                m = LABEL.match(w[4])
                if m:
                    labels.append(dict(x0=w[0], x1=w[2], y=(w[1] + w[3]) / 2, no=m.group(1),
                                       length=float(m.group(2).replace(",", "."))))
    if not labels:
        return []
    x_min = min(l["x0"] for l in labels) - 120
    W = W + [tuple(w[:5]) + (-1, -1) for w in extra_words]          # OCR'd names on outline pages
    segs, chev, boxes, dots, pointers = page_items(page, x_min)
    horiz = [i for i, s in enumerate(segs) if abs(s[1] - s[3]) < 0.4 and abs(s[0] - s[2]) > 4]
    uf = components(segs)
    tracks = []
    for l in labels:
        cx = (l["x0"] + l["x1"]) / 2
        best = None
        for i in horiz:
            s = segs[i]
            if min(s[0], s[2]) - 1 <= cx <= max(s[0], s[2]) + 1 and abs(s[1] - l["y"]) < 4:
                if best is None or abs(s[1] - l["y"]) < abs(segs[best][1] - l["y"]):
                    best = i
        if best is None:            # label between two line pieces: take the nearest pieces left and right
            cand = [i for i in horiz if abs(segs[i][1] - l["y"]) < 4 and
                    (abs(max(segs[i][0], segs[i][2]) - l["x0"]) < 6 or abs(min(segs[i][0], segs[i][2]) - l["x1"]) < 6)]
            if cand:
                best = cand[0]
        if best is None:
            continue
        y = segs[best][1]
        # extent: all horizontal pieces on this y connected through the label gap
        piece = [i for i in horiz if abs(segs[i][1] - y) < 0.5]
        xs = sorted((min(segs[i][0], segs[i][2]), max(segs[i][0], segs[i][2])) for i in piece)
        lo, hi = cx, cx
        changed = True
        while changed:
            changed = False
            for a, b in xs:
                if a - 8 <= hi and b + 8 >= lo and (a < lo or b > hi):
                    lo, hi = min(lo, a), max(hi, b)
                    changed = True
        ends = [c for c in chev if abs(c[1] - y) < 1.0 and (abs(c[0] - lo) < 4 or abs(c[0] - hi) < 4)]
        tracks.append(dict(l, line_y=y, lo=lo, hi=hi, comp=uf.find(best), arrows="".join(sorted({c[2] for c in ends}))))
    # diagrams = connected components (tracks that share switches); hairline-outline drawings break the
    # connections, so components whose tracks lie within 14 pt (y) and overlap in x are merged
    diag = defaultdict(list)
    for t in tracks:
        diag[t["comp"]].append(t)
    keys = sorted(diag, key=lambda k: min(t["line_y"] for t in diag[k]))
    merged = True
    while merged:
        merged = False
        for a in keys:
            for b in keys:
                if a == b or a not in diag or b not in diag:
                    continue
                A, B = diag[a], diag[b]
                gap = max(min(t["line_y"] for t in B) - max(t["line_y"] for t in A),
                          min(t["line_y"] for t in A) - max(t["line_y"] for t in B))
                ox = min(max(t["hi"] for t in A), max(t["hi"] for t in B)) - max(min(t["lo"] for t in A), min(t["lo"] for t in B))
                if gap < 14 and ox > 10 and min(len(A), len(B)) == 1:
                    diag[a] = A + B
                    del diag[b]
                    merged = True
        keys = [k for k in keys if k in diag]
    out = []
    for comp, ts in diag.items():
        y0, y1 = min(t["line_y"] for t in ts), max(t["line_y"] for t in ts)
        x0, x1 = min(t["lo"] for t in ts), max(t["hi"] for t in ts)
        ends = [w for w in W if any(q.strip(":,.").upper() in codes for q in w[4].split("/"))
                and y0 - 14 < (w[1] + w[3]) / 2 < y1 + 14 and x0 - 70 < w[0] and w[2] < x1 + 25]
        yc = (y0 + y1) / 2
        # combined end labels ("WDW/TG", "LWG/PPK"): one word per code
        ends = [w[:4] + (part,) + tuple(w[5:]) for w in ends for part in w[4].split("/") if part.strip(":,.").upper() in codes]
        own_y = [((w[1] + w[3]) / 2, w[4].strip(":,.").upper()) for w in ends if w[2] < x0 - 2]
        own = [w[4].strip(":,.").upper() for w in sorted(ends, key=lambda w: abs((w[1] + w[3]) / 2 - yc))
               if w[2] < x0 - 2 and y0 - 6 < (w[1] + w[3]) / 2 < y1 + 6
               and (w[3] - w[1] > 7 or w[5] < 0)]          # name beside the diagram (large print; OCR: any size)
        left = [w[4].strip(":,.").upper() for w in ends if x0 - 2 <= w[2] and w[0] < x0 + 30]
        right = [w[4].strip(":,.").upper() for w in ends if w[2] > x1 - 30 and w[0] >= x0 + 30]
        inside = [w[4].strip(":,.").upper() for w in ends if w[0] >= x0 + 30 and w[2] <= x1 - 30]
        marks = []
        for b in boxes:
            if x0 - 20 < b.x0 < x1 + 20 and y0 - 25 < b.y0 < y1 + 25:
                dot = any(b.x0 < dx < b.x1 and b.y0 < dy < b.y1 for dx, dy in dots)
                hl = any(abs(s[1] - s[3]) < 0.3 and b.x0 - 0.5 <= min(s[0], s[2]) and max(s[0], s[2]) <= b.x1 + 0.5
                         and b.y0 < s[1] < b.y1 for s in segs)
                if dot:
                    marks.append(dict(kind="PPKA" if hl else "PAP", y=(b.y0 + b.y1) / 2))
        ptr = [q for q in pointers if x0 - 40 < q.x1 < x0 + 8 and (y0 - 10 < q.y1 < y1 + 10 or y0 - 10 < q.y0 < y1 + 10)]
        ptr_from = None
        if ptr:
            q = ptr[0]
            ptr_from = q.y0 if y0 - 10 < q.y1 < y1 + 10 else q.y1          # the end away from the diagram
        for t in ts:
            t["through"] = t["lo"] - x0 < 6 and x1 - t["hi"] < 6        # runs over the whole diagram
            # connections drawn dashed = out of service (user, Dawuan IV / I): a siding reached only by dashed lines
            touch = lambda segs_: sum(1 for sg in segs_ for (px, py) in ((sg[0], sg[1]), (sg[2], sg[3]))
                                      if abs(py - t["line_y"]) < 1.2 and t["lo"] - 3 <= px <= t["hi"] + 3)
            t["out_of_service"] = (not t["through"]) and touch(dashed) > 0 and touch(solid) == 0
            near = [w for w in ends if abs((w[1] + w[3]) / 2 - t["line_y"]) < 9]
            t["end_l"] = [w[4].strip(":,.").upper() for w in near if abs(w[0] - t["lo"]) < 30 and w[2] < t["lo"] + 45]
            t["end_r"] = [w[4].strip(":,.").upper() for w in near if abs(w[2] - t["hi"]) < 30 and w[0] > t["hi"] - 45]
        # unlabelled running lines (main tracks drawn as curves / lines without a label, e.g. Cikarang) join the
        # direction analysis as unnamed tracks; the diagram's edges are then those of all its lines
        bx0, bx1 = x0 - 45, x1 + 45
        box = lambda q: bx0 <= min(q[0], q[2]) and max(q[0], q[2]) <= bx1 and y0 - 14 <= min(q[1], q[3]) \
            and max(q[1], q[3]) <= y1 + 14
        runs = sorted((min(q[0], q[2]), max(q[0], q[2]), q[1]) for i_ in horiz for q in [segs[i_]] if box(q)
                      and not any(abs(q[1] - t["line_y"]) < 1.2 and min(q[0], q[2]) < t["hi"] + 4
                                  and max(q[0], q[2]) > t["lo"] - 4 for t in ts))
        extra = []
        for a, b, y in runs:
            e = next((e for e in extra if abs(e["line_y"] - y) < 0.6 and a <= e["hi"] + 8 and b >= e["lo"] - 8), None)
            if e:
                e["lo"], e["hi"] = min(e["lo"], a), max(e["hi"], b)
            else:
                extra.append(dict(no="", line_y=y, lo=a, hi=b))
        extra = [e for e in extra if e["hi"] - e["lo"] > 15]
        for e in extra:
            e["arrows"] = "".join(sorted({c[2] for c in chev if abs(c[1] - e["line_y"]) < 1.0
                                          and (abs(c[0] - e["lo"]) < 4 or abs(c[0] - e["hi"]) < 4)}))
        pieces_ = [q for q in solid + dashed if box(q)]
        box_in = lambda q: x0 - 10 <= min(q[0], q[2]) and max(q[0], q[2]) <= x1 + 10 and y0 - 12 <= min(q[1], q[3]) \
            and max(q[1], q[3]) <= y1 + 12
        usable_directions(ts, [q for q in pieces_ if box_in(q)], x0, x1)
        # arrowheads beyond the labelled tracks: the ends of running lines drawn as curves (Cikarang)
        extra += [dict(no="", line_y=c[1], lo=c[0] - 4, hi=c[0] + 4, arrows=c[2]) for c in chev
                  if y0 - 14 < c[1] < y1 + 14 and (bx0 < c[0] < x0 - 3 or x1 + 3 < c[0] < bx1)]
        beyond = [e for e in extra if e["lo"] < x0 - 3 or e["hi"] > x1 + 3]
        if extra and (beyond or not any(t["usable"] for t in ts if is_roman(t["no"]))):
            # running lines that leave the labelled area (curves to arrowheads: Cikarang, Malang Kotalama): run the
            # analysis again from those ends and keep what either run allows
            before = [(t["usable"], t["enter"], t["through"]) for t in ts]
            allt = ts + extra
            ex0, ex1 = min(t["lo"] for t in allt), max(t["hi"] for t in allt)
            for t in allt:
                t["through"] = t["lo"] - ex0 < 6 and ex1 - t["hi"] < 6
            usable_directions(allt, pieces_, ex0, ex1)
            for t, (u, e, th) in zip(ts, before):
                # mains drawn through with arrows stay one-direction (user)
                t["usable"] = u if th and t["arrows"] else "".join(sorted(set(u + t["usable"])))
                if t["arrows"]:
                    t["usable"] = "".join(c for c in t["usable"] if c in t["arrows"])
                t["enter"] = "".join(sorted(set(e + t["enter"])))
                t["through"] = th
        out.append(dict(ptr_from=ptr_from, y0=y0, y1=y1, x0=x0, x1=x1, tracks=sorted(ts, key=lambda t: t["line_y"]), own=own, own_y=own_y, left=left,
                        right=right, inside=inside, marks=marks))
    # loose pieces without any name (thin lines not touching the rest) join the nearest named diagram
    out.sort(key=lambda d: d["y0"])
    named = [d for d in out if d["own"] or d["left"] or d["right"] or d["inside"]]
    for d in [d for d in out if d not in named]:
        near = [n for n in named if min(abs(d["y0"] - n["y1"]), abs(n["y0"] - d["y1"])) < 16
                and min(d["x1"], n["x1"]) - max(d["x0"], n["x0"]) > 10]
        if near:
            n = min(near, key=lambda n: min(abs(d["y0"] - n["y1"]), abs(n["y0"] - d["y1"])))
            n["tracks"] = sorted(n["tracks"] + d["tracks"], key=lambda t: t["line_y"])
            n["y0"], n["y1"] = min(n["y0"], d["y0"]), max(n["y1"], d["y1"])
            n["marks"] += d["marks"]
            out.remove(d)
    return out


def assign_station(diagram, nb, axis):
    """the station of a diagram: the code whose neighbours (infra.db sections) are printed at the diagram's ends;
    axis = station codes of the graph's own name column near the diagram (fallback)"""
    cand = diagram["left"] + diagram["right"] + diagram["inside"] + axis
    best, score = None, -1
    for x in dict.fromkeys(cand):
        sc = len(nb[x] & (set(cand) - {x})) * 2 + (x in axis) + (x in diagram["inside"])
        if sc > score:
            best, score = x, sc
    return best, score



NOTE = re.compile(r"(?:Emplasemen|Empl\.?)?\s*([A-Z][a-z]{1,4})\s+[Pp]eriksa(?:\s+pada)?(?:\s+[Ll]intas\s+(\S+))?")


def reference_notes(page, codes, extra_words=()):
    """'Emplasemen Kh periksa pada lintas Ma-Cp': the station's diagram is drawn on another line (user: skip it
    here). Returns [(code, y, line text, note)]"""
    r = page.rect
    lines = defaultdict(list)
    for w in page.get_text("words"):
        if w[0] > r.x1 - 430:
            lines[(w[5], w[6])].append(w)
    out = []
    for ws in lines.values():
        ws.sort(key=lambda w: w[0])
        t = " ".join(w[4] for w in ws)
        m = NOTE.search(t)
        if m and m.group(1).upper() in codes:
            out.append((m.group(1).upper(), (ws[0][1] + ws[0][3]) / 2, m.group(2) or "", t))
    return out


def label_fixes(path="manual/layout_label_fixes.csv"):
    """misprinted end labels on the diagrams: page, station, printed, correct"""
    import csv
    import os
    if not os.path.exists(path):
        return []
    return [r for r in csv.DictReader(open(path)) if r.get("page")]


def station_fixes(path="manual/layout_station_fixes.csv"):
    """diagrams whose station the reader cannot tell (user): page, y (top track of the diagram, +-15 pt), station"""
    import csv
    import os
    if not os.path.exists(path):
        return []
    return [r for r in csv.DictReader(open(path)) if r.get("page")]


def read_all(doc, inf, rows_by_page, ocr=None, fixes=None):
    """inf: infra.db connection (stations, sections); rows_by_page: {page: [(code, y)]} station rows of the graph
    sheet (build_infra.page_profile); ocr: build_infra.OCR for sheets that draw names as outlines"""
    codes = {c for (c,) in inf.execute("select code from stations")}
    adj = defaultdict(set)
    for a, b in inf.execute("select a, b from sections"):
        adj[a].add(b)
        adj[b].add(a)
    halts = {c for (c,) in inf.execute("select code from stations where kind in ('halt', 'sheet only')")}
    nb = defaultdict(set)              # neighbours as the diagrams name them: the next stations, stepping over halts
    hops = defaultdict(dict)           # stops between two stations (any kind; some halts are listed as stations)
    for x in list(adj):
        seen, todo = {x}, [(y, 1) for y in adj[x]]
        while todo:
            y, k = todo.pop()
            if y in seen or k > 8:
                continue
            seen.add(y)
            nb[x].add(y)
            if y in halts:
                todo += [(z, k + 1) for z in adj[y]]
        dist, frontier = {x: 0}, [x]
        for k in range(1, 5):
            frontier = [z for y in frontier for z in adj[y] if z not in dist]
            for z in frontier:
                dist.setdefault(z, k)
        hops[x] = dist
    out, refs = [], []
    for pg in GROUP_FIRST_PAGES:
        rows = rows_by_page.get(pg, [])
        page = doc[pg - 1]
        extra = []
        if ocr is not None:
            # names drawn as outlines (pages 19-24, 35-37): OCR the layout column when few names are text
            r = page.rect
            n_text = sum(1 for w in page.get_text("words") if w[0] > r.x1 - 430 and w[4].strip(":,.").upper() in codes)
            if n_text < 40:
                words, _ = ocr.read(page, pymupdf.Rect(r.x1 - 330, 0, r.x1 - 60, r.y1), tag="layouts")
                extra = [w for w in words if w[4].strip(":,.").upper() in codes]
        D = read_page(page, codes, extra)
        notes = reference_notes(page, codes)
        # a note takes its station's row in that table: no diagram there is assigned to it by position
        taken = set()
        for code, ny, _, _ in notes:
            cand = [r for r in rows if r[0] == code]
            if cand:
                taken.add(min(cand, key=lambda r: abs(r[1] - ny)))
        # two stations drawn touching each other (Semarang Poncol / Tawang): through tracks whose own end labels
        # point to different stations split the diagram; other tracks go with the nearest of those tracks
        split = []
        for dg in D:
            anchors = []
            for t in dg["tracks"]:
                if t["end_l"] and t["end_r"]:
                    c = [r[0] for r in rows if nb[r[0]] & set(t["end_l"]) and nb[r[0]] & set(t["end_r"])
                         and r[0] not in t["end_l"] + t["end_r"]]
                    if len(set(c)) == 1:
                        anchors.append((t["line_y"], c[0]))
            if len({a[1] for a in anchors}) < 2:
                split.append(dg)
                continue
            parts = defaultdict(list)
            for t in dg["tracks"]:
                parts[min(anchors, key=lambda a: abs(a[0] - t["line_y"]))[1]].append(t)
            for st, ts in parts.items():
                part = dict(dg, tracks=ts, y0=min(t["line_y"] for t in ts), y1=max(t["line_y"] for t in ts),
                            left=[x for t in ts for x in t["end_l"]], right=[x for t in ts for x in t["end_r"]],
                            own=[x for x in dg["own"] if x == st], inside=[])
                part["marks"] = [m for m in dg["marks"] if part["y0"] - 25 < m["y"] < part["y1"] + 25]
                split.append(part)
        D = split
        # italic names are halts (no diagram of their own): not an own-name label
        italic = []
        for b in page.get_text("dict")["blocks"]:
            for l in b.get("lines", []):
                for sp in l["spans"]:
                    if sp["flags"] & 2 or "Italic" in sp["font"]:
                        italic.append(((sp["bbox"][1] + sp["bbox"][3]) / 2, sp["text"].strip().upper()))
        for dg in D:
            dg["own_y"] = [(y, c) for y, c in dg["own_y"] if not any(abs(y - iy) < 2 and c == it for iy, it in italic)]
        # a name beside the diagram counts as its own name only when it is that station's row label (rotated end
        # labels and neighbouring rows' labels are not)
        for dg in D:
            dg["own"] = [c for c in dg["own"] if any(r[0] == c and any(abs(r[1] - y) < 5 for y, c2 in dg["own_y"] if c2 == c)
                                                     for r in rows)]
        # arrows from a station's row label to its diagram drawn further down / up (small filled shapes)
        labels = [w for w in page.get_text("words") if w[4].strip(":,.").upper() in codes
                  and any(r[0] == w[4].strip(":,.").upper() and abs(r[1] - (w[1] + w[3]) / 2) < 5 for r in rows)]
        for dr in page.get_drawings():
            q = dr["rect"]
            if not (dr.get("fill") and dr["type"] == "f" and len(dr["items"]) >= 5 and q.width < 26 and 8 < q.height < 160):
                continue
            src = [w for w in labels if abs(q.x0 - w[0]) < 22 and q.x0 < w[2] + 5 and (abs(q.y0 - w[3]) < 8 or abs(q.y1 - w[1]) < 8)
                   and not (w[0] - 2 < (q.x0 + q.x1) / 2 < w[2] + 2 and w[1] - 2 < (q.y0 + q.y1) / 2 < w[3] + 2)]
            if not src:
                continue
            w = min(src, key=lambda w: abs(q.x0 - w[0]))
            down = abs(q.y0 - w[3]) < 8
            tip = (q.x1, q.y1 if down else q.y0)
            hit = [dg for dg in D if dg["x0"] - 45 < tip[0] < dg["x0"] + 60 and dg["y0"] - 25 < tip[1] < dg["y1"] + 10]
            wy = (w[1] + w[3]) / 2
            hit = [dg for dg in hit if not (dg["y0"] - 6 < wy < dg["y1"] + 6)]       # points away from its own row
            if hit:
                dg = min(hit, key=lambda dg: abs(dg["y0"] - tip[1]))
                dg["pointed"] = w[4].strip(":,.").upper()
        for f in (label_fixes() if fixes is None else fixes):
            for dg in D:
                if pg == int(f["page"]) and (dg.get("pointed") == f["station"] or f["station"] in dg["own"]) \
                        and f["printed"] in dg["left"] + dg["right"]:
                    for side in ("left", "right"):
                        dg[side] = [f["correct"] if x == f["printed"] else x for x in dg[side]]
                    for t in dg["tracks"]:
                        t["end_l"] = [f["correct"] if x == f["printed"] else x for x in t.get("end_l", [])]
                        t["end_r"] = [f["correct"] if x == f["printed"] else x for x in t.get("end_r", [])]
                    dg["fix"] = f" (label fix: {f['printed']} -> {f['correct']})"
        italic_codes = {c for _, c in italic if c in codes}
        rows_all, rows = rows, [r for r in rows if r[0] not in italic_codes]     # halts in italics: no diagram
        used = {r[0] for r in taken if sum(1 for q in rows if q[0] == r[0]) == 1}
        for dg in D:
            ends = dg["left"] + dg["right"] + dg["inside"]
            st, how = None, None
            # 1. neighbours printed at both ends, 2. one end + own name / row, 3. own name, 4. pointer arrow
            both = [r for r in rows if dg["left"] and dg["right"] and nb[r[0]] & set(dg["left"]) and nb[r[0]] & set(dg["right"])]
            one = [r for r in rows if ends and r[0] not in ends and all(nb[r[0]] & {e} for e in set(ends) - set(dg["own"]))]
            if dg.get("pointed") and (not both or dg["pointed"] in [r[0] for r in both]):
                st, how = dg["pointed"], "pointer arrow from the name"
            elif both:
                st, how = min(both, key=lambda r: abs(r[1] - (dg["y0"] + dg["y1"]) / 2))[0], "neighbours"
            elif dg["own"]:
                st, how = dg["own"][0], "own name"
            elif one:
                pick = [r for r in one if r[0] in dg["own"]]
                c = (dg["y0"] + dg["y1"]) / 2
                st, how = min(pick or one, key=lambda r: abs(r[1] - c))[0], "one side + " + ("own name" if pick else "row")
            elif dg["own"]:
                st, how = dg["own"][0], "own name"
            elif dg["ptr_from"] is not None and rows:
                st, how = min(rows, key=lambda r: abs(r[1] - dg["ptr_from"]))[0], "pointer"
            dg.update(page=pg, station=st, how=(how or "") + dg.get("fix", "") if how else how)
            if st:
                used.add(st)
        # 2. the rest: nearest unused row
        for dg in D:
            if dg["station"] is None and rows:
                c = (dg["y0"] + dg["y1"]) / 2
                free = [r for r in rows if r[0] not in used]
                free = [r for r in free if abs(r[1] - c) < 60]
                if free:
                    r = min(free, key=lambda r: abs(r[1] - c))
                    dg.update(station=r[0], how=f"nearest row ({abs(r[1] - c):.0f} pt)")
                    used.add(r[0])
        # the same diagram printed twice on a sheet (identical tracks): keep the better-founded station
        RANK = ["neighbours", "one side + own name", "own name", "one side + row", "pointer", "nearest row"]
        rank = lambda d: next((i for i, k in enumerate(RANK) if (d["how"] or "").startswith(k)), 9)
        sig = lambda d: tuple(sorted((t["no"], t["length"]) for t in d["tracks"]))
        bysig = defaultdict(list)
        for dg in D:
            if len(dg["tracks"]) >= 3:
                bysig[sig(dg)].append(dg)
        for group in bysig.values():
            best = min(group, key=rank)
            for dg in group:
                dg["station"], dg["how"] = best["station"], best["how"]
        for f in station_fixes():
            for dg in D:
                if pg == int(f["page"]) and abs(dg["y0"] - float(f["y"])) < 15:
                    dg["station"], dg["how"] = f["station"], "manual (user)"
        # pieces of one station (some sheets draw the two halves of a double-track station unconnected) and
        # repeated copies of a diagram on the same sheet
        keep = []
        for dg in sorted(D, key=lambda d: d["y0"]):
            prev = next((k for k in keep if k["station"] == dg["station"] and dg["station"]), None)
            if prev is not None:
                sig = lambda d: sorted((t["no"], t["length"]) for t in d["tracks"])
                if sig(prev) == sig(dg):
                    continue                                            # a copy
                if dg["y0"] - prev["y1"] < 40:
                    prev["tracks"] += dg["tracks"]
                    prev["y1"], prev["marks"] = max(prev["y1"], dg["y1"]), prev["marks"] + dg["marks"]
                    prev["left"] += dg["left"]; prev["right"] += dg["right"]
                    continue
            keep.append(dg)
        out += keep
        refs += [dict(station=c, page=pg, y=y, line=ln, note=t) for c, y, ln, t in notes]
    return out, refs


SCHEMA = """
CREATE TABLE station_layout_refs(station TEXT, line_id TEXT, page INTEGER, y REAL, see_line TEXT, note TEXT);
CREATE TABLE station_layouts(station TEXT, line_id TEXT, page INTEGER, y REAL, evidence TEXT, left_ends TEXT, right_ends TEXT,
                             roman_tracks INTEGER, yard_tracks INTEGER, out_of_service_tracks INTEGER,
                             longest_roman_m REAL, ppka INTEGER, pap INTEGER);
CREATE TABLE station_tracks(station TEXT, line_id TEXT, page INTEGER, seq INTEGER, track TEXT, number INTEGER, kind TEXT,
                            length_m REAL, arrows TEXT, towards TEXT, through INTEGER, out_of_service INTEGER,
                            usable TEXT, usable_towards TEXT, enter TEXT);
"""


def manual_tracks(diagrams, path="manual/layout_tracks.csv"):
    """tracks the reader cannot get (labels too small / no labels), user: page, station, track, length_m. Added to
    the station's diagram on that page (or a new diagram when there is none); a track read from the sheet with the
    same number is replaced"""
    import csv
    import os
    if not os.path.exists(path):
        return diagrams
    for r in csv.DictReader(open(path)):
        if not r.get("page"):
            continue
        pg, st = int(r["page"]), r["station"]
        dg = next((d for d in diagrams if d.get("station") == st and d["page"] == pg), None)
        if dg is None:
            dg = dict(station=st, page=pg, y0=0.0, y1=0.0, how="manual (user)", tracks=[], left=[], right=[], own=[],
                      inside=[], marks=[])
            diagrams.append(dg)
        elif not dg["how"].endswith("+ manual tracks"):
            dg["how"] += " + manual tracks"
        dg["tracks"] = [t for t in dg["tracks"] if t["no"] != r["track"]] + [
            dict(no=r["track"], length=float(r["length_m"]) if r["length_m"] else None, arrows="", through=False,
                 line_y=9e9)]
    return diagrams


def write(inf, diagrams, refs=()):
    """station_layouts: one row per diagram; station_tracks: its tracks top to bottom. kind 'roman' = main or siding
    track, 'yard' = Arabic-numbered stabling / yard track; out_of_service = connected only by dashed lines (user);
    roman_tracks / longest_roman_m count usable (in-service) Roman tracks only. usable: directions (L / R on the
    sheet) in which a train can stand on the track and continue (forward moves over the drawn switches; through
    mains only in their arrow direction); usable_towards: the same as neighbour stations; enter: directions in
    which a train can get onto it (also dead ends / termini, where trains end or reverse). arrows: normal running direction drawn at the track ends
    (L / R on the sheet, LR = both, '' = none drawn); towards: the neighbour station(s) of that direction.
    A station can have different diagrams on different lines (separate levels / isolated tracks): one row per line.
    station_layout_refs: 'periksa lintas ...' notes, the station's diagram is on the line named there."""
    inf.executescript(SCHEMA)
    line_of = {}
    for lid, pages in inf.execute("select line_id, pages from lines"):
        for pg in pages.split():
            line_of[int(pg)] = lid
    for r in refs:
        inf.execute("INSERT INTO station_layout_refs VALUES(?,?,?,?,?,?)",
                    (r["station"], line_of.get(r["page"]), r["page"], round(r["y"], 1), r["line"] or None, r["note"]))
    diagrams = manual_tracks(diagrams)
    # a 'periksa lintas' note instead of a diagram: the diagram on the other line is the same (user), copy it
    have = {(d["station"], line_of.get(d["page"])) for d in diagrams if d.get("station")}
    copies = []
    for r in refs:
        ln = line_of.get(r["page"])
        if (r["station"], ln) in have:
            continue
        src = [d for d in diagrams if d.get("station") == r["station"]]
        if src:
            d = max(src, key=lambda d: len(d["tracks"]))
            copies.append(dict(d, how=f"copy of line {line_of.get(d['page'])} page {d['page']} ({r['note']})",
                               copy_line=ln))
            have.add((r["station"], ln))
    for d in [x for x in diagrams if x.get("station")] + copies:
        ln = d.get("copy_line") or line_of.get(d["page"])
        tr = d["tracks"]
        rom = [t for t in tr if is_roman(t["no"]) and not t.get("out_of_service")]      # usable main / siding
        oos = sum(1 for t in tr if t.get("out_of_service"))
        left = list(dict.fromkeys(x for x in d["left"] if x != d["station"]))
        right = list(dict.fromkeys(x for x in d["right"] if x != d["station"]))
        inf.execute("INSERT INTO station_layouts VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (d["station"], ln, d["page"], round(d["y0"], 1), d["how"], " ".join(left), " ".join(right), len(rom),
                     sum(1 for t in tr if not is_roman(t["no"])), oos, max((t["length"] for t in rom if t["length"] is not None), default=None),
                     int(any(m["kind"] == "PPKA" for m in d["marks"])), int(any(m["kind"] == "PAP" for m in d["marks"]))))
        for k, t in enumerate(tr, 1):
            tw = " ".join(([left[0]] if "L" in t["arrows"] and left else []) + ([right[0]] if "R" in t["arrows"] and right else []))
            ut = " ".join(([left[0]] if "L" in t.get("usable", "") and left else []) +
                          ([right[0]] if "R" in t.get("usable", "") and right else []))
            inf.execute("INSERT INTO station_tracks VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        (d["station"], ln, d["page"], k, t["no"], roman(t["no"]) if is_roman(t["no"]) else int(re.sub(r"\D", "", t["no"])),
                         "roman" if is_roman(t["no"]) else "yard", t["length"], t["arrows"], tw or None,
                         int(t["through"]), int(bool(t.get("out_of_service"))), t.get("usable"), ut or None,
                         t.get("enter")))


if __name__ == "__main__":
    import pickle
    doc = pymupdf.open(sys.argv[1] if len(sys.argv) > 1 else "GAPEKA-JAWA-2026.pdf")
    inf = sqlite3.connect(sys.argv[2] if len(sys.argv) > 2 else "infra.db")
    rows = pickle.load(open(sys.argv[3], "rb")) if len(sys.argv) > 3 else {}
    D, refs = read_all(doc, inf, rows)
    print(len(D), "diagrams,", sum(len(d["tracks"]) for d in D), "tracks,", len(refs), "see-other-line notes")
