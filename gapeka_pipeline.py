#!/usr/bin/env python3
"""Gapeka pipeline: Gapeka PDF(s) -> one SQLite database.

usage:  python3 gapeka_pipeline.py OUT.db FILE_OR_FOLDER [FILE_OR_FOLDER ...] [--dwell 30] [--no-graph]

Any mix of PDFs may be given (timetable pages and graph pages can be in the same or in
different files). Pages are classified automatically:
  * timetable page  : contains "Stasiun dan Perhentian"
  * graph page      : page width > 1000 pt
Give files in page order; a train that continues on the next file is re-joined by row number.

Steps
  1  parse timetable pages          -> stations, trains, stops, notes
  2  read station distances         -> station_links   (graph profile column "jarak antar stasiun")
  3  estimate Berh/Sb halt times    -> halt_fit, halt_est, view schedule
  4  link crossing/overtake notes   -> constraints
  5  extract graph train lines      -> graph_pages, graph_trains, graph_crossings   (optional)

Halt model (general, no per-halt input):
  run time of a leg between two timed stops  T = a*km + b      (robust least squares)
  fitted on legs with no halt/pass between, per service label, falling back to one pooled
  fit over all services when a service has too little data.
  For a leg containing halts: (T - dwell*n_halts) is split over the sub-legs in proportion
  to a*km_i + b; each halt gets arr = running time, dep = arr + dwell.
  If distances are unknown the sub-legs are weighted equally (method 'equal').
  All halt rows are ESTIMATES (method column), timetable prints no time for them.
Needs: pip install pymupdf numpy
"""
import glob
import heapq
import os
import re
import sqlite3
import sys
from collections import defaultdict

import numpy as np

try:
    import pymupdf              # PyMuPDF 1.24.3 and newer
except ImportError:
    try:
        import fitz as pymupdf  # older PyMuPDF
    except ImportError:
        sys.exit("PyMuPDF is missing. Install it with:  python -m pip install pymupdf numpy")

# ---------------------------------------------------------------- 1. timetable parsing
TIME_RE = re.compile(r"^\d{2}:\d{2}:\d{2}$")
HEAD_RE = re.compile(r"^KA\s+(\S+)\s+\((.+?)\)\s*(?:Lintas Pelayanan|Relasi)\s+(.+)$")
STN_RE = re.compile(r"^(.*?)\s*\(([A-Z]+)\)$")
# column boundaries (PDF points) on the 595 pt wide timetable pages
COLS = [("no", 0, 69), ("station", 69, 215), ("vmax", 215, 262),
        ("arr", 262, 305), ("dep", 305, 357), ("note", 357, 9999)]


def col_of(x0):
    for name, a, b in COLS:
        if a <= x0 < b:
            return name
    return "note"


def secs(s):
    h, m, sec = map(int, s.split(":"))
    return h * 3600 + m * 60 + sec


def parse_notes(text):
    """Keterangan cell -> [(symbol, other_train, own)]. own=False for '(ka 415x)' partner views."""
    out = []
    if not text:
        return out
    parts, depth, cur = [], 0, ""
    for ch in text:
        depth += (ch == "(") - (ch == ")")
        if ch == "," and depth == 0:
            parts.append(cur)
            cur = ""
        else:
            cur += ch
    parts.append(cur)
    for p in parts:
        p = p.strip()
        if p.startswith("("):
            m = re.search(r"ka\s*([0-9]+[A-Z]*(?:\s*JJ)?)\s*(x|//|=)?", p)
            if m:
                out.append((m.group(2) or "x", m.group(1).strip(), False))
            continue
        m = re.match(r"^(x|//|=)?\s*ka\s*([0-9]+[A-Z]*(?:\s*JJ)?)", p)
        if m:
            out.append((m.group(1) or "?", m.group(2).strip(), True))
    return out


def page_rows(page):
    words = page.get_text("words")
    lines = defaultdict(list)
    for w in words:
        lines[round((w[1] + w[3]) / 2)].append(w)
    items, anchors = [], []
    for y, ws in sorted(lines.items()):
        ws.sort(key=lambda w: w[0])
        text = " ".join(w[4] for w in ws)
        m = HEAD_RE.match(text)
        if m and ws[0][0] < 74:
            items.append(("head", y, {"no": m.group(1), "service": m.group(2), "route": m.group(3).strip()}))
        elif ws[0][0] < 74 and ws[0][4].isdigit() and ws[0][2] < 76:
            anchors.append((y, ws[0][4]))
    rows = {y: {"no": int(n), "cells": defaultdict(list)} for y, n in anchors}
    ay = sorted(rows)
    stray = []
    for y, ws in lines.items():
        if not ay:
            continue
        near = min(ay, key=lambda a: abs(a - y))
        if abs(near - y) > 6:
            stray.append((y, near, ws))          # e.g. second line of a wrapped station name
            continue
        for w in sorted(ws, key=lambda w: (w[1], w[0])):
            c = col_of(w[0])
            if c == "no" and w is ws[0] and abs(near - y) <= 1 and w[4].isdigit():
                continue
            rows[near]["cells"][c].append(w[4])
    # wrapped station names: stray station-column lines (<= 14 pt from a row) complete a name that is
    # missing or does not yet end with its "(CODE)"
    for y, near, ws in sorted(stray):
        if abs(near - y) > 14:
            continue
        sw = [w[4] for w in sorted(ws, key=lambda w: w[0]) if col_of(w[0]) == "station"]
        if not sw:
            continue
        cur = rows[near]["cells"]["station"]
        if cur and re.search(r"\([A-Z]+\)$", " ".join(cur)):
            continue                                  # name already complete
        if y < near:
            rows[near]["cells"]["station"] = sw + cur
        else:
            cur.extend(sw)
    for y in ay:
        cells = {k: " ".join(v) for k, v in rows[y]["cells"].items()}
        cells.pop("no", None)
        items.append(("row", y, {**cells, "no": rows[y]["no"]}))
    items.sort(key=lambda it: it[1])
    return items


def classify(pdfs):
    """-> list of (pdf_index, page_number, page, 'timetable'|'graph'|'other') in file order"""
    out = []
    for fi, path in enumerate(pdfs):
        doc = pymupdf.open(path)
        for pno, page in enumerate(doc, 1):
            if page.rect.width > 1000:
                kind = "graph"
            elif "Stasiun dan Perhentian" in page.get_text():
                kind = "timetable"
            else:
                kind = "other"
            out.append((fi, pno, page, kind))
    return out


def parse_timetables(pages):
    trains, cur, seen = [], None, set()
    orphans = 0
    for fi, pno, page, kind in pages:
        if kind != "timetable":
            continue
        for k, y, d in page_rows(page):
            if k == "head":
                if d["no"] in seen:          # same train given twice (overlapping extracts): ignore 2nd copy
                    cur = None
                    continue
                seen.add(d["no"])
                cur = {"no": d["no"], "service": d["service"], "route": d["route"], "page": f"{fi}:{pno}", "rows": []}
                trains.append(cur)
            elif cur is not None:
                if d["no"] == len(cur["rows"]) + 1:     # rows are numbered 1..n, continuing over pages/files
                    d["page"] = f"{fi}:{pno}"
                    cur["rows"].append(d)
                else:
                    cur = None
                    orphans += 1
            else:
                orphans += 1
    return trains, orphans


