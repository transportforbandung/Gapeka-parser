"""Reference planner, phase 3 (docs/simulator_package.md): inserts ONE train into the fixed timetable of the package
and plans its waits by the GAPEKA rules. Scheduled trains are never moved (default, like an extra train / KLB that is
held so regular trains stay on time). Uses the package only (and physics_ref / replay for running times).

Timeline: every scheduled train's time at every station row; passes without a printed time are filled in proportion
to the distance between the neighbouring timed points (profiles km).
"""
import bisect
from collections import defaultdict

import physics_ref


def _row_time(r):
    return r[2] if r[2] is not None else r[3]


class Timeline:
    """per train: list of [station, kind, arr, dep] with every time filled; per block (consecutive non-halt stations
    of a train, directed) the trains' entry / exit times"""

    def __init__(self, pkg, skip=()):
        self.pkg = pkg
        self.kind = {c: s["kind"] for c, s in pkg["stations"].items()}
        self.prof = physics_ref.Profiles(pkg["profiles"])
        self.trains = {}
        self._signals_init()
        for t in pkg["trains"]:
            if t["no"] in skip:
                continue
            tl = self.fill(t)
            if tl:
                self.trains[t["no"]] = tl
        self.index()

    def set_rows(self, no, rows):
        """replace a train's rows by planned rows ([station, kind, arr, dep, ...]); call index() afterwards"""
        out = []
        for r in rows:
            a, d = r[2], r[3]
            out.append([r[0], r[1], a if a is not None else d, d if d is not None else a])
        self.trains[no] = [r for r in out if r[2] is not None]

    def _signals_init(self):
        """block signals per directed sheet section: (x, y) -> km positions from x on the track used x -> y (hulu =
        sheet order a -> b, hilir = b -> a); sheet graph for routes between timetable stations"""
        import heapq
        self._heapq = heapq
        self.sig = defaultdict(list)
        self.sheet = defaultdict(dict)
        for sec in self.pkg["sections"]:
            if sec.get("variant"):
                continue
            a, b, L = sec["a"], sec["b"], sec["km"]
            self.sheet[a][b] = self.sheet[b][a] = L
            for off, track, *_ in sec.get("block_signals", []):
                if off is None:
                    continue
                tr = (track or "").split()
                if not tr or "hu" in tr:
                    self.sig[(a, b)].append(off)
                if not tr or "hi" in tr:
                    self.sig[(b, a)].append(L - off)
        self._bp = {}

    def sheet_route(self, a, b, limit):
        dist, prev, pq = {a: 0.0}, {}, [(0.0, a)]
        while pq:
            d, x = self._heapq.heappop(pq)
            if x == b:
                out = [b]
                while out[-1] != a:
                    out.append(prev[out[-1]])
                return out[::-1]
            if d > dist.get(x, 1e9) or d > limit:
                continue
            for y, w in self.sheet[x].items():
                if d + w < dist.get(y, 1e9):
                    dist[y], prev[y] = d + w, x
                    self._heapq.heappush(pq, (d + w, y))
        return None

    def block_points(self, rows, i, j, ctx=None):
        """block points of a train's block rows[i] -> rows[j] (two consecutive non-halt stations) as fractions of the
        distance from rows[i]: 0, every block signal on the way (on the track of this direction), 1. Halts are not
        block points (user)."""
        key = tuple(r[0] for r in rows[i:j + 1])
        if key in self._bp:
            return self._bp[key]
        ctx = ctx or set(key)
        cum, pos = 0.0, []
        for k in range(i, j):
            x, y = rows[k][0], rows[k + 1][0]
            L = self.km(x, y, ctx) or 0.0
            route = [x, y] if y in self.sheet.get(x, {}) else self.sheet_route(x, y, max(3 * L, 5))
            if route and len(route) > 1:
                rl = sum(self.sheet[u][v] for u, v in zip(route, route[1:]))
                c = 0.0
                for u, v in zip(route, route[1:]):
                    for o in self.sig.get((u, v), []):
                        pos.append(cum + (c + o) * (L / rl if rl else 1.0))
                    c += self.sheet[u][v]
            cum += L
        fr = sorted({round(p / cum, 4) for p in pos if cum and 0 < p < cum})
        out = [0.0] + fr + [1.0]
        self._bp[key] = out
        return out

    def km(self, a, b, ctx):
        p = self.prof.get(a, b, ctx)
        return p["km"] if p else None

    def fill(self, t, keep_all=False):
        rows = [list(r[:4]) for r in t["stops"]]
        ctx = {r[0] for r in rows}
        cum = [0.0]
        for i in range(len(rows) - 1):
            k = self.km(rows[i][0], rows[i + 1][0], ctx)
            cum.append(cum[-1] + (k if k else 1.0))
        timed = [i for i, r in enumerate(rows) if r[2] is not None or r[3] is not None]
        if len(timed) < 2:
            return None
        for p, q in zip(timed, timed[1:]):
            t0 = rows[p][3] if rows[p][3] is not None else rows[p][2]
            t1 = rows[q][2] if rows[q][2] is not None else rows[q][3]
            for i in range(p + 1, q):
                x = t0 + (t1 - t0) * (cum[i] - cum[p]) / ((cum[q] - cum[p]) or 1.0)
                rows[i][2] = rows[i][3] = round(x)
        for r in rows:
            if r[2] is None:
                r[2] = r[3]
            if r[3] is None:
                r[3] = r[2]
        if keep_all:
            return rows
        rows = [r for r in rows if r[2] is not None]
        return rows

    def index(self):
        """block entries: (a, b) directed consecutive non-halt stations -> sorted [(enter = dep at a, exit = arr at b,
        train)]; point entries: station -> sorted [(arr, dep, train, prev station, next station)]"""
        self.block = defaultdict(list)
        self.point = defaultdict(list)
        self.block_double = {}
        self.bpts = {}                               # (a, b) -> block points as fractions of the block (0 .. 1)
        self.block_base = defaultdict(list)          # without the day copies (statistics)
        self.knots = {}                              # (a, b, train) -> times at the halts inside the block
        for no, rows in self.trains.items():
            ctx = {r[0] for r in rows}
            idx = [i for i, r in enumerate(rows) if self.kind.get(r[0]) != "halt"]
            st = [rows[i] for i in idx]
            for (i, x), (j, y) in zip(zip(idx, st), zip(idx[1:], st[1:])):
                stood = (x[3] - x[2]) >= 60          # the train stood at the entry station before entering
                self.block_base[(x[0], y[0])].append((x[3], y[2], no))
                for shift in (-86400, 0, 86400):     # copies a day earlier / later: comparisons across midnight
                    self.block[(x[0], y[0])].append((x[3] + shift, y[2] + shift, no, stood))
                if (x[0], y[0]) not in self.bpts:
                    self.bpts[(x[0], y[0])] = self.block_points(rows, i, j, ctx)
                if j > i + 1:
                    self.knots[(x[0], y[0], no)] = self.block_knots(rows, i, j, ctx, x[3])
                if (x[0], y[0]) not in self.block_double:
                    self.block_double[(x[0], y[0])] = all(self.double(rows[k][0], rows[k + 1][0], ctx)
                                                          for k in range(i, j))
            for i, r in enumerate(st):
                for shift in (-86400, 0, 86400):
                    self.point[r[0]].append((r[2] + shift, r[3] + shift, no, st[i - 1][0] if i else None,
                                             st[i + 1][0] if i + 1 < len(st) else None))
        for v in self.block.values():
            v.sort()
        for v in self.point.values():
            v.sort()
        self.fit_block_spacing()

    def fit_block_spacing(self):
        """per double-track block: the tightest spacing GAPEKA keeps between consecutive trains at the block points
        (follower reaches a point - leader passed it, minimum over the points and over all GAPEKA pairs; dinas
        excluded). Where the sheet shows fewer block signals than the line has (Gambir - Manggarai: KRL 3 min behind
        intercity trains), this tells the planner what the line allows. A line condition per block, learned per
        edition; used only as an alternative to the block-point check."""
        fixed = self.pkg.get("rules", {}).get("headways", {}).get("double_block_spacing_s")
        if fixed:                                    # fitted on GAPEKA when the package was built (not on a scenario)
            self.block_hmin = {(a, b): float(h) for a, b, h in fixed}
            return
        ttype = {t["no"]: t["type"] for t in self.pkg["trains"]}
        self.block_hmin = {}
        for (a, b), occ in self.block_base.items():
            if not self.block_double.get((a, b)):
                continue
            f = self.bpts.get((a, b), (0.0, 1.0))
            occ = sorted(o for o in occ if ttype.get(o[2]) != "dinas")
            best = None
            for (e1, x1, n1), (e2, x2, n2) in zip(occ, occ[1:]):
                if x2 < x1:
                    continue
                k1, k2 = self.knots.get((a, b, n1)), self.knots.get((a, b, n2))
                h = min(e2 + knot_time(k2, g, x2 - e2, False) - e1 - knot_time(k1, g, x1 - e1, True) for g in f)
                best = h if best is None else min(best, h)
            if best is not None:
                self.block_hmin[(a, b)] = max(best, 60.0)

    def block_knots(self, rows, i, j, ctx, t0):
        """[(fraction of the block, arrival - t0, departure - t0)] at the block ends and the halts in between: a train
        with halts in the block is not at an even speed (Gambir - Manggarai: KRL stop at Gondangdia, Cikini)"""
        cum = [0.0]
        for k in range(i, j):
            cum.append(cum[-1] + (self.km(rows[k][0], rows[k + 1][0], ctx) or 1.0))
        L = cum[-1] or 1.0
        out = [(0.0, 0.0, 0.0)]
        for n in range(i + 1, j):
            out.append((cum[n - i] / L, rows[n][2] - t0, rows[n][3] - t0))
        out.append((1.0, rows[j][2] - t0, rows[j][2] - t0))
        return out

    def double(self, a, b, ctx=frozenset()):
        p = self.prof.get(a, b, set(ctx) | {a, b})
        return bool(p and p["double"])


