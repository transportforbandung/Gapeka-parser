"""KA Dinas times read from the graph sheets (PDF pages 7-51).

KA Dinas (R = trainset, L = locomotive moves) have no printed times between their start and end: they are only drawn
on the graph. Regular trains keep their timetable times; only KA Dinas are read here (user).

Method (guided by gapeka.db): every dashed stroke segment in the graph area is put into (absolute time, y) space with
the page's hour grid (section_symbols.grid_axes); pieces cut at page edges are joined. A segment whose ends lie on two
adjacent station rows (infra.db sheet_pairs) is a 'leg' between those stations with the times where it crosses the
rows. For each KA Dinas in gapeka.db the route (its stops, expanded through sheet-only rows) is followed leg by leg,
starting within a few minutes of its index departure and always taking the next leg that continues where the
previous one ended. The train's label (e.g. 'R177-2') near the chosen line confirms it.

Flags: KA Dinas in the timetable not found on the graph, and KA Dinas labels on the graph with no timetable train.
"""
import collections
import re
import sqlite3

import pymupdf

import section_symbols as S

LABEL = re.compile(r"(?:LL|DL|DR|[RL])\d+[A-Z]?(-\d+)?")


def label_no(w):
    """graph label -> timetable number: 'LL101' (lok langsir) and 'DL2509-2' are L101 / L2509-2; 'DR345' -> R345."""
    return w[1:] if w[:2] in ("LL", "DL", "DR") else w