def build_tables(db, trains):
    db.executescript("""
    DROP TABLE IF EXISTS stations; DROP TABLE IF EXISTS trains; DROP TABLE IF EXISTS stops; DROP TABLE IF EXISTS notes;
    CREATE TABLE stations(code TEXT PRIMARY KEY, name TEXT);
    CREATE TABLE trains(no TEXT PRIMARY KEY, service TEXT, route TEXT, first_page TEXT, n_stops INTEGER);
    CREATE TABLE stops(train_no TEXT, seq INTEGER, station TEXT, vmax INTEGER, kind TEXT, arr_s INTEGER,
                       dep_s INTEGER, note TEXT, page TEXT, PRIMARY KEY(train_no, seq));
    CREATE TABLE notes(train_no TEXT, seq INTEGER, station TEXT, symbol TEXT, other_train TEXT, own INTEGER);
    """)
    problems = []
    for t in trains:
        day, prev = 0, None
        for r in t["rows"]:
            m = STN_RE.match(r.get("station", ""))
            if not m:
                problems.append((t["no"], r["no"], "bad station", {k: v for k, v in r.items() if k != "page"}))
                continue
            name, code = m.groups()
            db.execute("INSERT OR IGNORE INTO stations VALUES(?,?)", (code, name))
            a, d = r.get("arr", "-"), r.get("dep", "-")
            if a == "Ls":
                kind = "pass"
            elif a == "Berh":
                kind = "halt"
            else:
                kind = "stop" if TIME_RE.match(a) else "origin_or_end"
            vals = []
            for v in (a, d):
                if TIME_RE.match(v):
                    s = secs(v) + day * 86400
                    if prev is not None and s < prev - 3600:        # crossed midnight
                        day += 1
                        s += 86400
                    prev = s
                    vals.append(s)
                else:
                    vals.append(None)
            vmax = r.get("vmax", "")
            db.execute("INSERT INTO stops VALUES(?,?,?,?,?,?,?,?,?)",
                       (t["no"], r["no"], code, int(vmax) if vmax.isdigit() else None, kind,
                        vals[0], vals[1], r.get("note"), r["page"]))
            for sym, other, own in parse_notes(r.get("note", "")):
                db.execute("INSERT INTO notes VALUES(?,?,?,?,?,?)", (t["no"], r["no"], code, sym, other, int(own)))
        db.execute("INSERT INTO trains VALUES(?,?,?,?,?)", (t["no"], t["service"], t["route"], t["page"], len(t["rows"])))
    return problems


# ---------------------------------------------------------------- 2. station distances
def norm(s):
    return re.sub(r"[^a-z0-9]", "", s.lower())


KM_RE = re.compile(r"^\d{1,3}\.\d{3}$")


def read_distances(db, pages):
    """Adjacent-station distances from the graph profile column 'Jarak antar stasiun':
    the number printed between two station-name rows. Stations are matched to the timetable
    station names (normalised). Result: station_links(a, b, km, page)."""
    names = {}
    for code, name in db.execute("select code,name from stations"):
        names.setdefault(norm(name), code)
    db.executescript("DROP TABLE IF EXISTS station_links; CREATE TABLE station_links(a TEXT, b TEXT, km REAL, page TEXT);")
    n = 0
    for fi, pno, page, kind in pages:
        if kind != "graph":
            continue
        words = [w for w in page.get_text("words") if w[0] < 460 and w[1] > 60]
        # text rows
        rows = defaultdict(list)
        for w in words:
            rows[round((w[1] + w[3]) / 2 / 2.5)].append(w)
        found = []      # (y, x, code)
        rl = sorted(rows.items())
        for i, (k, ws) in enumerate(rl):
            ws.sort(key=lambda w: w[0])
            # single words and runs of words on this row, and joined with the next row (two-line names)
            cands = []
            for a in range(len(ws)):
                acc = ""
                for b in range(a, min(a + 4, len(ws))):
                    acc += norm(ws[b][4])
                    cands.append((acc, ws[a][0], (ws[a][1] + ws[a][3]) / 2))
            if i + 1 < len(rl) and rl[i + 1][0] - k <= 6:
                nxt = sorted(rl[i + 1][1], key=lambda w: w[0])
                a0 = "".join(norm(w[4]) for w in ws if abs(w[0] - ws[0][0]) < 80)
                cands.append((a0 + "".join(norm(w[4]) for w in nxt[:3]), ws[0][0], (ws[0][1] + ws[0][3]) / 2))
            for acc, x, y in cands:
                if len(acc) >= 3 and acc in names:
                    found.append((y, x, names[acc]))
        # keep the longest/first match per (row,x)
        uniq = {}
        for y, x, c in found:
            uniq[(round(y / 3), c)] = (y, x, c)
        found = sorted(uniq.values())
        nums = [(w[0], (w[1] + w[3]) / 2, float(w[4])) for w in words if KM_RE.match(w[4])]
        for (y1, x1, c1), (y2, x2, c2) in zip(found, found[1:]):
            if c1 == c2 or y2 - y1 < 8 or y2 - y1 > 160:
                continue
            xr = max(x1, x2)
            mid = [v for v in nums if y1 + 3 < v[1] < y2 - 3 and xr - 130 < v[0] < xr - 25]
            if not mid:
                continue
            # the distance column is the one nearer to the name column (km column is aligned with the name row, not between)
            v = min(mid, key=lambda v: abs(v[1] - (y1 + y2) / 2))
            db.execute("INSERT INTO station_links VALUES(?,?,?,?)", (c1, c2, v[2], f"{fi}:{pno}"))
            n += 1
    return n