def knot_time(knots, f, run, clear):
    """time (relative to the block entry) at fraction f: when the train reaches it (clear=False: arrival) or has
    passed it (clear=True: departure from a halt there); knots None = even speed over run"""
    if not knots:
        return run * f
    for (f0, a0, d0), (f1, a1, d1) in zip(knots, knots[1:]):
        if f <= f1 + 1e-9:
            if abs(f - f1) < 1e-4:
                return d1 if clear else a1
            if abs(f - f0) < 1e-4:
                return d0 if clear else a0
            return d0 + (a1 - d0) * (f - f0) / ((f1 - f0) or 1.0)
    return knots[-1][2] if clear else knots[-1][1]


def fit_headways(tl, ttype):
    """fitted from the GAPEKA timeline (recomputed per edition): minimum following gaps (0.1th percentile = the
    tightest gap GAPEKA uses apart from a few outliers, whole minutes, at least the 2-min dispatcher gap, dinas
    excluded). single: follower enters a block after the leader cleared it; cross: an opposing train
    enters after the other cleared the block. double: no fitted gap; the planner checks block points (stations + block
    signals): double_block_clear_min = the tightest margin GAPEKA keeps between the leader clearing a block and the
    follower entering it (times by distance inside the block; for information, 0 = no margin beyond 'block clear')."""
    def p1(v):
        v = sorted(v)
        return max(2.0, float(round(v[int(0.001 * (len(v) - 1))] / 60))) if v else None
    single, cross, dbl = [], [], defaultdict(list)
    for (a, b), occ in tl.block.items():
        occ = sorted(o for o in tl.block_base.get((a, b), []) if ttype.get(o[2]) != "dinas")
        if tl.block_double[(a, b)]:
            f = tl.bpts.get((a, b), (0.0, 1.0))
            for (e1, x1, n1), (e2, x2, n2) in zip(occ, occ[1:]):
                if x2 < x1:
                    continue                         # overtaken inside the block (not possible on one track)
                dbl["signals" if len(f) > 2 else "stations"].append(
                    min(e2 + (x2 - e2) * f[k] - e1 - (x1 - e1) * f[k + 1] for k in range(len(f) - 1)))
            continue
        for (e1, x1, n1), (e2, x2, n2) in zip(occ, occ[1:]):
            single.append(e2 - x1)
        for e1, x1, n1 in occ:
            for e2, x2, n2 in tl.block_base.get((b, a), []):
                if ttype.get(n2) != "dinas" and 0 <= e2 - x1 < 1800:
                    cross.append(e2 - x1)
    out = dict(single_follow_min=p1(single), single_cross_min=p1(cross),
               double_block_clear_min={k: round(sorted(v)[int(0.001 * (len(v) - 1))] / 60, 1)
                                       for k, v in sorted(dbl.items())},
               double_block_spacing_s=[[a, b, round(h)] for (a, b), h in sorted(tl.block_hmin.items())],
               note="single: 0.1th percentile of GAPEKA's gaps, whole minutes, >= 2; double: block points (stations + "
                    "block signals), double_block_spacing_s = tightest GAPEKA spacing at the block points per directed "
                    "block, >= 60 s (fallback where the sheet shows too few signals) (planner.fit_headways)")
    return out