def page_segments(page, pno, row_ys=()):
    """Dashed stroke segments of the graph area as (t0, y0, t1, y1, x0, x1) with t in minutes (absolute, day)."""
    axes = S.grid_axes(page, pno)
    if not axes:
        return [], []
    g = next(g for g in S.GROUPS if g[0] <= pno <= g[1])
    hpp = 24 / (g[1] - g[0] + 1) if len(axes) == 1 or g[1] > g[0] else 12

    def axis_for(y):
        inside = [a for a in axes if a["y0"] - 30 <= y <= a["y1"] + 30]
        return min(inside or axes, key=lambda a: min(abs(y - a["y0"]), abs(y - a["y1"])) if not inside else 0)

    segs = []

    def add(a, b, kind):
        if abs(a.y - b.y) < 2:
            return
        ax = axis_for((a.y + b.y) / 2)
        right = ax["x0"] + ax["spacing"] * hpp
        if min(a.x, b.x) < ax["x0"] - 2 or max(a.x, b.x) > right + 2:
            return
        ta = (ax["h0"] + (a.x - ax["x0"]) / ax["spacing"]) * 60
        tb = (ax["h0"] + (b.x - ax["x0"]) / ax["spacing"]) * 60
        segs.append(dict(t0=ta, y0=a.y, t1=tb, y1=b.y, x0=a.x, x1=b.x, page=pno, kind=kind,
                         edge_l=ax["h0"] * 60, edge_r=(ax["h0"] + hpp) * 60))

    for dr in page.get_drawings():
        if dr["type"] == "f" and len(dr["items"]) >= 12 and dr["rect"].height > 8:
            # a line drawn as a filled outline (dashes as shapes, bends at the rows): sample its centre where it
            # crosses each station row (thin there: crossings within 3 pt), join consecutive row crossings
            r = dr["rect"]
            edges = []
            for it in dr["items"]:
                pts = [p for p in it[1:] if isinstance(p, pymupdf.Point)]
                if it[0] == "c":
                    pts = [pts[0], pts[-1]]
                edges += list(zip(pts, pts[1:]))
            cross = []
            for y in row_ys:
                if not (r.y0 - 1.5 <= y <= r.y1 + 1.5):
                    continue
                found = None
                for dy in (0, 0.6, -0.6, 1.2, -1.2, 1.8, -1.8, 2.5, -2.5):
                    yy = min(max(y + dy, r.y0 + 0.01), r.y1 - 0.01)
                    xs = [p.x + (q.x - p.x) * (yy - p.y) / (q.y - p.y) for p, q in edges
                          if min(p.y, q.y) <= yy <= max(p.y, q.y) and p.y != q.y]
                    if xs:
                        found = xs
                        break
                if found and max(found) - min(found) < 8:          # arrowheads at a row widen the outline
                    found.sort()
                    cross.append(pymupdf.Point(found[len(found) // 2], y))
            # where the outline meets the page's left / right graph edge (line continues on the next page)
            ax = axis_for((r.y0 + r.y1) / 2)
            for xe in (ax["x0"], ax["x0"] + ax["spacing"] * hpp):
                if r.x0 - 1.5 <= xe <= r.x1 + 1.5:
                    near = [p for p, q in edges for p in (p, q) if abs(p.x - xe) < 1.6]
                    if near and max(p.y for p in near) - min(p.y for p in near) < 4:
                        cross.append(pymupdf.Point(xe, sum(p.y for p in near) / len(near)))
            cross.sort(key=lambda p: p.y)
            for a, b in zip(cross, cross[1:]):
                add(a, b, "filled")
            continue
        if dr["type"] != "s" or not re.search(r"\[\s*[\d.]", dr.get("dashes") or ""):
            continue
        w = dr.get("width") or 0
        if w < 0.6:
            continue
        for it in dr["items"]:
            if it[0] != "l":
                continue
            add(it[1], it[2], "dashed")
    labels = [(label_no(w[4]), (w[0] + w[2]) / 2, (w[1] + w[3]) / 2, pno, w[4]) for w in page.get_text("words")
              if LABEL.fullmatch(w[4]) and w[0] > 360]
    return segs, labels


def join_edges(segs):
    """Join pieces of one line cut at the page edge (end at the right edge of page n, start at the left edge of
    page n + 1 at the same y)."""
    ends = [s for s in segs if abs(max(s["t0"], s["t1"]) - s["edge_r"]) < 0.6]
    starts = [s for s in segs if abs(min(s["t0"], s["t1"]) - s["edge_l"]) < 0.6]
    used = set()
    for e in ends:
        ye = e["y0"] if e["t0"] > e["t1"] else e["y1"]
        for s in starts:
            g = next(g for g in S.GROUPS if g[0] <= e["page"] <= g[1])
            nxt = e["page"] + 1 if e["page"] < g[1] else g[0]
            if id(s) in used or s["page"] != nxt:
                continue
            ys = s["y0"] if s["t0"] < s["t1"] else s["y1"]
            if abs(ys - ye) < 1.5:
                # extend e by s
                pts = sorted([(e["t0"], e["y0"]), (e["t1"], e["y1"]), (s["t0"], s["y0"]), (s["t1"], s["y1"])])
                e["t0"], e["y0"], e["t1"], e["y1"] = pts[0][0], pts[0][1], pts[-1][0], pts[-1][1]
                used.add(id(s))
                break
    return [s for s in segs if id(s) not in used]


def read(pdf_path, infra_db, tt_db, tol_start=6.0):
    doc = pymupdf.open(pdf_path)
    inf = sqlite3.connect(infra_db)
    tt = tt_db if isinstance(tt_db, sqlite3.Connection) else sqlite3.connect(tt_db)
    pairs = collections.defaultdict(list)          # page -> [(a, b, y_a, y_b)]
    for pg, a, b, ya, yb in inf.execute("select page, a, b, y_a, y_b from sheet_pairs"):
        pairs[pg].append((a, b, ya, yb))
    segs, labels = [], []
    for pno in range(7, 52):
        s, l = page_segments(doc[pno - 1], pno, sorted({y for _, _, ya, yb in pairs[pno] for y in (ya, yb)}))
        segs += s
        labels += l
    segs = join_edges(segs)

    # legs: directed (from, to) -> [(t_from, t_to, seg)]
    legs = collections.defaultdict(list)
    for s in segs:
        lo, hi = sorted([(s["y0"], s["t0"]), (s["y1"], s["t1"])])

        def t_at(y):
            return lo[1] + (hi[1] - lo[1]) * (y - lo[0]) / (hi[0] - lo[0])
        for a, b, ya, yb in pairs[s["page"]]:
            # a segment may run over several rows (halts crossed without a break): every pair inside its span
            if min(ya, yb) >= lo[0] - 3 and max(ya, yb) <= hi[0] + 3:
                ta, tb = t_at(ya), t_at(yb)
                if ta <= tb:
                    legs[(a, b)].append((ta, tb, s))
                else:
                    legs[(b, a)].append((tb, ta, s))
    # sheet adjacency for route expansion through sheet-only rows
    adj = collections.defaultdict(set)
    for pg in pairs:
        for a, b, _, _ in pairs[pg]:
            adj[a].add(b)
            adj[b].add(a)

    def expand(u, v):
        """shortest row path u -> v whose inner rows are sheet-only ('?')."""
        prev, q = {u: None}, collections.deque([u])
        while q:
            x = q.popleft()
            if x == v:
                break
            for y in adj[x]:
                if y not in prev and (y == v or y.startswith("?")):
                    prev[y] = x
                    q.append(y)
        if v not in prev:
            return None
        path = [v]
        while prev[path[-1]] is not None:
            path.append(prev[path[-1]])
        return path[::-1]

    results, missing = {}, []
    trains = tt.execute("select no from trains where type='dinas'").fetchall()
    for (no,) in trains:
        st = tt.execute("select seq, station, arr_s, dep_s from stops where train_no=? order by seq", (no,)).fetchall()
        dep0 = st[0][3] if st[0][3] is not None else st[0][2]
        arr1 = st[-1][2] if st[-1][2] is not None else st[-1][3]
        rows = []
        ok = True
        for (_, u, _, _), (_, v, _, _) in zip(st, st[1:]):
            p = expand(u, v)
            if p is None:
                ok = False
                break
            rows += p if not rows else p[1:]
        if not ok or len(rows) < 2 or dep0 is None:
            missing.append((no, "route not on the graph" if not ok else "no start time", None))
            continue
        # beam search over the legs: start within tol_start of the index departure, every next leg continues where
        # the previous ended (a small gap = through run, larger = a stop); score = gaps + end deviation
        steps = list(zip(rows, rows[1:]))

        def run(k0):
            """beam from step k0; returns (legs, score, chain) of the best (possibly partial) chain"""
            beam, best_partial = [(0.0, None, [])], None
            for k in range(k0, len(steps)):
                a, b = steps[k]
                nb = []
                for score, t_prev, ch in beam:
                    for ta, tb, s in legs.get((a, b), []):
                        for shift in (0, 1440, -1440):
                            ta2, tb2 = ta + shift, tb + shift
                            if t_prev is None:
                                d = abs(ta2 - dep0 / 60)
                                if d <= tol_start + (10 if k0 else 0):
                                    nb.append((d + 3 * k0, tb2, ch + [(a, b, ta2, tb2, s)]))
                            else:
                                gap = ta2 - t_prev
                                if -0.6 <= gap <= 10:
                                    pen = abs(gap) if abs(gap) <= 1 else 2 + gap
                                    nb.append((score + pen, tb2, ch + [(a, b, ta2, tb2, s)]))
                if not nb:
                    break
                nb.sort(key=lambda x: x[0])
                beam = nb[:40]
            beam = [x for x in beam if x[2]]
            if not beam:
                return None

            def total(x):
                if len(x[2]) + k0 < len(steps) or arr1 is None:
                    return x[0]
                dev = abs(x[1] - arr1 / 60)
                return x[0] + 2 * min(dev, abs(dev - 1440))
            bx = min(beam, key=total)
            return (len(bx[2]), total(bx), bx[2])

        found = [r for r in (run(k0) for k0 in range(len(steps))) if r]
        fail = (steps[0][0], steps[0][1], len(legs.get(steps[0], [])), None)
        chain = max(found, key=lambda r: (r[0], -r[1]))[2] if found else None
        partial = bool(chain) and len(chain) < len(steps)
        if not chain:
            missing.append((no, "no line on the graph", fail))
            continue
        end_dev = chain[-1][3] - (arr1 / 60 if arr1 is not None else chain[-1][3])
        # label confirmation: the train's label near one of the chosen segments
        confirmed = False
        for name, lx, ly, lpg, _ in labels:
            if name != no:
                continue
            for _, _, _, _, s in chain:
                if s["page"] != lpg:
                    continue
                x0, y0, x1, y1 = s["x0"], s["y0"], s["x1"], s["y1"]
                if min(y0, y1) - 15 <= ly <= max(y0, y1) + 15:
                    f = (ly - y0) / (y1 - y0) if y1 != y0 else 0
                    xl = x0 + f * (x1 - x0)
                    if abs(xl - lx) < 40:
                        confirmed = True
        results[no] = dict(chain=chain, end_dev=end_dev, confirmed=confirmed, partial=partial, dep0=dep0 / 60,
                           arr1=None if arr1 is None else arr1 / 60)
    tt_nos = {n for (n,) in trains}
    graph_only = sorted({(raw, lpg) for name, _, _, lpg, raw in labels if name not in tt_nos})
    return results, missing, graph_only, labels


def apply(db, pdf_path, infra_db):
    """Write graph times for KA Dinas into gapeka.db (db = connection): tables dinas_graph (every row a matched line
    crosses) and dinas_graph_flags; passing times in halt_est become 'dinas-graph' where the match is trusted
    (label beside the line, or the train has no label anywhere). Printed start / end times are kept."""
    res, missing, graph_only, labels = read(pdf_path, infra_db, db)
    labelled = {n for n, *_ in labels}
    db.execute("DROP TABLE IF EXISTS dinas_graph")
    db.execute("DROP TABLE IF EXISTS dinas_graph_flags")
    db.execute("CREATE TABLE dinas_graph(train_no TEXT, station TEXT, time_s INTEGER, page INTEGER, status TEXT)")
    db.execute("CREATE TABLE dinas_graph_flags(train_no TEXT, issue TEXT, detail TEXT)")
    n_used = 0
    for no, r in res.items():
        status = "confirmed" if r["confirmed"] else ("unlabelled" if no not in labelled else "doubtful")
        # times at rows (first time a row is reached)
        tl = {}
        for a, b, ta, tb, s in r["chain"]:
            tl.setdefault(a, (ta, s["page"]))
            tl.setdefault(b, (tb, s["page"]))
        stops = db.execute("select seq, station, arr_s, dep_s from stops where train_no=? order by seq", (no,)).fetchall()
        ref = {st: (h if h is not None else None) for _, st, *_ in stops for h in [None]}
        est = dict(((seq, st), t) for seq, st, t in db.execute(
            "select seq, station, arr_s from halt_est where train_no=?", (no,)))
        for st, (t, pg) in tl.items():
            db.execute("INSERT INTO dinas_graph VALUES(?,?,?,?,?)", (no, st, int(round(t * 60)), pg, status))
        if status == "doubtful":
            db.execute("INSERT INTO dinas_graph_flags VALUES(?,?,?)", (no, "label elsewhere: matched line doubtful",
                                                                        "model times kept"))
            continue
        if r["partial"]:
            db.execute("INSERT INTO dinas_graph_flags VALUES(?,?,?)", (no, "only part of the route drawn",
                                                                        " ".join(tl)))
        if abs(r["end_dev"]) > 3 and not r["partial"]:
            db.execute("INSERT INTO dinas_graph_flags VALUES(?,?,?)", (no, "graph end time differs from index",
                                                                        f"{r['end_dev']:+.1f} min"))
        t_dep = stops[0][3] if stops[0][3] is not None else stops[0][2]
        t_arr = stops[-1][2] if stops[-1][2] is not None else stops[-1][3]
        last, rejected = t_dep, []
        for seq, st, a, d in stops[1:-1]:
            if st not in tl:
                continue
            t = tl[st][0] * 60
            ref_t = est.get((seq, st)) or (t_dep or 0)
            t += 86400 * round((ref_t - t) / 86400)         # same day as the model / index times
            # guard: inside the index window (5 min slack) and never backwards along the route
            if t_dep is not None and t_arr is not None and not (t_dep - 300 <= t <= t_arr + 300 and t >= last - 60):
                rejected.append(st)
                continue
            last = t
            db.execute("UPDATE halt_est SET arr_s=?, dep_s=?, method='dinas-graph' WHERE train_no=? AND seq=?",
                       (int(round(t)), int(round(t)), no, seq))
            n_used += 1
        if rejected:
            db.execute("INSERT INTO dinas_graph_flags VALUES(?,?,?)", (no, "graph time outside the index window "
                                                                        "(model kept)", " ".join(rejected)))
    for no, why, f in missing:
        db.execute("INSERT INTO dinas_graph_flags VALUES(?,?,?)", (no, "in the timetable, not found on the graph",
                                                                    f"{f[0]}-{f[1]}" if f else why))
    for raw, pg in graph_only:
        db.execute("INSERT INTO dinas_graph_flags VALUES(?,?,?)", (raw, "on the graph, not in the timetable",
                                                                    f"page {pg}"))
    db.commit()
    return res, missing, graph_only, n_used


if __name__ == "__main__":
    res, missing, graph_only, labels = read("GAPEKA-JAWA-2026.pdf", "infra.db", "gapeka.db")
    conf = sum(r["confirmed"] for r in res.values())
    bad_end = [(n, round(r["end_dev"], 1)) for n, r in res.items() if abs(r["end_dev"]) > 3 and not r["partial"]]
    print(f"KA Dinas found on the graph: {len(res)} (label confirmed {conf}, partial route {sum(r['partial'] for r in res.values())}); not found: {len(missing)}")
    print("end time off the index by > 3 min:", len(bad_end), bad_end[:15])
    print("missing:", collections.Counter(m[1] for m in missing), missing[:20])
    print("graph labels without a timetable KA Dinas:", len(graph_only), graph_only[:30])