def read_distances_aligned(db, pages):
    """Distances when station names on the graph are not text (drawn as outlines).
    1. every drawn train line is matched to a timetable train by its crossing times (>= 4 station rows
       within 45 s, rows and stops in the same order) -> row index <-> station code
    2. the number printed between two station rows in the profile column is that interval's distance."""
    import bisect
    pg = {f"{fi}:{pno}": page for fi, pno, page, kind in pages if kind == "graph"}
    # timetable index: time bucket -> [(train, seq, station, t)]
    idx = defaultdict(list)
    seqs, tstops = {}, {}
    for no, in db.execute("select no from trains"):
        rows = db.execute("select seq,station,kind,arr_s,dep_s from stops where train_no=? order by seq", (no,)).fetchall()
        seqs[no] = [r[1] for r in rows]
        tstops[no] = [(seq, [x % 86400 for x in (a, d) if x is not None]) for seq, st, kind, a, d in rows if a is not None or d is not None]
        for seq, st, kind, a, d in rows:
            for t in {x for x in (a, d) if x is not None}:
                idx[int(t % 86400) // 60].append((no, seq, st, t % 86400))
    # a link is only believed if the two stations are neighbours in some timetable train's full stop list
    # (the timetable lists every station on the way, including Berh and Ls)
    adjacent = set()
    for lst in seqs.values():
        for a, b in zip(lst, lst[1:]):
            adjacent.add((a, b))
            adjacent.add((b, a))
    between = {}              # (A, B, number of stations between) -> the stations between them, if unambiguous
    clash = set()
    for lst in seqs.values():
        for i in range(len(lst)):
            for j in range(i + 2, min(i + 6, len(lst))):
                for key, val in (((lst[i], lst[j], j - i - 1), lst[i + 1:j]), ((lst[j], lst[i], j - i - 1), lst[i + 1:j][::-1])):
                    if key in between and between[key] != val:
                        clash.add(key)
                    between[key] = val
    for key in clash:
        between.pop(key, None)
    n_links = 0
    for page_id, panel, ytop, ybot in db.execute("select page,panel,y_top,y_bot from graph_pages").fetchall():
        if page_id not in pg:
            continue
        lines = [y for (y,) in db.execute("select y from graph_lines where page=? and panel=? order by idx", (page_id, panel))]
        if len(lines) < 3:
            continue
        votes = defaultdict(lambda: defaultdict(int))        # row -> code -> votes
        order_fill = []                                       # (row_a, row_b, [codes between incl.])
        for (tid,) in db.execute("select id from graph_trains where page=? and panel=?", (page_id, panel)).fetchall():
            cr = db.execute("select line_idx,t_s from graph_crossings where train_id=? order by line_idx", (tid,)).fetchall()
            if len(cr) < 4:
                continue
            cand = defaultdict(list)                          # train -> [(row, seq, st)]
            for r, t in cr:
                tt = t % 86400
                for b in (int(tt) // 60 - 1, int(tt) // 60, int(tt) // 60 + 1):
                    for no, seq, st, ts in idx.get(b, ()):
                        if abs(ts - tt) <= 45 or abs(abs(ts - tt) - 86400) <= 45:
                            cand[no].append((r, seq, st))
            ctimes = [t % 86400 for _, t in cr]
            tlo, thi = min(ctimes) - 60, max(ctimes) + 60
            for no, hits in cand.items():
                hits = sorted(set(hits))
                if len({h[0] for h in hits}) < 4:
                    continue
                # precision: every timed stop of that train inside the drawn span must also be drawn
                inspan = [ts for sq, ts in tstops[no] if any(tlo <= x <= thi for x in ts)]
                good = sum(1 for ts in inspan if any(abs(x - c) <= 45 for x in ts for c in ctimes))
                if not inspan or good / len(inspan) < 0.95:
                    continue
                # longest chain where rows and seq both increase (or rows increase while seq decreases)
                best = None
                for sign in (1, -1):
                    chain = []
                    for r, sq, st in sorted(hits, key=lambda h: (h[0], sign * h[1])):
                        if not chain or (r > chain[-1][0] and sign * sq > sign * chain[-1][1]):
                            chain.append((r, sq, st))
                    if best is None or len(chain) > len(best[0]):
                        best = (chain, sign)
                chain, sign = best
                if len(chain) < 4:
                    continue
                for (r1, s1, c1), (r2, s2, c2) in zip(chain, chain[1:]):
                    votes[r1][c1] += 1
                    votes[r2][c2] += 1
                    if abs(s2 - s1) == r2 - r1:               # same number of rows and stops: fill the stations between
                        step = 1 if s2 > s1 else -1
                        for k in range(r2 - r1 + 1):
                            votes[r1 + k][seqs[no][s1 - 1 + step * k]] += 1
        code = {r: max(v.items(), key=lambda kv: kv[1])[0] for r, v in votes.items()
                if max(v.values()) >= 2 and sum(v.values()) <= 50 * max(v.values())}
        # the same station on two rows close together is a mistake: keep the row with more votes
        for r in sorted(code):
            for r2 in range(r + 1, r + 4):
                if r in code and r2 in code and code[r] == code[r2]:
                    drop = r if sum(votes[r].values()) < sum(votes[r2].values()) else r2
                    code.pop(drop, None)
        # rows still empty between two known rows: take the stations that every timetable train lists between them
        for r1 in sorted(code):
            nxt = [r for r in code if r > r1]
            if not nxt:
                continue
            r2 = min(nxt)
            gap = r2 - r1 - 1
            if 1 <= gap <= 4:
                mid = between.get((code[r1], code[r2], gap))
                if mid and len(mid) == gap:
                    for k, st in enumerate(mid):
                        code.setdefault(r1 + 1 + k, st)
        if os.environ.get("GAPEKA_DEBUG"):
            print("DEBUG rows->stations", page_id, panel, sorted(code.items()))
        if len(code) < 3:
            continue
        # distance column: the column of KM-like numbers that lies between station lines, not on them
        page = pg[page_id]
        nums = [(w[0], (w[1] + w[3]) / 2, float(w[4])) for w in page.get_text("words")
                if KM_RE.match(w[4]) and ytop - 3 <= (w[1] + w[3]) / 2 <= ybot + 3 and w[0] < 460]
        cols = defaultdict(list)
        for x, y, v in nums:
            cols[round(x / 4)].append((y, v))
        best_col, best_score = None, 0
        for k, vals in cols.items():
            if len(vals) < 3:
                continue
            on = sum(1 for y, v in vals if min(abs(y - ly) for ly in lines) < 3.5)
            score = len(vals) * (1 - on / len(vals)) ** 2
            if score > best_score:
                best_col, best_score = k, score
        if best_col is None:
            continue
        dvals = cols[best_col] + cols.get(best_col - 1, []) + cols.get(best_col + 1, [])
        for k in range(len(lines) - 1):
            if k not in code or k + 1 not in code or code[k] == code[k + 1]:
                continue
            mid = [(abs(y - (lines[k] + lines[k + 1]) / 2), v) for y, v in dvals if lines[k] + 1 < y < lines[k + 1] - 1]
            if len(mid) == 1 and (code[k], code[k + 1]) in adjacent:
                db.execute("INSERT INTO station_links VALUES(?,?,?,?)", (code[k], code[k + 1], mid[0][1], f"aligned:{page_id}:{panel}"))
                n_links += 1
    return n_links


def observed_runs(db):
    """(a, b) -> [(seconds, vmax)] for adjacent stations that a train times at both ends with nothing between."""
    obs = defaultdict(list)
    for no, in db.execute("select no from trains"):
        last, mids = None, False
        for st, kind, a, d, v in db.execute("select station,kind,arr_s,dep_s,vmax from stops where train_no=? order by seq", (no,)):
            if kind in ("halt", "pass") and not (kind == "pass" and d is not None):
                mids = True
                continue
            if kind == "pass":
                a = d
            if last is not None and not mids and a is not None and a > last[1]:
                obs[(last[0], st)].append((a - last[1], last[2]))
            last, mids = ((st, d, v) if d is not None else None), False
    return obs


def read_km_columns(db, pages):
    """Cumulative-km column printed beside the station rows (0.000 ... 54.810). Station names may be outlines,
    so the run of km values is matched to the station sequence of the timetable: every window of that length in
    any train's stop list is a candidate; the one whose neighbour distances agree with the distances already
    known wins. Consecutive differences become station links."""
    seqs = [[r[0] for r in db.execute("select station from stops where train_no=? order by seq", (no,))]
            for no, in db.execute("select no from trains")]
    known = defaultdict(list)
    for a, b, km in db.execute("select a,b,km from station_links where page not like 'aligned%'"):
        known[(a, b)].append(km)
        known[(b, a)].append(km)
    soft = defaultdict(list)      # train-line alignment: may agree (evidence for) but never counts against a candidate
    for a, b, km in db.execute("select a,b,km from station_links where page like 'aligned%'"):
        soft[(a, b)].append(km)
        soft[(b, a)].append(km)
    by_len = defaultdict(lambda: defaultdict(int))
    for lst in seqs:
        for l in (lst, lst[::-1]):
            for n in range(4, min(len(l), 60) + 1):
                for i in range(len(l) - n + 1):
                    by_len[n][tuple(l[i:i + n])] += 1
    db.execute("drop table if exists km_unmatched")
    db.execute("create table km_unmatched(page TEXT, x REAL, n_rows INT, y_first INT, km_first REAL, y_last INT, km_last REAL)")
    sname = {c: norm(n) for c, n in db.execute("select code,name from stations")}
    n_links = 0
    for fi_pno, page, kind in [(f"{fi}:{pno}", page, kind) for fi, pno, page, kind in pages]:
        if kind != "graph":
            continue
        pw = sorted(((w[1], w[0], w[4]) for w in page.get_text("words") if 250 < w[0] < 470), key=lambda q: q[1])
        rt_cache = {}

        def rowtext(y):
            if y not in rt_cache:
                rt_cache[y] = norm("".join(t for yy, x, t in pw if abs(yy - y) <= 7))
            return rt_cache[y]

        def score(cand, km, ys):
            ok = bad = hit = 0
            for a, b, k1, k2 in zip(cand, cand[1:], km, km[1:]):
                kn = known.get((a, b))
                if kn:
                    if any(abs(kk - (k2 - k1)) <= 0.03 * kk + 0.01 for kk in kn):
                        ok += 1
                    else:
                        bad += 1
            for c, y in zip(cand, ys):
                nm = sname.get(c)
                if nm and len(nm) >= 4 and nm in rowtext(y):
                    hit += 1
            return ok + hit - 3 * bad
        cols = defaultdict(list)
        for w in page.get_text("words"):
            if KM_RE.match(w[4]) and w[0] < 470:
                cols[round(w[2] / 6)].append((w[1], float(w[4])))
        for key, vals in cols.items():
            vals.sort()
            runs, cur = [], []
            for y, v in vals:
                if cur and v <= cur[-1][1]:
                    runs.append(cur)
                    cur = []
                cur.append((y, v))
            runs.append(cur)
            for run in runs:
                if len(run) < 5:
                    continue
                covered = [False] * len(run)
                accepted = []
                for m in range(len(run), 4, -1):
                    for i in range(len(run) - m + 1):
                        if any(covered[i:i + m]):
                            continue
                        seg = run[i:i + m]
                        ys = [y for y, v in seg]
                        if min(b - a for a, b in zip(ys, ys[1:])) < 4:
                            continue
                        km = [v for y, v in seg]
                        best = [(score(cand, km, ys), cand) for cand in by_len.get(m, ())]
                        if not best:
                            continue
                        top = max(sc for sc, c in best)
                        ties = [c for sc, c in best if sc == top]
                        if not (top >= 2 and len(ties) == 1):
                            continue
                        for j in range(i, i + m):
                            covered[j] = True
                        cand = ties[0]
                        accepted.append((i, m, cand))
                        if os.environ.get("GAPEKA_DEBUG"):
                            print("kmwin", fi_pno, i, m, cand[0], cand[-1], top)
                        for a, b, k1, k2 in zip(cand, cand[1:], km, km[1:]):
                            if a != b and k2 > k1:
                                db.execute("insert into station_links values(?,?,?,?)", (a, b, round(k2 - k1, 3), f"kmcol:{fi_pno}"))
                                n_links += 1
                # a graph row for a station the timetable never lists (closed stop): drop one interior row
                for m in range(min(len(run), 12), 5, -1):
                    for i in range(len(run) - m + 1):
                        if any(covered[i:i + m]):
                            continue
                        for d in range(1, m - 1):
                            seg = run[i:i + m]
                            seg = seg[:d] + seg[d + 1:]
                            ys = [y for y, v in seg]
                            if min(b - a for a, b in zip(ys, ys[1:])) < 4:
                                continue
                            km = [v for y, v in seg]
                            best = [(score(cand, km, ys), cand) for cand in by_len.get(m - 1, ())]
                            if not best:
                                continue
                            top = max(sc for sc, c in best)
                            ties = [c for sc, c in best if sc == top]
                            if top >= 2 and len(ties) == 1:
                                for j in range(i, i + m):
                                    covered[j] = True
                                cand = ties[0]
                                accepted.append((i, m, cand))
                                for a, b, k1, k2 in zip(cand, cand[1:], km, km[1:]):
                                    if a != b and k2 > k1:
                                        db.execute("insert into station_links values(?,?,?,?)", (a, b, round(k2 - k1, 3), f"kmcol:{fi_pno}"))
                                        n_links += 1
                                break
                if not any(covered) and len(run) >= 5:
                    db.execute("insert into km_unmatched values(?,?,?,?,?,?,?)",
                               (fi_pno, key * 6, len(run), round(run[0][0]), run[0][1], round(run[-1][0]), run[-1][1]))
                # rows left over right after an accepted window: continue from its last station, taking the
                # continuation that most trains run (a branch line with few trains does not win)
                for i, m, cand in list(accepted):
                    j = i + m
                    while j < len(run) and not covered[j]:
                        end = j
                        while end < len(run) and not covered[end]:
                            end += 1
                        seg = run[j - 1:end]
                        if len(seg) < 3:
                            break
                        L = len(seg)
                        pool = {c: n for c, n in by_len.get(L, {}).items() if c[0] == cand[-1] and c[1] != cand[-2]}
                        if not pool:
                            break
                        rank = sorted(pool.items(), key=lambda x: -x[1])
                        if len(rank) > 1 and rank[0][1] < 3 * rank[1][1]:
                            break
                        cont = rank[0][0]
                        km = [v for y, v in seg]
                        for a, b, k1, k2 in zip(cont, cont[1:], km, km[1:]):
                            if a != b and k2 > k1:
                                db.execute("insert into station_links values(?,?,?,?)", (a, b, round(k2 - k1, 3), f"kmcol:{fi_pno}"))
                                n_links += 1
                        for q in range(j, end):
                            covered[q] = True
                        break
    return n_links


def load_manual_links(db, path):
    """manual_links.csv: lines 'code,code,km' (# comments allowed). They replace every other value for that pair."""
    if not os.path.exists(path):
        return 0
    n = 0
    for line in open(path, encoding="utf-8"):
        line = line.split("#")[0].strip()
        if not line:
            continue
        try:
            a, b, km = [x.strip() for x in line.split(",")[:3]]
            km = float(km)
        except ValueError:
            print("    manual_links.csv: skipped line", line)
            continue
        db.execute("DELETE FROM station_links WHERE (a=? AND b=?) OR (a=? AND b=?)", (a, b, b, a))
        db.execute("INSERT INTO station_links VALUES(?,?,?,'manual')", (a, b, km))
        n += 1
    return n


def write_missing_links(db, path):
    """missing_links.csv: neighbouring station pairs (as listed in the timetable) where at least one station has no
    distance at all, most-used first. Fill km in and put the lines in manual_links.csv."""
    linked = {x for r in db.execute("select a,b from station_links") for x in r}
    names = dict(db.execute("select code,name from stations"))
    cnt = defaultdict(int)
    for no, in db.execute("select no from trains"):
        lst = [r[0] for r in db.execute("select station from stops where train_no=? order by seq", (no,))]
        for a, b in zip(lst, lst[1:]):
            if a != b and (a not in linked or b not in linked):
                cnt[tuple(sorted((a, b)))] += 1
    with open(path, "w", encoding="utf-8") as f:
        f.write("# a,b,km,(names, trains using this pair) - fill in km and move the line to manual_links.csv\n")
        for (a, b), n in sorted(cnt.items(), key=lambda x: -x[1]):
            f.write(f"{a},{b},,{names.get(a, a)} - {names.get(b, b)} ({n} trains)\n")
    return len(cnt)


def drop_implausible_links(db):
    """remove links that contradict the timetable: for a station pair that some train times at both ends
    with nothing between, the implied speed must be 8 km/h .. printed limit * 1.1; a pair that has several
    different distances (e.g. from different pages) is kept only if they agree within 15 %."""
    obs = defaultdict(list)
    for no, in db.execute("select no from trains"):
        last = None
        mids = False
        for st, kind, a, d, v in db.execute("select station,kind,arr_s,dep_s,vmax from stops where train_no=? order by seq", (no,)):
            if kind in ("halt", "pass") and not (kind == "pass" and d is not None):
                mids = True
                continue
            if kind == "pass":
                a = d
            if last is not None and not mids and a is not None and a > last[1]:
                obs[(last[0], st)].append((a - last[1], last[2]))
            last, mids = ((st, d, v) if d is not None else None), False
    # the cumulative-km column is more reliable than the train-line alignment: where both exist, keep the km column
    db.execute("""DELETE FROM station_links WHERE page LIKE 'aligned%' AND EXISTS (
                  SELECT 1 FROM station_links k WHERE k.page LIKE 'kmcol%'
                  AND ((k.a=station_links.a AND k.b=station_links.b) OR (k.a=station_links.b AND k.b=station_links.a)))""")
    rows = db.execute("select rowid,a,b,km,page from station_links").fetchall()
    bad = set()
    by_pair = defaultdict(list)
    for rid, a, b, km, page in rows:
        by_pair[frozenset((a, b))].append((rid, km, page))
        for T, v in obs.get((a, b), []) + obs.get((b, a), []):
            speed = km / T * 3600
            if speed > (v or 120) * 1.1 or speed < 8:
                bad.add(rid)
                break
    for pair, lst in by_pair.items():
        kms = sorted(k for _, k, _ in lst)
        if len(lst) > 1 and kms[-1] > kms[0] * 1.15:
            # disagreeing values: keep only those from name matching (more reliable) if they agree
            keep = [r for r in lst if not str(r[2]).startswith(("aligned", "kmcol"))]
            for rid, k, page in lst:
                if not keep or (str(page).startswith(("aligned", "kmcol"))):
                    bad.add(rid)
    for rid in bad:
        db.execute("DELETE FROM station_links WHERE rowid=?", (rid,))
    return len(bad)



def make_distance(db):
    adj = defaultdict(dict)
    for a, b, km in db.execute("select a,b,km from station_links"):
        for u, v in ((a, b), (b, a)):
            adj[u].setdefault(v, []).append(km)
    adj = {u: {v: float(np.median(k)) for v, k in d.items()} for u, d in adj.items()}
    cache = {}

    def dist(a, b):
        """shortest path distance along the adjacency graph (sums sub-legs); None if unknown"""
        if a == b:
            return 0.0
        if (a, b) in cache:
            return cache[(a, b)]
        best, pq = {a: 0.0}, [(0.0, a)]
        while pq:
            d, u = heapq.heappop(pq)
            if u == b:
                break
            if d > best.get(u, 1e9) or d > 150:
                continue
            for v, w in adj.get(u, {}).items():
                if d + w < best.get(v, 1e9):
                    best[v] = d + w
                    heapq.heappush(pq, (d + w, v))
        r = best.get(b)
        cache[(a, b)] = cache[(b, a)] = r
        return r
    return dist


# ---------------------------------------------------------------- 3. halt estimation
def robust_fit(d, t):
    """least squares a*km+b, trimming outliers beyond 2.5 x max(MAD, 20 s) and refitting"""
    A = np.column_stack([d, np.ones(len(d))])
    coef = np.linalg.lstsq(A, t, rcond=None)[0]
    keep = np.ones(len(d), bool)
    for _ in range(4):
        r = t - A @ coef
        mad = max(np.median(np.abs(r - np.median(r))) * 1.4826, 20.0)
        keep = np.abs(r) < 2.5 * mad
        if keep.sum() < 8:
            keep = np.ones(len(d), bool)
            break
        coef = np.linalg.lstsq(A[keep], t[keep], rcond=None)[0]
    r = t - A @ coef
    return float(coef[0]), float(coef[1]), float(np.sqrt(np.mean(r[keep] ** 2)))


def vclass(v):
    """speed class of a train from its highest printed speed limit"""
    return "vmax<=85" if v <= 85 else ("vmax<=105" if v <= 105 else "vmax>105")


def valid_fit(a, b):
    return 20 <= a <= 150 and 0 <= b <= 700


TFB_SERVICES = ("Commuter Line Bandung Raya", "Commuter Line Garut", "Siliwangi")


def apply_halt_offsets(db, path, dwell):
    """halt_offsets.csv: 'halt_code,from_code,minutes[,trains]' - for every train whose halt at halt_code follows the
    timed stop from_code, the halt time is set to that stop's departure + minutes (taken from a published timetable).
    The optional 4th column lists train numbers (space separated) that use this value instead of the general rule
    for the same halt_code,from_code. Skipped when it would not fit before the next timed stop."""
    if not os.path.exists(path):
        return 0
    rules = []                        # (halt, from, seconds, trains) ; trains = () for the general rule
    general, specific = {}, {}        # (halt, from) / (halt, from, train) -> index in rules
    for line in open(path, encoding="utf-8"):
        line = line.split("#")[0].strip()
        if not line:
            continue
        try:
            parts = [x.strip() for x in line.split(",")]
            h, f, m = parts[:3]
            trains = tuple(parts[3].split()) if len(parts) > 3 else ()
            rules.append((h, f, float(m) * 60, trains))
        except ValueError:
            print("    halt_offsets.csv: skipped line", line)
            continue
        for no in trains:
            specific[(h, f, no)] = len(rules) - 1
        if not trains:
            general[(h, f)] = len(rules) - 1
    n = 0
    diff = defaultdict(list)          # rule -> model minus manual (minutes): accuracy of the model
    skipped = defaultdict(list)       # rule -> trains where the manual time does not fit before the next stop
    used = defaultdict(set)           # rule -> trains it was applied to
    for no, seq, st, model_t in db.execute("select train_no,seq,station,arr_s from halt_est").fetchall():
        rows = db.execute("select seq,station,kind,arr_s,dep_s from stops where train_no=? order by seq", (no,)).fetchall()
        i = next(k for k, r in enumerate(rows) if r[0] == seq)
        prev = next((r for r in reversed(rows[:i]) if r[2] in ("stop", "origin_or_end") and (r[4] is not None or r[3] is not None)), None)
        nxt = next((r for r in rows[i + 1:] if r[2] in ("stop", "origin_or_end") and (r[3] is not None or r[4] is not None)), None)
        if prev is None:
            continue
        rule = specific.get((st, prev[1], no), general.get((st, prev[1])))
        if rule is None:
            continue
        t0 = prev[4] if prev[4] is not None else prev[3]
        t = t0 + rules[rule][2]
        if nxt is not None and t + dwell >= (nxt[3] if nxt[3] is not None else nxt[4]):
            skipped[rule].append(no)
            continue
        diff[rule].append((model_t - t) / 60)
        used[rule].add(no)
        db.execute("UPDATE halt_est SET arr_s=?, dep_s=?, method='manual' WHERE train_no=? AND seq=?", (round(t), round(t + dwell), no, seq))
        n += 1
    print("    halt_offsets.csv: model minus manual time per rule (minutes)")
    for rule, (h, f, sec, trains) in enumerate(rules):
        lab = f"{h} after {f} +{sec / 60:g}" + (f" (trains {' '.join(trains)})" if trains else "")
        if rule in diff:
            v = diff[rule]
            print(f"      {lab}: n={len(v)}  median {np.median(v):+.1f}  max |diff| {max(abs(x) for x in v):.1f}")
        if rule in skipped:
            print(f"      {lab}: SKIPPED (does not fit before the next stop) for trains {', '.join(skipped[rule])}")
        if rule not in diff and rule not in skipped:
            print(f"      {lab}: UNUSED - no train halts at {h} right after a timed stop at {f} (check the codes)")
        elif trains and set(trains) - used[rule] - set(skipped[rule]):
            print(f"      {lab}: trains never matched: {' '.join(sorted(set(trains) - used[rule]))}")
    if diff:
        allv = [abs(x) for v in diff.values() for x in v]
        print(f"      overall: {len(allv)} halts, mean |diff| {np.mean(allv):.2f} min, max {max(allv):.1f} min")
    return n


def estimate_halts(db, dist, dwell, min_clean=20, min_distinct=6):
    """Estimate Berh/Sb times. Weight of each sub-leg, best source first:
      observed : median run time of that same station pair, taken from trains that time both stations
      fit/class/pooled : a*km + b, fitted per service, else per speed class, else over everything
      equal    : only if no distance is known
    Legs whose distance contradicts the timetable (faster than the printed speed limit, or slower
    than 10 km/h on average) are treated as 'distance unknown': a missing link on the graph page
    makes the shortest path take a detour, so such distances cannot be trusted."""
    svc = dict(db.execute("select no,service from trains"))
    tvmax = dict(db.execute("select train_no,max(vmax) from stops group by train_no"))
    segs = []      # (train, group, class, A=(seq,st,dep,vmax), mids[(seq,st,kind)], T, B)
    for no in svc:
        last, mids = None, []
        for seq, st, kind, a, d, v in db.execute("select seq,station,kind,arr_s,dep_s,vmax from stops where train_no=? order by seq", (no,)):
            if kind == "pass" and d is not None:       # pass-through with a printed time: a timed point
                a = d
            elif kind in ("halt", "pass"):
                mids.append((seq, st, kind))
                continue
            if last is not None and a is not None:
                segs.append((no, svc[no], vclass(tvmax.get(no) or 70), last, mids, a - last[2], st))
            last, mids = ((seq, st, d, v) if d is not None else None), []

    def leg_km(A, mids, B):
        pts = [A] + [m[1] for m in mids] + [B]
        parts = [dist(pts[i], pts[i + 1]) for i in range(len(pts) - 1)]
        return parts if all(parts) else None

    def physical(parts, T, vmax):
        if not parts or T <= 0:
            return False
        d = sum(parts)
        v = d / T * 3600
        return v <= (vmax or 120) * 1.05 and v >= 10

    obs = defaultdict(list)       # (A,B) -> run times of directly adjacent timed stops
    clean = []                    # (group, class, km, T, pair)
    for no, g, cl, A, mids, T, B in segs:
        if not mids and 0 < T < 7200:
            obs[(A[1], B)].append(T)
        if not mids:
            parts = leg_km(A[1], [], B)
            if parts and parts[0] >= 1.5 and physical(parts, T, A[3]):
                clean.append((g, cl, parts[0], T, (A[1], B)))
    # technical (minimum) run time of a pair: the fastest trains carry little or no recovery time
    obs_min = {k: float(np.percentile(v, 10)) for k, v in obs.items()}
    obs = {k: float(np.median(v)) for k, v in obs.items()}

    def fit_group(rows, nmin=min_clean):
        if len(rows) < nmin or len({(round(r[2], 2)) for r in rows}) < min_distinct:
            return None
        a, b, r = robust_fit(np.array([x[2] for x in rows]), np.array([x[3] for x in rows], float))
        return (a, b, r, len(rows)) if valid_fit(a, b) else None

    def envelope(rows):
        """one row per station pair, with the shortest run time any train of the group needs for it"""
        best = {}
        for r in rows:
            if r[4] not in best or r[3] < best[r[4]][3]:
                best[r[4]] = r
        return list(best.values())

    db.executescript("""DROP TABLE IF EXISTS halt_fit; DROP TABLE IF EXISTS halt_est;
      CREATE TABLE halt_fit(grp TEXT, s_per_km REAL, base_s REAL, dwell_s REAL, n_clean INTEGER, rmse_s REAL);
      CREATE TABLE halt_est(train_no TEXT, seq INTEGER, station TEXT, arr_s INTEGER, dep_s INTEGER, method TEXT,
                            PRIMARY KEY(train_no, seq));""")
    svc_fit, cls_fit, pooled = {}, {}, None
    for g in {c[0] for c in clean}:
        f = fit_group([c for c in clean if c[0] == g])
        if f:
            svc_fit[g] = f
            db.execute("INSERT INTO halt_fit VALUES(?,?,?,?,?,?)", (g, f[0], f[1], dwell, f[3], f[2]))
    for cl in {c[1] for c in clean}:
        f = fit_group([c for c in clean if c[1] == cl])
        if f:
            cls_fit[cl] = f
            db.execute("INSERT INTO halt_fit VALUES(?,?,?,?,?,?)", ("class " + cl, f[0], f[1], dwell, f[3], f[2]))
    f = fit_group(clean)
    if f:
        pooled = f
        db.execute("INSERT INTO halt_fit VALUES(?,?,?,?,?,?)", ("(pooled)", f[0], f[1], dwell, f[3], f[2]))
    # lower-envelope fits (technical run time), same fallback order; fewer rows needed: one row per station pair
    min_svc, min_cls, min_pooled = {}, {}, None
    for idx, store, label in ((0, min_svc, "min {}"), (1, min_cls, "min class {}")):
        for k in {c[idx] for c in clean}:
            f = fit_group(envelope([c for c in clean if c[idx] == k]), nmin=8)
            if f:
                store[k] = f
                db.execute("INSERT INTO halt_fit VALUES(?,?,?,?,?,?)", (label.format(k), f[0], f[1], dwell, f[3], f[2]))
    f = fit_group(envelope(clean), nmin=8)
    if f:
        min_pooled = f
        db.execute("INSERT INTO halt_fit VALUES(?,?,?,?,?,?)", ("min (pooled)", f[0], f[1], dwell, f[3], f[2]))

    def to_minute(t, lo, hi):
        """round to the whole minute like a published timetable; stay inside (lo, hi)"""
        for r in (round(t / 60) * 60, (t // 60) * 60):
            if lo < r and r + dwell < hi:
                return r
        return t

    counts = defaultdict(int)
    for no, g, cl, A, mids, T, B in segs:
        hs = [m for m in mids if m[2] == "halt"]
        if not hs:
            continue
        pts = [A[1]] + [m[1] for m in mids] + [B]
        parts = leg_km(A[1], mids, B)
        if parts and not physical(parts, T, A[3]):
            parts = None
        # 0) technical run time per sub-leg, recovery time left at the end of the leg (before the next timed stop)
        tech, tsrc = [], set()
        for i in range(len(pts) - 1):
            o = obs_min.get((pts[i], pts[i + 1])) or obs_min.get((pts[i + 1], pts[i]))
            if o:
                tech.append(o)
                tsrc.add("observed")
                continue
            src = next(((s, tag) for s, tag in ((min_svc.get(g), "fit"), (min_cls.get(cl), "class"), (min_pooled, "pooled")) if s), None)
            if not parts or not src:
                tech = None
                break
            tech.append(src[0][0] * parts[i] + src[0][1])
            tsrc.add(src[1])
        if tech and sum(tech) + dwell * len(hs) <= T:
            method = ("fit" if "fit" in tsrc else "class" if "class" in tsrc else "pooled" if "pooled" in tsrc else "observed") + "-front"
            cum, nh, prev_t = 0.0, 0, A[2]
            for i, m in enumerate(mids):
                cum += tech[i]
                if m[2] == "halt":
                    t = to_minute(A[2] + cum + dwell * nh, prev_t, A[2] + T)
                    db.execute("INSERT OR REPLACE INTO halt_est VALUES(?,?,?,?,?,?)", (no, m[0], m[1], round(t), round(t + dwell), method))
                    prev_t = t + dwell
                    nh += 1
                    counts[method] += 1
            continue
        w, method = None, None
        # 1) observed run times of the same station pairs
        o = [obs.get((pts[i], pts[i + 1])) or obs.get((pts[i + 1], pts[i])) for i in range(len(pts) - 1)]
        if all(o):
            w, method = o, "observed"
        # 2) distance model
        elif parts:
            for src, tag in ((svc_fit.get(g), "fit"), (cls_fit.get(cl), "class"), (pooled, "pooled")):
                if src:
                    w, method = [src[0] * x + src[1] for x in parts], tag
                    break
        if w is None:
            w, method = [1.0] * len(pts[1:]), "equal"
            w = [1.0] * (len(pts) - 1)
        avail = max(T - dwell * len(hs), 0)
        tot, cum, nh, prev_t = sum(w), 0.0, 0, A[2]
        for i, m in enumerate(mids):
            cum += w[i]
            if m[2] == "halt":
                t = to_minute(A[2] + avail * cum / tot + dwell * nh, prev_t, A[2] + T)
                prev_t = t + dwell
                db.execute("INSERT OR REPLACE INTO halt_est VALUES(?,?,?,?,?,?)", (no, m[0], m[1], round(t), round(t + dwell), method))
                nh += 1
                counts[method] += 1
    db.executescript("""DROP VIEW IF EXISTS schedule;
      CREATE VIEW schedule AS
      SELECT s.train_no, s.seq, s.station, s.vmax, s.kind,
             COALESCE(s.arr_s, h.arr_s) AS arr_s, COALESCE(s.dep_s, h.dep_s) AS dep_s,
             CASE WHEN h.train_no IS NOT NULL THEN 1 ELSE 0 END AS estimated, h.method, s.note
      FROM stops s LEFT JOIN halt_est h ON h.train_no=s.train_no AND h.seq=s.seq;""")
    return dict(counts), len(clean)


# ---------------------------------------------------------------- 4. note linking
def link_notes(db):
    db.executescript("""DROP TABLE IF EXISTS constraints;
    CREATE TABLE constraints(id INTEGER PRIMARY KEY, kind TEXT, station TEXT, train_a TEXT, seq_a INT, a_arr INT, a_dep INT,
      train_b TEXT, b_arr INT, b_dep INT, b_in_extract INT, own_stated INT, reciprocal INT, waiter TEXT, overtaker TEXT);""")
    trains = {r[0] for r in db.execute("select no from trains")}
    notes = db.execute("select train_no,seq,station,symbol,other_train,own from notes").fetchall()
    st = lambda t, s: db.execute("select seq,arr_s,dep_s from stops where train_no=? and station=?", (t, s)).fetchone()
    base = lambda n: (re.match(r"\d+", n) or [n])[0]
    dw = lambda r: (r[2] - r[1]) if r and r[1] is not None and r[2] is not None else None
    seen = set()
    for t, seq, s, sym, o, own in notes:
        ob = o if o in trains else (base(o) if base(o) in trains else None)
        A, B = st(t, s), (st(ob, s) if ob else None)
        recip = int(any(x[0] == ob and x[2] == s and x[4] == t for x in notes)) if ob else 0
        key = (sym, s, tuple(sorted([t, o])))
        if sym == "x" and key in seen and recip:
            continue
        seen.add(key)
        waiter = overt = None
        if sym == "x" and B and dw(A) is not None and dw(B) is not None and dw(A) != dw(B):
            waiter = t if dw(A) > dw(B) else ob
        elif sym == "=":
            waiter = t           # owner dwells long = waits to be overtaken
        elif sym == "//":
            overt = t            # owner passes
        db.execute("INSERT INTO constraints(kind,station,train_a,seq_a,a_arr,a_dep,train_b,b_arr,b_dep,b_in_extract,own_stated,reciprocal,waiter,overtaker) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                   ({"x": "cross", "=": "overtaken_wait", "//": "overtake_pass"}.get(sym, "other"), s, t, A[0] if A else seq,
                    A[1] if A else None, A[2] if A else None, o, B[1] if B else None, B[2] if B else None,
                    int(bool(B)), own, recip, waiter, overt))
    return db.execute("select kind,count(*),sum(b_in_extract) from constraints group by 1").fetchall()


# ---------------------------------------------------------------- 5. graph train lines
def hour_axes(page, minsize):
    rows = defaultdict(list)
    for b in page.get_text("dict")["blocks"]:
        for l in b.get("lines", []):
            for s in l["spans"]:
                t = s["text"].strip()
                if t.isdigit() and s["size"] > minsize and int(t) <= 24:
                    rows[round(s["origin"][1] / 20)].append((int(t), (s["bbox"][0] + s["bbox"][2]) / 2, s["bbox"][1]))
    fits = []
    for v in rows.values():
        if len(v) < 3:
            continue
        v.sort(key=lambda t: t[1])
        h = [a for a, _, _ in v]
        x = [b for _, b, _ in v]
        mh, mx = sum(h) / len(h), sum(x) / len(x)
        den = sum((a - mh) ** 2 for a in h)
        if den == 0:
            continue
        kx = sum((a - mh) * (b - mx) for a, b in zip(h, x)) / den
        x0 = mx - kx * mh
        if max(abs(x0 + kx * a - b) for a, b in zip(h, x)) > 8 or not (100 < kx < 400):
            continue
        fits.append({"y": v[0][2], "pt_per_hour": kx, "x_at_0h": x0, "hours": h})
    return fits


def cross_x(d, y):
    xs = []
    for it in d["items"]:
        if it[0] == "l":
            a, b = it[1], it[2]
            if a.y != b.y and (a.y - y) * (b.y - y) <= 0:
                xs.append(a.x + (y - a.y) / (b.y - a.y) * (b.x - a.x))
    return xs


def extract_graph(db, pages):
    db.executescript("""DROP TABLE IF EXISTS graph_lines; DROP TABLE IF EXISTS graph_pages; DROP TABLE IF EXISTS graph_trains; DROP TABLE IF EXISTS graph_crossings;
    CREATE TABLE graph_pages(page TEXT, panel INT, pt_per_hour REAL, x_at_0h REAL, y_top REAL, y_bot REAL, n_lines INT);
    CREATE TABLE graph_trains(id INTEGER PRIMARY KEY, page TEXT, panel INT, style TEXT, freight INT, n_cross INT);
    CREATE TABLE graph_crossings(train_id INT, line_y REAL, line_idx INT, t_s REAL);
    CREATE TABLE graph_lines(page TEXT, panel INT, idx INT, y REAL);""")
    tid, ok, fail = 0, 0, []
    for fi, pno, page, kind in pages:
        if kind != "graph":
            continue
        axes = hour_axes(page, 16.5) or hour_axes(page, 9.5)
        if not axes:
            fail.append(f"{fi}:{pno}")
            continue
        ys = sorted({round(it[1].y, 1) for d in page.get_drawings() for it in d["items"]
                     if it[0] == "l" and abs(it[1].y - it[2].y) < .3 and abs(it[1].x - it[2].x) > 700})
        if not ys:
            fail.append(f"{fi}:{pno}")
            continue
        gaps = sorted(b - a for a, b in zip(ys, ys[1:]))
        thr = max(120, gaps[len(gaps) // 2] * 4) if gaps else 120
        panels, cur = [], [ys[0]]
        for a, b in zip(ys, ys[1:]):
            if b - a > thr:
                panels.append(cur)
                cur = []
            cur.append(b)
        panels.append(cur)
        drs = [d for d in page.get_drawings() if len(d["items"]) >= 4 and d["type"] in ("f", "s", "fs")]
        for pi, ln in enumerate(panels):
            if len(ln) < 4:
                continue
            ax = min(axes, key=lambda f: min(abs(f["y"] - ln[0]), abs(f["y"] - ln[-1])))
            db.execute("INSERT INTO graph_pages VALUES(?,?,?,?,?,?,?)", (f"{fi}:{pno}", pi, ax["pt_per_hour"], ax["x_at_0h"], ln[0], ln[-1], len(ln)))
            db.executemany("INSERT INTO graph_lines VALUES(?,?,?,?)", [(f"{fi}:{pno}", pi, i, y) for i, y in enumerate(ln)])
            for d in drs:
                r = d["rect"]
                if r.y0 < ln[0] - 2 or r.y1 > ln[-1] + 2 or r.width < 20:
                    continue
                cr = []
                for i, y in enumerate(ln):
                    xs = cross_x(d, y)
                    if xs:
                        tm = ((min(xs) + max(xs)) / 2 - ax["x_at_0h"]) / ax["pt_per_hour"]
                        if ax["hours"][0] - .05 <= tm <= ax["hours"][-1] + .05:
                            cr.append((i, y, tm * 3600))
                if len(cr) < 3:
                    continue
                dashed = bool(d.get("dashes") and d["dashes"] not in ("[] 0", "[] 0.0"))
                tid += 1
                db.execute("INSERT INTO graph_trains VALUES(?,?,?,?,?,?)", (tid, f"{fi}:{pno}", pi,
                           "dashed" if dashed else ("filled" if d["type"] == "f" else "stroke"), int(dashed), len(cr)))
                db.executemany("INSERT INTO graph_crossings VALUES(?,?,?,?)", [(tid, y, i, t) for i, y, t in cr])
        ok += 1
    return tid, ok, fail


# ---------------------------------------------------------------- main
def main(argv):
    dwell, graph, files = 30, True, []
    manual = os.path.join(os.path.dirname(os.path.abspath(__file__)), "manual_links.csv")
    it = iter(argv[1:])
    for a in it:
        if a == "--dwell":
            dwell = float(next(it))
        elif a == "--no-graph":
            graph = False
        elif a == "--links":
            manual = next(it)
        else:
            files.append(a)
    if len(files) < 2:
        sys.exit(__doc__)
    out, pdfs = files[0], []
    for a in files[1:]:                      # a folder is expanded to its PDFs, sorted by name
        if os.path.isdir(a):
            pdfs += sorted(glob.glob(os.path.join(a, "*.pdf")))
        else:
            pdfs.append(a)
    if not pdfs:
        sys.exit("no PDF files found")
    pages = classify(pdfs)
    print("pages:", {k: sum(1 for p in pages if p[3] == k) for k in ("timetable", "graph", "other")})
    db = sqlite3.connect(out)
    trains, orphans = parse_timetables(pages)
    problems = build_tables(db, trains)
    nh = db.execute("select count(*) from stops where kind='halt'").fetchone()[0]
    ns = db.execute("select count(*) from stops").fetchone()[0]
    print(f"[1] trains {len(trains)}, stops {ns}, halts {nh}, problems {len(problems)}, orphan rows skipped {orphans}")
    for p in problems[:10]:
        print("    ", p)
    n_graph = None
    if graph:
        n_graph = extract_graph(db, pages)
    n = read_distances(db, pages)
    n2 = read_distances_aligned(db, pages) if graph else 0
    n3 = read_km_columns(db, pages) if graph else 0
    n_bad = drop_implausible_links(db)
    n_man = load_manual_links(db, manual)
    n_pairs = write_missing_links(db, os.path.splitext(out)[0] + "_missing_links.csv")
    n_un = db.execute("select count(*) from km_unmatched").fetchone()[0]
    print(f"[2] station links {n} from names + {n2} from matching drawn train lines to the timetable + {n3} from cumulative-km columns; {n_bad} dropped as implausible; {n_man} from manual_links.csv; {n_un} km columns left unmatched (table km_unmatched); {n_pairs} neighbour pairs still need a distance, listed in {os.path.splitext(os.path.basename(out))[0]}_missing_links.csv")
    linked = {x for r in db.execute("select a,b from station_links") for x in r}
    miss = [(c, nm, k) for c, nm, k in db.execute(
        "select s.station, st.name, count(*) from stops s join stations st on st.code=s.station group by s.station order by 3 desc")
        if c not in linked]
    print(f"    stations with no distance on any graph page: {len(miss)} of "
          f"{db.execute('select count(*) from stations').fetchone()[0]}; most used: {[(c, nm) for c, nm, k in miss[:12]]}")
    counts, nclean = estimate_halts(db, make_distance(db), dwell)
    apply_halt_offsets(db, os.path.join(os.path.dirname(os.path.abspath(__file__)), "halt_offsets.csv"), dwell)
    counts = dict(db.execute("select method,count(*) from halt_est group by 1"))
    print(f"[3] halts estimated {counts} (clean fit legs: {nclean}, dwell {dwell:.0f}s)")
    left = db.execute(f"""select h.station, st.name, count(*) from halt_est h join trains t on t.no=h.train_no
                          join stations st on st.code=h.station
                          where h.method!='manual' and t.service in ({','.join('?' * len(TFB_SERVICES))})
                          group by 1 order by 3 desc""", TFB_SERVICES).fetchall()
    if left:
        print(f"    TfB halts still estimated by the model (add a rule to halt_offsets.csv): {left}")
    for r in db.execute("select grp,round(s_per_km,1),round(base_s),n_clean,round(rmse_s) from halt_fit"):
        print("     fit", r)
    print("[4] constraints", link_notes(db))
    if graph:
        n, ok, fail = n_graph
        print(f"[5] graph trains {n} from {ok} pages; no hour axis on {fail}")
    db.commit()
    db.execute("VACUUM")
    print("wrote", out)


if __name__ == "__main__":
    main(sys.argv)