# ================================================================================================ planner
def hhmm_s(s):
    h, m = s.split(":")
    return int(h) * 3600 + int(m) * 60


class Planner:
    """plan one train against the fixed timetable (timeline without that train)"""

    def __init__(self, pkg, timeline):
        import replay
        self.pkg, self.tl = pkg, timeline
        self.eng = replay.Engine(pkg)
        self.kind = timeline.kind
        hw = pkg["rules"]["headways"]
        self.single_follow = hw["single_follow_min"] * 60
        self.single_cross = hw["single_cross_min"] * 60
        gaps = {(w["event"], w["track"]): float(w["gap_min"]) * 60 for w in pkg["rules"]["wait_gaps"] if w["line"] == "*"}
        mw = [float(x["value"]) for x in pkg["rules"].get("engine_params", []) if x["param"] == "min_wait"]
        self.min_wait = (mw[0] if mw else 1) * 60
        ab = [float(x["value"]) for x in pkg["rules"].get("engine_params", []) if x["param"] == "approach_blocks"]
        self.approach_blocks = int(ab[0]) if ab else 1
        dg = [float(x["value"]) for x in pkg["rules"].get("engine_params", []) if x["param"] == "double_follow_gap"]
        self.double_gap = (dg[0] if dg else 2) * 60
        self.prefer_stop = True                 # double-track overtakes at the last planned stop (hold_at_stop)
        # GAPEKA times are whole minutes: gaps are checked with this tolerance (engine_params time_tolerance)
        tol = [float(x["value"]) for x in pkg["rules"].get("engine_params", []) if x["param"] == "time_tolerance"]
        self.tol = tol[0] if tol else 59.0
        self.overtake_gap = {"single": gaps.get(("overtake", "single"), 120), "double": gaps.get(("overtake", "double"), 150)}
        self.type = {t["no"]: t["type"] for t in pkg["trains"]}
        self.facultative = {t["no"]: bool(t.get("facultative")) for t in pkg["trains"]}
        self.scheduled = {t["no"] for t in pkg["trains"]}   # GAPEKA trains (a user-created train is not in here)
        self.ignore = set()                     # trains being displaced by the planned train (priority)
        self.displaced = set()
        self.displace = False
        ep = {x["param"]: x["value"] for x in pkg["rules"].get("engine_params", [])}
        self.prio_gap = int(float(ep.get("priority_number_gap", 100)))
        self.displace_ratio = float(ep.get("displace_cost_ratio", 3))
        # station tracks: Roman tracks in service and the longest usable one, over all diagrams of the station
        self.roman, self.longest = defaultdict(int), defaultdict(float)
        for L in pkg["layouts"]:
            for tr in L["tracks"]:
                name, kind, length, usable, towards, enter, through, oos = tr[:8]
                if kind == "roman" and not oos and (usable or through):
                    self.roman[L["station"]] += 1
                    self.longest[L["station"]] = max(self.longest[L["station"]], length or 0)
        # dinas tutup: station -> list of (from_s, to_s) closed windows (predicted hours)
        self.closed = defaultdict(list)
        for d in pkg["dinas_tutup"]:
            for st, h in d.get("hours", {}).items():
                self.closed[st].append((hhmm_s(h["closed_from"]), hhmm_s(h["closed_to"])))

    # ---- helpers
    def prio(self, no):
        """lower = higher priority: regular trains before KA Dinas, then the train number (PM 110 Pasal 11(2)), then
        regular before facultative"""
        import re
        m = re.match(r"\D*(\d+)", no)
        return (self.type.get(no) == "dinas", int(m.group(1)) if m else 10 ** 6, self.facultative.get(no, False), no)

    def outranks(self, a, b):
        """a has CLEARLY higher priority than b (research/cross_priority.py: GAPEKA crossings follow the number in 81 %
        when the numbers differ by >= 100, but only 57-61 % below that, close to chance; the direction (odd = from the
        east, even = from the west) makes no difference): a regular train over a KA Dinas; else the numbers differ by
        at least priority_number_gap; the same number: regular over facultative"""
        pa, pb = self.prio(a), self.prio(b)
        if pa[0] != pb[0]:
            return pa[0] < pb[0]
        if pb[1] - pa[1] >= self.prio_gap:
            return True
        return pa[1] == pb[1] and pa[2] < pb[2]

    def can_displace(self, other):
        """user: a train with priority runs through every station it does not serve: the conflicting SCHEDULED train
        is held before the block instead (re-planned afterwards). Allowed against a train it outranks or one of equal
        priority (numbers < priority_number_gap apart: then only if the total delay is smaller, see plan_priority). A
        train created outside the schedule never displaces a scheduled one."""
        return (self.displace and self.skip in self.scheduled and other in self.scheduled and other != self.skip
                and not self.outranks(other, self.skip))

    def is_closed(self, st, t):
        for a, b in self.closed.get(st, []):
            x = t % 86400
            if (a <= b and a <= x < b) or (a > b and (x >= a or x < b)):
                return True
        return False

    def can_wait(self, st, t0, t1, length):
        """T may stand at st from t0 to t1 while other trains enter / pass: a station (not a halt, not closed), with a
        Roman track; it must fit the longest one; the trains standing there at the same time must leave a track"""
        if self.kind.get(st) == "halt" or not self.roman.get(st) or self.is_closed(st, t0):
            return False
        if length and self.longest.get(st) and length > self.longest[st]:
            return False
        standing = sum(1 for a, d, no, *_ in self.tl.point.get(st, []) if a < t1 and d > t0 and d - a >= 60
                       and no != self.skip and no not in self.ignore)
        return standing + 1 < self.roman[st] + (1 if self.roman[st] == 1 else 0) or standing == 0

    def run_time(self, train, rows, p, q):
        if self.printed is not None:
            T = self.printed_run(p, q)
            if T is not None:
                # an end where the planner added a stop: printed time + the physics difference stop vs pass
                for n in (p, q):
                    if n in self.added and rows[n][1] == "stop":
                        as_pass = [list(r) for r in rows]
                        as_pass[n][1] = "pass"
                        a, b = self.eng.compose(train, rows, p, q), self.eng.compose(train, as_pass, p, q)
                        if a is not None and b is not None:
                            T += a - b
                return T
        T = self.eng.compose(train, rows, p, q)
        if T is None:                      # no profile: fall back to 1 min per km at 60 km/h
            T = 60.0 * max(1, q - p)
        return T

    def block_conflict(self, a, b, d, arr, mid, own_type, overtaking_ok, at=None, tknots=None):
        """first conflict for T in block a -> b entering at d, clearing at arr: returns (earliest new departure, other
        train, kind) or None. mid = elementary blocks inside (dinas tutup pieces span several); at = when T reached a
        (a train that passes a while T stands there overtakes T: T leaves after the dispatcher gap)"""
        at = d if at is None else at
        # tknots: T's times at the halts inside the block, relative to its departure (Timeline.block_knots)
        worst = None
        for x, y in mid:
            dbl = self.tl.block_double.get((x, y), self.tl.block_double.get((y, x), False))
            for e1, x1, no, stood in self.tl.block.get((x, y), []):   # same direction
                if x1 < d - 7200 or e1 > arr + 7200 or no == self.skip or no in self.ignore:
                    continue
                ot = self.type.get(no)
                if dbl:
                    # block points (stations + block signals of this track, halts excluded): a train may run into
                    # the next block only when the train ahead has cleared it (user: "trains usually only need the
                    # block ahead to clear"); times inside the block by distance. GAPEKA 2026 keeps this with no extra
                    # margin (0.0 min minimum at whole minutes; research/block_clear.py), so only the time tolerance.
                    f = self.tl.bpts.get((x, y), (0.0, 1.0)) if len(mid) == 1 else (0.0, 1.0)
                    run = arr - d
                    tl_ = self.tol
                    ok = self.tl.knots.get((x, y, no))      # the other train's times at halts in the block
                    if ok and abs(ok[-1][1] - (x1 - e1)) > 1:
                        ok = None
                    tk = tknots if len(mid) == 1 else None
                    o_at = lambda g, c: e1 + knot_time(ok, g, x1 - e1, c)
                    t_at = lambda g, c: d + knot_time(tk, g, run, c)
                    n = len(f) - 1
                    # other ahead, T behind: T reaches f[k] after the other has passed f[k+1]
                    g_ = self.double_gap                 # dispatcher gap after block clear (user)
                    need_t = max(o_at(f[k + 1], True) + g_ - knot_time(tk, f[k], run, False) for k in range(n))
                    # T ahead, other behind: the other reaches f[k] after T has passed f[k+1]; a faster train behind
                    # (approach_blocks = 1) must find the block clear one block point earlier: it sees the signal
                    # from there and is not slowed
                    ab = self.approach_blocks if (x1 - e1) < run - 30 else 0
                    ahead = all(o_at(f[max(k - ab, 0)], False) >= t_at(f[k + 1], True) + g_ - tl_ for k in range(n))
                    H = self.tl.block_hmin.get((x, y)) if len(mid) == 1 else None
                    if H is not None:
                        # or spaced like the tightest GAPEKA pair on this block at every block point
                        need_t = min(need_t, max(o_at(g, True) + H - knot_time(tk, g, run, False) for g in f))
                        ahead = ahead or all(o_at(g, False) >= t_at(g, True) + H - tl_ for g in f)
                    if e1 >= at - tl_ and n:
                        # overtaken at a: the waiting train leaves when the overtaker has passed the next block point
                        # + the dispatcher gap (user; wait_gaps overtake double)
                        need_t = max(need_t, o_at(f[1], True) + self.overtake_gap["double"])
                    behind = d >= need_t - tl_
                    if not (ahead or behind):
                        kind = "overtake" if x1 < arr else "follow"
                        if worst is None or need_t > worst[0]:
                            worst = (need_t, no, kind)
                else:
                    follow_o = 0 if (stood and e1 >= d) else self.single_follow   # overtaken here: leaves after T
                    if not (d >= x1 + self.single_follow - self.tol or e1 >= arr + follow_o - self.tol):
                        new = x1 + (self.overtake_gap["single"] if e1 > d - 1 else self.single_follow)
                        kind = "overtake" if e1 >= d else "follow"
                        if worst is None or new > worst[0]:
                            worst = (new, no, kind)
            if dbl:
                continue
            for e2, x2, no, _ in self.tl.block.get((y, x), []):       # opposite direction (single track)
                if x2 < d - 7200 or e2 > arr + 7200 or no == self.skip or no in self.ignore:
                    continue
                if not (d >= x2 + self.single_cross - self.tol or e2 >= arr + self.single_cross - self.tol):
                    new = x2 + self.single_cross
                    if worst is None or new > worst[0]:
                        worst = (new, no, "cross")
        return worst

    def t_knots(self, tr, rows, i, j, run):
        """the planned train's times at the halts between rows i and j (as written by _plan), relative to departure"""
        if j <= i + 1:
            return None
        ctx = {r[0] for r in rows}
        cum = [0.0]
        for k in range(i, j):
            cum.append(cum[-1] + (self.tl.km(rows[k][0], rows[k + 1][0], ctx) or 1.0))
        L = cum[-1] or 1.0
        out = [(0.0, 0.0, 0.0)]
        for n in range(i + 1, j):
            sub = self.run_time(tr, rows, i, n) if rows[n][1] == "halt" else None
            x = sub if sub else run * (n - i) / (j - i)
            out.append((cum[n - i] / L, x, x))
        out.append((1.0, run, run))
        return out

    def hold_at_stop(self, rows, idx, k, other, hold, hold_why, why, length):
        """double-track overtake (data: GAPEKA lets the faster train pass where the slower one stops anyway): hold T at
        its last planned stop before station idx[k] until the overtaker has reached the station after that stop +
        the overtake gap. False if there is no such stop (then T waits where the conflict is)."""
        o_rows = self.tl.trains.get(other)
        if not o_rows:
            return False
        o_at = {r[0]: n for n, r in enumerate(o_rows)}
        for n in reversed(idx[:k]):
            r = rows[n]
            if r[1] not in ("stop", "terminal") or n in hold or r[0] not in o_at:
                continue
            q = o_at[r[0]]
            nxt = [x for x in o_rows[q + 1:] if self.kind.get(x[0]) != "halt"]
            if not nxt:
                return False
            ref = nxt[0][2] + self.overtake_gap["double"]
            here = r[3] if r[3] is not None else r[2]
            if here is not None:                       # the overtaker's copy on T's day (times may pass 86400)
                ref += min((-86400, 0, 86400), key=lambda sh: abs(o_rows[q][2] + sh - here))
            if ref <= (r[3] or 0) or not self.can_wait(r[0], r[2] or r[3], ref, length):
                return False
            hold[n] = ref
            hold_why[n] = why
            return True
        return False

    def printed_run(self, p, q):
        """the train's printed running time p -> q (its own timed points; untimed points interpolated by the
        timeline); None if the train waited (stopped) between them"""
        a, b = self.printed[p], self.printed[q]
        if a[3] is None or b[2] is None:
            return None
        if any(r[1] == "stop" for r in self.printed[p + 1:q]):
            return None
        return b[2] - a[3]

    def plan(self, train, dep0, length=None, max_iter=60, printed_times=False, printed_from=None):
        """printed_times: keep the running times between the train's printed timed points (taken from printed_from,
        the unedited train, or the train itself)"""
        self.skip = train["no"]                 # the planned train never conflicts with its own old times
        self.printed = None
        self.added = set()
        if printed_times:
            tl_rows = self.tl.fill(printed_from or train, keep_all=True)
            self.printed = tl_rows if tl_rows and len(tl_rows) == len(train["stops"]) else None
        return self._plan(train, dep0, length, max_iter)

    def _plan(self, train, dep0, length=None, max_iter=60):
        """train: package-like dict (stops = rows [station, kind, arr, dep, vmax, origin] with planned dwell in
        arr / dep of stops); returns (rows with planned times, waits [(station, other train, kind, seconds)])"""
        base = [list(r) for r in train["stops"]]
        dwell = {i: (r[3] - r[2]) if r[1] == "stop" and r[2] is not None and r[3] is not None else 0
                 for i, r in enumerate(base)}
        extra_stop, forbid = set(), set()
        notes, hold, hold_why = {}, {}, {}
        for _ in range(max_iter):
            rows = [list(r) for r in base]
            for i in extra_stop:
                if rows[i][1] == "pass":
                    rows[i][1] = "stop"
            self.added = {n for n in extra_stop if base[n][1] == "pass"}   # only stops the planner added
            tr = dict(train, notes={k: v for k, v in notes.items()})
            idx = [i for i, r in enumerate(rows) if self.kind.get(r[0]) != "halt"]
            t = dep0
            rows[0][2], rows[0][3] = None, dep0
            waits, restart = [], False
            k = 0
            while k < len(idx) - 1:
                i = idx[k]
                # next block end: the next station that is open when T gets there (dinas tutup)
                m = k + 1
                while m < len(idx) - 1 and rows[idx[m]][1] == "pass" and self.is_closed(rows[idx[m]][0], t):
                    m += 1
                j = idx[m]
                run = self.run_time(tr, rows, i, j)
                d = max(t, hold.get(i, t))
                mid = [(rows[idx[n]][0], rows[idx[n + 1]][0]) for n in range(k, m)]
                why = hold_why.get(i) if d > t + 1 else None
                at = rows[i][2] if rows[i][2] is not None else t
                tknots = self.t_knots(tr, rows, i, j, run)
                for _ in range(200):
                    c = self.block_conflict(rows[i][0], rows[j][0], d, d + run, mid, train["type"], True, at, tknots)
                    if c is None:
                        break
                    if base[i][1] == "pass" and i not in extra_stop and self.can_displace(c[1]):
                        self.ignore.add(c[1])                   # T keeps running; the other is re-planned later
                        self.displaced.add(c[1])
                        continue
                    d = max(c[0], d + 1)
                    why = why or c                              # the first conflict is the reason for the wait
                if d > t + 1:                                   # T has to wait at rows[i]
                    st = rows[i][0]
                    arr_i = rows[i][2] if rows[i][2] is not None else t
                    if i == 0 or (rows[i][1] in ("stop", "terminal") or i in extra_stop) and self.can_wait(st, arr_i, d, length):
                        if rows[i][1] == "pass":
                            pass
                        waits.append((st, why[1], why[2], d - t))
                        notes[st] = {"cross": "x", "overtake": "=", "follow": "="}[why[2]]
                    elif self.prefer_stop and rows[i][1] == "pass" and why[2] in ("overtake", "follow") and \
                            self.tl.block_double.get((rows[i][0], rows[j][0]), False) and \
                            self.hold_at_stop(rows, idx, k, why[1], hold, hold_why, why, length):
                        restart = True                          # held at the last planned stop instead
                        break
                    elif rows[i][1] == "pass" and i not in forbid and self.can_wait(st, arr_i, d, length):
                        # the train stops here to wait: arrival later by the braking loss, departure from rest
                        prev_i = [n for n in idx[:k]][-1] if k else None
                        loss_in = 0.0
                        if prev_i is not None:
                            before = self.run_time(tr, rows, prev_i, i)
                            rows[i][1] = "stop"
                            self.added.add(i)
                            extra_stop.add(i)
                            loss_in = self.run_time(tr, rows, prev_i, i) - before
                        else:
                            rows[i][1] = "stop"
                            self.added.add(i)
                            extra_stop.add(i)
                        notes[st] = {"cross": "x", "overtake": "=", "follow": "="}[why[2]]
                        arr_i = t + loss_in
                        rows[i][2] = round(arr_i)
                        run = self.run_time(tr, rows, i, j)
                        tknots = self.t_knots(tr, rows, i, j, run)
                        d = max(d, arr_i + self.min_wait)
                        for _ in range(200):
                            c = self.block_conflict(rows[i][0], rows[j][0], d, d + run, mid, train["type"], True, arr_i,
                                                    tknots)
                            if c is None:
                                break
                            d = max(c[0], d + 1)
                        waits.append((st, why[1], why[2], d - arr_i))
                    else:                                       # cannot wait here: wait at an earlier station
                        forbid.add(i)
                        prev = [n for n in idx[:k] if n not in forbid and self.kind.get(rows[n][0]) != "halt"]
                        if not prev:
                            waits.append((st, why[1], why[2] + " (no place to wait)", d - t))
                        else:
                            extra_stop.add(prev[-1])
                            notes[rows[prev[-1]][0]] = {"cross": "x", "overtake": "=", "follow": "="}[why[2]]
                            restart = True
                            break
                # fill times along the block (halts / passes in between by the run share)
                rows[i][3] = d
                arr = d + run
                for n in range(i + 1, j):
                    sub = self.run_time(tr, rows, i, n) if rows[n][1] == "halt" else None
                    x = d + (sub if sub else run * (n - i) / (j - i))
                    rows[n][2] = rows[n][3] = round(x)
                rows[j][2] = round(arr)
                t = arr + (dwell.get(j, 0) if rows[j][1] == "stop" else 0)
                if j in extra_stop and rows[j][1] == "stop" and dwell.get(j, 0) == 0:
                    t = arr + 60                                # an added stop: at least 1 min
                rows[j][3] = round(t) if j < len(rows) - 1 else None
                k = m
            if not restart:
                return rows, waits
        return rows, waits


def _end(rows):
    for r in reversed(rows):
        if r[2] is not None or r[3] is not None:
            return r[2] if r[2] is not None else r[3]
    return None


def plan_priority(pkg, tl, P, train, dep0, printed_times=True, printed_from=None, max_trains=12, ratio=None):
    """plan a scheduled train with priority (user): where it would wait at a station it does not serve for a scheduled
    train it outranks (or one of equal priority), that train is displaced instead and re-planned afterwards (held
    before the block), in priority order, each against everything planned so far; it may displace others in turn.
    Accepted only if it pays (user: stop holding when the delay is much bigger than the gain): gain = how much earlier
    the train arrives than with plain planning; cost = the displaced trains' extra delay at their ends, divided by
    `ratio` (engine_params displace_cost_ratio) for trains the planned train outranks, counted in full for trains of
    equal priority (there the least total delay decides). cost > gain -> the plain plan (the train waits).
    The timeline is restored before returning. Returns ({train: (rows, waits)}, info)."""
    ratio = P.displace_ratio if ratio is None else ratio
    byno = {t["no"]: t for t in pkg["trains"]}
    P.displace, P.ignore, P.displaced = False, set(), set()
    plain = P.plan(train, dep0, printed_times=printed_times, printed_from=printed_from)
    saved = {}
    out = {}
    queue = [(train, dep0, printed_from)]
    try:
        while queue and len(out) < max_trains:
            t, dep, pf = queue.pop(0)
            P.displace, P.ignore, P.displaced = True, set(), set()
            rows, waits = P.plan(t, dep, printed_times=printed_times, printed_from=pf)
            out[t["no"]] = (rows, waits)
            for no in [t["no"]] + sorted(P.displaced, key=P.prio):
                if no not in saved:
                    saved[no] = tl.trains.get(no)
            tl.set_rows(t["no"], rows)
            for no in P.displaced:
                tl.trains.pop(no, None)                 # out of the way until it is planned again
            tl.index()
            done = set(out) | {q[0]["no"] for q in queue}
            for no in sorted(P.displaced - done, key=P.prio):
                o = byno[no]
                queue.append((o, o["stops"][0][3], o))
            queue.sort(key=lambda q: P.prio(q[0]["no"]))
    finally:
        P.displace, P.ignore, P.displaced = False, set(), set()
        for no, rows in saved.items():
            if rows is None:
                tl.trains.pop(no, None)
            else:
                tl.trains[no] = rows
        tl.index()
    me = train["no"]
    e0, e1 = _end(plain[0]), _end(out[me][0])
    gain = (e0 - e1) if e0 is not None and e1 is not None else 0.0
    cost, raw = 0.0, 0.0
    P.skip = me
    for no, (rows, _) in out.items():
        if no == me:
            continue
        old = _end(saved.get(no) or [])
        new = _end(rows)
        if old is None or new is None:
            continue
        d = max(0.0, new - old)
        raw += d
        cost += d / ratio if P.outranks(me, no) else d
    info = dict(gain_s=gain, cost_s=cost, delay_others_s=raw, displaced=[n for n in out if n != me],
                accepted=bool(len(out) > 1 and gain > 0 and cost <= gain and not queue))
    if not info["accepted"]:
        return {me: plain}, info
    return out, info


def replan_test(pkg, types=("intercity", "commuter", "krl"), physics=False, examples=0):
    """re-planning test (docs/simulator_package.md 5.3): every train is taken out and planned again from its printed
    departure against all other trains; its GAPEKA waits are cut to a 1-min stop; printed running times between its
    timed points are kept (physics=True: the package physics instead). Returns per type: trains, GAPEKA waits,
    planner waits, waits at the same station / with the same train, departure there within 1 min, trains without
    waits kept clean, end within 2 min, mean |end difference| (min)."""
    tl = Timeline(pkg)
    P = Planner(pkg, tl)
    gwait = defaultdict(set)
    for w in pkg["waits"]:
        if w.get("waiter"):
            other = [n for n in w["trains"] if n and n != w["waiter"]]
            gwait[w["waiter"]].add((w["station"], other[0] if other else None))
    stats = defaultdict(lambda: defaultdict(float))
    ex = []
    for t in pkg["trains"]:
        if t["type"] not in types or t["no"] not in tl.trains:
            continue
        G = gwait.get(t["no"], set())
        gst = {st for st, _ in G}
        stops = [list(r) for r in t["stops"]]
        for r in stops:
            if r[0] in gst and r[1] == "stop" and r[2] is not None and r[3] is not None:
                r[3] = r[2] + 60                  # the printed wait becomes a 1-min stop
        rows, waits = P.plan(dict(t, stops=stops, notes={}), stops[0][3], printed_times=not physics, printed_from=t)
        Pw = {(w[0], w[1]) for w in waits if w[3] >= 30}
        pst = {st for st, _ in Pw}
        s = stats[t["type"]]
        s["trains"] += 1
        s["g_waits"] += len(G)
        s["p_waits"] += len(Pw)
        s["station_hits"] += len(gst & pst)
        s["pair_hits"] += len(G & Pw)
        pdep = {r[0]: r[3] for r in rows}
        gdep = {r[0]: r[3] for r in t["stops"]}
        s["dep_within_1"] += sum(1 for st in gst if pdep.get(st) is not None and gdep.get(st) is not None
                                 and abs(pdep[st] - gdep[st]) <= 60)
        s["no_wait_trains"] += not G
        s["no_wait_trains_clean"] += (not G and not Pw)
        end_p, end_g = rows[-1][2], t["stops"][-1][2]
        if end_p is not None and end_g is not None:
            s["end_n"] += 1
            s["end_abs_min"] += abs(end_p - end_g) / 60
            s["end_within_2"] += abs(end_p - end_g) <= 120
        if examples and (G - Pw or Pw - G) and len(ex) < examples:
            ex.append((t["no"], t["type"], sorted(G), sorted(Pw)))
    out = {}
    for typ, s in stats.items():
        out[typ] = dict(trains=int(s["trains"]), gapeka_waits=int(s["g_waits"]), planner_waits=int(s["p_waits"]),
                        same_station=s["station_hits"] / max(s["g_waits"], 1),
                        same_station_and_train=s["pair_hits"] / max(s["g_waits"], 1),
                        departure_within_1min=s["dep_within_1"] / max(s["g_waits"], 1),
                        extra_waits=(s["p_waits"] - s["station_hits"]) / max(s["p_waits"], 1),
                        no_wait_trains_clean=s["no_wait_trains_clean"] / max(s["no_wait_trains"], 1),
                        end_within_2min=s["end_within_2"] / max(s["end_n"], 1),
                        end_mean_abs_min=s["end_abs_min"] / max(s["end_n"], 1))
    return out, ex
