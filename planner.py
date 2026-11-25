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
        self.block_double = getattr(self, "block_double", {}) or {}
        self.bpts = getattr(self, "bpts", {}) or {}  # (a, b) -> block points as fractions of the block (0 .. 1)
        self.block_base = defaultdict(list)          # without the day copies (statistics)
        self.knots = {}                              # (a, b, train) -> times at the halts inside the block
        self.keys_of = {}                            # train -> the index keys it occupies (incremental updates)
        self.ptimes = {}                             # train -> {(a, b, run, single block): times at block points}
        self.maxdur = defaultdict(float)             # block key / station -> longest occupation (bisect windows)
        for no in self.trains:
            self._add(no, sort=False)
        for v in self.block.values():
            v.sort()
        for v in self.point.values():
            v.sort()
        self.fit_block_spacing()

    def _add(self, no, sort=True):
        rows = self.trains[no]
        ctx = {r[0] for r in rows}
        idx = [i for i, r in enumerate(rows) if self.kind.get(r[0]) != "halt"]
        st = [rows[i] for i in idx]
        bk, pk, kk = [], [], []
        ins = bisect.insort if sort else (lambda lst, item: lst.append(item))
        for (i, x), (j, y) in zip(zip(idx, st), zip(idx[1:], st[1:])):
            key = (x[0], y[0])
            stood = (x[3] - x[2]) >= 60              # the train stood at the entry station before entering
            self.block_base[key].append((x[3], y[2], no))
            for shift in (-86400, 0, 86400):         # copies a day earlier / later: comparisons across midnight
                ins(self.block[key], (x[3] + shift, y[2] + shift, no, stood))
            if y[2] - x[3] > self.maxdur[key]:
                self.maxdur[key] = y[2] - x[3]
            bk.append(key)
            if key not in self.bpts:
                self.bpts[key] = self.block_points(rows, i, j, ctx)
            if j > i + 1:
                self.knots[(x[0], y[0], no)] = self.block_knots(rows, i, j, ctx, x[3])
                kk.append((x[0], y[0], no))
            if key not in self.block_double:
                self.block_double[key] = all(self.double(rows[k][0], rows[k + 1][0], ctx) for k in range(i, j))
        for i, r in enumerate(st):
            for shift in (-86400, 0, 86400):
                ins(self.point[r[0]], (r[2] + shift, r[3] + shift, no, st[i - 1][0] if i else None,
                                       st[i + 1][0] if i + 1 < len(st) else None))
            pk.append(r[0])
            if r[3] - r[2] > self.maxdur[r[0]]:
                self.maxdur[r[0]] = r[3] - r[2]
        self.keys_of[no] = (bk, pk, kk)

    def window(self, key, t0, t1, point=False):
        """the entries of block key (or station key, point=True) that can be occupied between t0 and t1: a slice of
        the sorted list found by bisect (the callers still filter each entry exactly)"""
        lst = (self.point if point else self.block).get(key)
        if not lst:
            return ()
        lo = bisect.bisect_left(lst, (t0 - self.maxdur[key] - 1.0,))
        hi = bisect.bisect_right(lst, (t1 + 1.0,))
        return lst[lo:hi]

    def _remove(self, no):
        bk, pk, kk = self.keys_of.pop(no, ((), (), ()))
        for key in set(bk):
            self.block[key] = [e for e in self.block[key] if e[2] != no]
            self.block_base[key] = [e for e in self.block_base[key] if e[2] != no]
        for st in set(pk):
            self.point[st] = [e for e in self.point[st] if e[2] != no]
        for k in kk:
            self.knots.pop(k, None)
        self.ptimes.pop(no, None)

    def drop(self, no):
        """take a train out of the timeline (incremental, no full index)"""
        if no in self.trains:
            self._remove(no)
            del self.trains[no]

    def put(self, no, rows, planned=True):
        """put a train into the timeline: planned rows ([station, kind, arr, dep, ...], via set_rows) or timeline
        rows (planned=False)"""
        if no in self.trains:
            self._remove(no)
        if planned:
            self.set_rows(no, rows)
        else:
            self.trains[no] = rows
        self._add(no)

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


WAGON_M = {"GD": 12.5, "GT": 13.2, "GK": 14.0}        # research/layout_checks.py (web lengths; GD = INKA 12.5 m)
LOCO_M = {"CC 201": 15.2, "CC 203": 15.2, "CC 204": 15.85, "CC 206": 15.85}


def train_length(t):
    """train length (m) from the package consist: locomotive-hauled passenger cars x 21 m + 16 m locomotive; multiple
    units (KRL, KRD, EMU-like commuters) cars x 20 m (a 12-car KRL = 240 m fits Bojonggede II-251; x 21 + 16 did not and
    made 53 GAPEKA KRL calls there 'impossible'); freight wagons x wagon length + locomotive; None without a consist"""
    c = t.get("consist") or {}
    if not c.get("cars"):
        return None
    if c.get("wagon"):
        return c["cars"] * WAGON_M.get(c["wagon"][:2], 14.0) + LOCO_M.get(c.get("locomotive"), 16.0)
    if t.get("type") == "krl" or t.get("run_class") == "commuter-emu" or (not c.get("locomotive") and
                                                                          t.get("type") == "commuter"):
        return c["cars"] * 20.0           # multiple units (KRL, KRD, Ka Feeder): ~20 m cars, no locomotive
    return c["cars"] * 21.0 + 16.0


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


def _ends(t):
    a, b = t["stops"][0], t["stops"][-1]
    return (a[0], a[3] if a[3] is not None else a[2]), (b[0], b[2] if b[2] is not None else b[3])


def duty_links(pkg):
    """consecutive trains of one rolling-stock unit (duties: rangkaian pokok sets, locomotive rosters) that connect
    at the same station: [(previous train, next train, duty kind)]"""
    T = {t["no"]: t for t in pkg["trains"]}
    out = []
    for d in pkg["duties"]:
        tr = [n for n in d["trains"] if n in T]
        for a, b in zip(tr, tr[1:]):
            (sa, _), (ea_st, ea) = _ends(T[a])
            (sb, db), _ = _ends(T[b])
            if ea_st == sb and ea is not None and db is not None:
                out.append((a, b, d["kind"]))
    return out


def fit_turnaround(pkg):
    """fitted per edition: the tightest turnaround GAPEKA uses (1st percentile, whole minutes, >= 1) per duty kind and
    the next train's type: next departure - previous arrival at the same station"""
    T = {t["no"]: t for t in pkg["trains"]}
    g = defaultdict(list)
    for a, b, kind in duty_links(pkg):
        ea = _ends(T[a])[1][1]
        db = _ends(T[b])[0][1]
        g[f"{kind}:{T[b]['type']}"].append(((db - ea) % 86400) / 60)
    out = {}
    for k, v in g.items():
        v.sort()
        out[k] = max(1.0, float(round(v[int(0.01 * (len(v) - 1))])))
    return dict(sorted(out.items()))


def station_exemptions(pkg, tl=None):
    """fitted per edition: [train, station] where the PRINTED timetable already breaks the station check (all
    trains there could not get a usable track that fits). The user tolerates existing scheduled cases (freight
    misfits, crossings with a train longer than the loop); new or edited trains stay strict."""
    tl = tl or Timeline(pkg)
    P = Planner(pkg, tl)
    out = []
    for no, rows in tl.trains.items():
        if P.type.get(no) == "dinas":
            continue
        P.skip = no
        st_rows = [r for r in rows if P.kind.get(r[0]) != "halt"]
        for k, r in enumerate(st_rows):
            nxt = st_rows[k + 1][0] if k + 1 < len(st_rows) else None
            if P.station_free(r[0], r[2], r[3], P.length.get(no), nxt):
                out.append([no, r[0]])
    return sorted(out)


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
        _ep = {x["param"]: x["value"] for x in pkg["rules"].get("engine_params", [])}
        self.dispatcher_gap = float(_ep.get("dispatcher_gap", 2)) * 60
        self.release_margin = float(_ep.get("release_margin", 1)) * 60
        ab = [float(x["value"]) for x in pkg["rules"].get("engine_params", []) if x["param"] == "approach_blocks"]
        self.approach_blocks = int(ab[0]) if ab else 1
        dg = [float(x["value"]) for x in pkg["rules"].get("engine_params", []) if x["param"] == "double_follow_gap"]
        self.double_gap = (dg[0] if dg else 2) * 60
        self.prefer_stop = True
        self.recovery = True                    # a late train runs down to its technical minimum (recover)
        self.hold_slack = 600                   # s: a hold at the last stop may exceed the wait it replaces by this                 # double-track overtakes at the last planned stop (hold_at_stop)
        # GAPEKA times are whole minutes: gaps are checked with this tolerance (engine_params time_tolerance)
        tol = [float(x["value"]) for x in pkg["rules"].get("engine_params", []) if x["param"] == "time_tolerance"]
        self.tol = tol[0] if tol else 59.0
        self.overtake_gap = {"single": gaps.get(("overtake", "single"), 120), "double": gaps.get(("overtake", "double"), 150)}
        self.type = {t["no"]: t["type"] for t in pkg["trains"]}
        self.facultative = {t["no"]: bool(t.get("facultative")) for t in pkg["trains"]}
        self.scheduled = {t["no"] for t in pkg["trains"]}   # GAPEKA trains (a user-created train is not in here)
        self.windows = []                       # track work (window time): dict(a, b, start, end, direction=None)
        self.ignore = set()                     # trains being displaced by the planned train (priority)
        self.displaced = set()
        self.displaced_at = {}
        self.forced = set()                     # trains held by the long-train rule (any priority)
        self.displaced_blk = {}                 # displaced train -> the block (a, b) of the train it gave way to
        self.tail = False                       # plan_priority past its cap
        self.local = False                      # dispatch(): decide each hold locally (worth_holding)
        self.no_hold = set()                    # (holder, held) pairs dispatch() found not worth it after all
        self.hold_gain = {}                     # (holder, held) -> the holder's wait avoided
        self.root = None                        # the edited train of a plan_priority chain
        self.chain_rule = False                 # True: chain trains displace only trains they clearly outrank (tested: worse)
        self.stop_displace = False              # True: displacement also at its own stops (tested: worse)
        self.displace = False
        ep = {x["param"]: x["value"] for x in pkg["rules"].get("engine_params", [])}
        self.prio_gap = int(float(ep.get("priority_number_gap", 50)))
        self.displace_ratio = float(ep.get("displace_cost_ratio", 3))
        # station tracks: Roman tracks in service and the longest usable one, over all diagrams of the station
        # station tracks (Roman, in service), merged over the station's diagrams (one per line) by name + n-th
        # occurrence: the longest length, the union of the neighbours each is usable towards; a track whose direction
        # the reader could not derive (usable '') counts as usable both ways (unknown, not forbidden)
        merged = defaultdict(dict)
        for L in pkg["layouts"]:
            seen = defaultdict(int)
            for tr in L["tracks"]:
                name, kind, length, usable, towards, enter, through, oos = tr[:8]
                if kind != "roman" or oos:
                    continue
                seen[name] += 1
                key = (name, seen[name])
                m = merged[L["station"]].setdefault(key, dict(name=name, length=0.0, towards=set(), unknown=False))
                m["length"] = max(m["length"], length or 0.0)
                m["towards"] |= set((towards or "").split())
                if not usable and not through:
                    m["unknown"] = True
        self.tracks = {st: list(v.values()) for st, v in merged.items()}
        self.roman = {st: len(v) for st, v in self.tracks.items()}
        self.longest = {st: max((x["length"] for x in v), default=0.0) for st, v in self.tracks.items()}
        self.length = {t["no"]: train_length(t) for t in pkg["trains"]}
        # rolling stock (duties): a train cannot leave before its unit's previous train has arrived + turnaround
        turn = pkg["rules"].get("headways", {}).get("turnaround_min", {})
        self.duty_prev, self.duty_next = {}, defaultdict(list)
        self.link_gap = {}                            # (prev, next) -> (printed gap s, printed arrival, printed dep)
        T = {t["no"]: t for t in pkg["trains"]}
        for a, b, kind in duty_links(pkg):
            m = turn.get(f"{kind}:{self.type.get(b)}", 1.0) * 60
            printed = (_ends(T[b])[0][1] - _ends(T[a])[1][1]) % 86400
            m = min(m, printed)                       # a printed turnaround tighter than the fitted one is kept
            self.duty_prev.setdefault(b, []).append((a, m))
            self.link_gap[(a, b)] = (printed, _ends(T[a])[1][1], _ends(T[b])[0][1])
            self.duty_next[a].append((b, m))
        self.exempt = {(x["train_no"], x["station"]) for x in pkg["rules"].get("fit_exemptions", [])}
        # printed GAPEKA calls that break the station check (misfit crossings KAI accepts): tolerated as printed
        self.exempt |= {tuple(x) for x in pkg["rules"].get("headways", {}).get("station_exemptions", [])}
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

    def worth_holding(self, c, d, run):
        """dispatcher's local decision (user: stop holding when the delay caused is much bigger than the gain): T would
        wait c[0] - d here; holding the other train costs it about (T clears the block + dispatcher gap) - its own entry
        into the block. Hold it if that is at most displace_cost_ratio x the gain when T clearly outranks it, at most the
        gain when the two are equal. Without local mode (plan_priority) always True (decided globally there)."""
        if not self.local or len(c) < 4 or c[3] is None:
            return True
        if (self.skip, c[1]) in self.no_hold:
            return False                               # tried before: the held train lost far more downstream
        gain = max(c[0] - d, 0.0)
        hold = max(d + run + self.dispatcher_gap - c[3], 0.0)
        limit = gain * (self.displace_ratio if self.outranks(self.skip, c[1]) else 1.0)
        if hold <= limit:
            self.hold_gain[(self.skip, c[1])] = self.hold_gain.get((self.skip, c[1]), 0.0) + gain
            return True
        return False

    def fit_forced(self, st, other, length):
        """T is longer than every Roman track at st while the other train fits one (or its length is unknown)"""
        lo = self.longest.get(st)
        if not lo or not length or length <= lo:
            return False
        ol = self.length.get(other)
        return ol is None or ol <= lo

    def can_displace(self, other):
        """user: a train with priority runs through every station it does not serve: the conflicting SCHEDULED train
        is held before the block instead (re-planned afterwards). Allowed against a train it outranks or one of equal
        priority (numbers < priority_number_gap apart: then only if the total delay is smaller, see plan_priority). A
        train created outside the schedule never displaces a scheduled one."""
        if not (self.displace and self.skip in self.scheduled and other in self.scheduled and other != self.skip):
            return False
        if self.tail:
            return False                               # chain tail: no priority holding (long-train rule only)
        if any(n == other for n, _ in self.duty_next.get(self.skip, [])):
            return False                               # its own set's next trip follows it anyway (plan_edit)
        if self.chain_rule and self.root is not None and self.skip != self.root:
            return self.outranks(self.skip, other)     # in the chain: only trains it clearly outranks (no ping-pong)
        return not self.outranks(other, self.skip)

    def window_conflict(self, a, b, d, arr):
        """window time (track work, PTDI: re-plan that day only): T may not be in block a -> b while a window closes a
        sheet section on its route (both tracks, or only the direction given as 'X->Y'); T enters after the window.
        Returns (earliest new departure, None, 'window') or None"""
        if not self.windows:
            return None
        route = self.tl.sheet_route(a, b, 60) or [a, b]
        legs = set(zip(route, route[1:]))
        for w in self.windows:
            hit = (w["a"], w["b"]) in legs or (w["b"], w["a"]) in legs
            if not hit:
                continue
            if w.get("direction"):
                x, y = w["direction"].split("->")
                if (x, y) not in legs:
                    continue
            for sh in (-86400, 0, 86400):
                s0, s1 = w["start"] + sh, w["end"] + sh
                if d < s1 and arr > s0:
                    return (s1, None, "window")
        return None

    def ready_time(self, no, near):
        """earliest departure of train no from its unit's previous train(s): their arrival (as planned now, in the
        timeline) + the fitted turnaround; times taken on the day nearest `near`"""
        ready = None
        for prev, m in self.duty_prev.get(no, []):
            rows = self.tl.trains.get(prev)
            if not rows:
                continue
            gap, a_old, d_old = self.link_gap[(prev, no)]
            delta = (rows[-1][2] - a_old + 43200) % 86400 - 43200      # how late the previous trip is now
            ready = max(ready or 0, d_old - gap + delta + m)            # on the printed departure's day
        return ready if ready is not None else near

    def is_closed(self, st, t):
        for a, b in self.closed.get(st, []):
            x = t % 86400
            if (a <= b and a <= x < b) or (a > b and (x >= a or x < b)):
                return True
        return False

    def can_wait(self, st, t0, t1, length, towards=None):
        """T may stand at st from t0 to t1 (user rules, rules/priority.csv): only at a station (not a halt, not closed)
        with Roman tracks. Every moment another train is there (standing or passing), all trains present plus T need
        a track each: usable in their own direction (towards their next station) and long enough (matching). T alone
        at the station need not fit (stop alone: it may stand on the main). A T longer than every usable track while
        others come would block the station entry: not allowed (blocked station; tolerated for printed freight in
        fit_exemptions)."""
        if self.kind.get(st) == "halt" or not self.roman.get(st) or self.is_closed(st, t0):
            return False
        return self.station_free(st, t0, t1, length, towards) is None

    def station_free(self, st, t0, t1, length, towards=None):
        """None if T can be at st from t0 to t1 next to the other trains there (see can_wait); else the trains present
        at the first moment it cannot (list of (arr, dep, train, next station))"""
        if self.kind.get(st) == "halt" or not self.roman.get(st):
            return None
        # whole-minute times (time_tolerance): a train leaving in the minute T arrives no longer counts
        # only a train that STANDS needs to fit its track (a train running through needs a free track, any length)
        others = [(a, d - self.tol, no, nxt, d - a >= 60) for a, d, no, prv, nxt in self.tl.window(st, t0, t1, True)
                  if a <= t1 and d - self.tol >= t0 and no != self.skip and no not in self.ignore]
        if not others:
            return None                                  # stop alone
        tracks = self.tracks[st]

        def fits(tr, ln, tw):
            if ln and tr["length"] and ln > tr["length"]:
                return False
            return tr["unknown"] or not tw or not tr["towards"] or tw in tr["towards"] or \
                not any(tw in x["towards"] for x in tracks)      # direction not on this diagram: any track
        exempt = (self.skip, st) in self.exempt
        moments = sorted({max(a, t0) for a, d, no, nxt, sd in others})
        for x in moments:
            present = [(self.length.get(no) if sd else None, nxt) for a, d, no, nxt, sd in others if a <= x <= d]
            need = present + [(None if exempt or t1 - t0 < 60 else length, towards)]
            # greedy matching: most constrained train first, shortest suitable track
            need.sort(key=lambda q: sum(fits(tr, q[0], q[1]) for tr in tracks))
            free = list(tracks)
            for ln, tw in need:
                ok = sorted((tr for tr in free if fits(tr, ln, tw)), key=lambda tr: tr["length"])
                if not ok:
                    return [o[:4] for o in others if o[0] <= x <= o[1]]
                free.remove(ok[0])
        return None

    def release(self, rows, i, j, d, run, stay, length, nxt_after):
        """user (release rule, rules/priority.csv): T must not arrive at a station without a free track for it. If
        station rows[j] is full when T would arrive, T leaves rows[i] so that it ARRIVES when a track frees: earliest
        departure = the time the blocking train leaves - running time + dispatcher gap; by default 1 min before the train
        ahead leaves (GAPEKA practice, release_margin: it protects T's path beyond), no such margin when T ends there or
        goes another way than the train ahead. Returns (new departure, blocking train) or None."""
        st = rows[j][0]
        if self.is_closed(st, d + run):
            return None
        arr = d + run
        for _ in range(30):
            busy = self.station_free(st, arr, arr + stay, length, nxt_after)
            if not busy:
                break
            arr = min(dep for a, dep, no, nx in busy) + 1    # the first train to leave frees a track
        else:
            return None
        if arr <= d + run + 1:
            return None
        lead = min(busy_first := self.station_free(st, d + run, d + run + stay, length, nxt_after) or [],
                   key=lambda o: o[1], default=None)
        new = arr - run + self.dispatcher_gap - 1
        if lead and nxt_after is not None and lead[3] == nxt_after:
            new = max(new, lead[1] - self.release_margin)
        return (max(new, d + 1), lead[2] if lead else None)

    def recover(self, train, rows, p, q, run, t):
        """a late train recovers time (user: real trains run at their technical minimum when late): leaving rows[p] at t
        later than printed, it runs max(technical minimum, printed run - lateness), never ahead of its printed times.
        Only with printed running times (the reference for 'late'); Planner.recovery switches it."""
        if not self.recovery or self.printed is None:
            return run
        pd = self.printed[p][3] if self.printed[p][3] is not None else self.printed[p][2]
        if pd is None:
            return run
        late = t - pd
        if late <= 60:
            return run
        mn = self.eng.minimum(train, rows, p, q)
        if mn is None:
            return run
        return max(min(run, mn + 0.0) if mn < run else run, run - late)

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
        worst = self.window_conflict(a, b, d, arr)
        for x, y in mid:
            dbl = self.tl.block_double.get((x, y), self.tl.block_double.get((y, x), False))
            if dbl:
                # bounds on T's knot times, for a quick no-conflict test (same result as the full test below)
                tk_ = tknots if len(mid) == 1 else None
                t_lo = min([0.0] + [v for kn in tk_ for v in kn[1:]]) if tk_ else 0.0
                t_hi = max([arr - d] + [v for kn in tk_ for v in kn[1:]]) if tk_ else arr - d
                g_max = max(self.double_gap, self.overtake_gap["double"], 0.0)
            tpts = None
            for e1, x1, no, stood in self.tl.window((x, y), d - 7200, arr + 7200):   # same direction
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
                    # quick test: every time of the other train lies in [e1 + o_lo, e1 + o_hi], T's in [d + t_lo,
                    # d + t_hi]; the H branch only lowers need_t, so 'behind' holds when d clears the latest possible
                    # need_t, 'ahead' when the other's earliest time clears T's latest + gap
                    if ok:
                        o_lo = min([0.0] + [v for kn in ok for v in kn[1:]])
                        o_hi = max([x1 - e1] + [v for kn in ok for v in kn[1:]])
                    else:
                        o_lo, o_hi = 0.0, x1 - e1
                    if d >= e1 + o_hi + g_max - t_lo - tl_ + 1e-6 or \
                            e1 + o_lo >= d + t_hi + self.double_gap - tl_ + 1e-6:
                        continue
                    tk = tknots if len(mid) == 1 else None
                    # times at the block points f: the other train's (relative to e1, cached per train and block)
                    # and T's (relative to d, once per block): oA / tA reaching the point, oC / tC passed it
                    pc = self.tl.ptimes.setdefault(no, {})
                    pkey = (x, y, x1 - e1, len(mid) == 1)
                    if pkey not in pc:
                        pc[pkey] = ([knot_time(ok, g, x1 - e1, False) for g in f],
                                    [knot_time(ok, g, x1 - e1, True) for g in f])
                    rA, rC = pc[pkey]
                    oA = [e1 + v for v in rA]
                    oC = [e1 + v for v in rC]
                    if tpts is None:
                        tpts = ([knot_time(tk, g, run, False) for g in f], [knot_time(tk, g, run, True) for g in f])
                    tA, tC = tpts
                    n = len(f) - 1
                    # other ahead, T behind: T reaches f[k] after the other has passed f[k+1]
                    g_ = self.double_gap                 # dispatcher gap after block clear (user)
                    need_t = max(oC[k + 1] + g_ - tA[k] for k in range(n))
                    # T ahead, other behind: the other reaches f[k] after T has passed f[k+1]; a faster train behind
                    # (approach_blocks = 1) must find the block clear one block point earlier: it sees the signal
                    # from there and is not slowed
                    ab = self.approach_blocks if (x1 - e1) < run - 30 else 0
                    ahead = True
                    for k in range(n):
                        if not oA[max(k - ab, 0)] >= (d + tC[k + 1]) + g_ - tl_:
                            ahead = False
                            break
                    H = self.tl.block_hmin.get((x, y)) if len(mid) == 1 else None
                    if H is not None:
                        # or spaced like the tightest GAPEKA pair on this block at every block point
                        need_t = min(need_t, max(oC[k] + H - tA[k] for k in range(n + 1)))
                        ahead = ahead or all(oA[k] >= (d + tC[k]) + H - tl_ for k in range(n + 1))
                    if e1 >= at - tl_ and n:
                        # overtaken at a: the waiting train leaves when the overtaker has passed the next block point
                        # + the dispatcher gap (user; wait_gaps overtake double)
                        need_t = max(need_t, oC[1] + self.overtake_gap["double"])
                    behind = d >= need_t - tl_
                    if not (ahead or behind):
                        kind = "overtake" if x1 < arr else "follow"
                        if worst is None or need_t > worst[0]:
                            worst = (need_t, no, kind, e1)
                else:
                    follow_o = 0 if (stood and e1 >= d) else self.single_follow   # overtaken here: leaves after T
                    if not (d >= x1 + self.single_follow - self.tol or e1 >= arr + follow_o - self.tol):
                        new = x1 + (self.overtake_gap["single"] if e1 > d - 1 else self.single_follow)
                        kind = "overtake" if e1 >= d else "follow"
                        if worst is None or new > worst[0]:
                            worst = (new, no, kind, e1)
            if dbl:
                continue
            for e2, x2, no, _ in self.tl.window((y, x), d - 7200, arr + 7200):   # opposite direction
                if x2 < d - 7200 or e2 > arr + 7200 or no == self.skip or no in self.ignore:
                    continue
                if not (d >= x2 + self.single_cross - self.tol or e2 >= arr + self.single_cross - self.tol):
                    new = x2 + self.single_cross
                    if worst is None or new > worst[0]:
                        worst = (new, no, "cross", e2)
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
            # the overtaker's copy (times may pass 86400) is the one that meets T at the conflict station: its time
            # there nearest to T's arrival there; else nearest to T's departure from this stop
            cs = rows[idx[k]][0]
            t_at = rows[idx[k]][2] if rows[idx[k]][2] is not None else (r[3] if r[3] is not None else r[2])
            o_cs = next((x[2] for x in o_rows if x[0] == cs), None)
            base_t, near = (o_cs, t_at) if o_cs is not None else (o_rows[q][2], r[3] if r[3] is not None else r[2])
            if near is not None:
                ref += min((-86400, 0, 86400), key=lambda sh: abs(base_t + sh - near))
            nx = next((x[0] for x in rows[n + 1:] if self.kind.get(x[0]) != "halt"), None)
            # only worth it if the hold is about as long as the wait at the conflict station would be (no holding a
            # train for hours at an earlier stop for an overtaker that is still far behind)
            wait_there = max((why[0] or 0) - (t_at or 0), 0) if t_at is not None else 0
            if ref - (r[3] or r[2] or 0) > wait_there + self.hold_slack:
                return False
            if ref <= (r[3] or 0) or not self.can_wait(r[0], r[2] or r[3], ref, length, nx):
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
        if length is None:
            length = self.length.get(train["no"])     # consist length (m); None = unknown (no fit check)
        dep0 = max(dep0, self.ready_time(train["no"], dep0))
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
        for it in range(max_iter):
            final = it == max_iter - 1      # last attempt: no more restarts, wait where the conflict is (flagged)
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
                run = self.recover(tr, rows, i, j, self.run_time(tr, rows, i, j), t)
                d = max(t, hold.get(i, t))
                mid = [(rows[idx[n]][0], rows[idx[n + 1]][0]) for n in range(k, m)]
                why = hold_why.get(i) if d > t + 1 else None
                at = rows[i][2] if rows[i][2] is not None else t
                tknots = self.t_knots(tr, rows, i, j, run)
                stay = dwell.get(j, 0) if rows[j][1] == "stop" else 0
                nxt_after = next((rows[n][0] for n in idx[m + 1:]), None)
                for _ in range(200):
                    c = self.block_conflict(rows[i][0], rows[j][0], d, d + run, mid, train["type"], True, at, tknots)
                    if c is None:
                        rl = self.release(rows, i, j, d, run, stay, length, nxt_after)
                        if rl is None:
                            break
                        c = (rl[0], rl[1], "release")
                    elif self.can_displace(c[1]) and (base[i][1] == "pass" or ((self.stop_displace or self.local) and
                                                                               self.outranks(self.skip, c[1]))) \
                            and self.worth_holding(c, d, run):
                        # T keeps running (a station it passes: any train it may displace; one of its stops or a
                        # stop added for a wait: only trains it clearly outranks); the other is re-planned later.
                        # plan_priority keeps this only if it pays (cost limit)
                        self.ignore.add(c[1])
                        self.displaced.add(c[1])
                        self.displaced_at.setdefault(c[1], rows[i][0])
                        self.displaced_blk.setdefault(c[1], (rows[i][0], rows[j][0]))
                        continue
                    d = max(c[0], d + 1)
                    why = why or c                              # the first conflict is the reason for the wait
                if d > t + 1:                                   # T has to wait at rows[i]
                    st = rows[i][0]
                    arr_i = rows[i][2] if rows[i][2] is not None else t
                    if i == 0 or (rows[i][1] in ("stop", "terminal") or i in extra_stop) and self.can_wait(st, arr_i, d, length, rows[j][0]):
                        if rows[i][1] == "pass":
                            pass
                        waits.append((st, why[1], why[2], d - t))
                        notes[st] = {"cross": "x", "overtake": "=", "follow": "=", "release": "=", "window": "="}[why[2]]
                    elif not final and self.prefer_stop and rows[i][1] == "pass" and why[2] in ("overtake", "follow") and \
                            self.tl.block_double.get((rows[i][0], rows[j][0]), False) and \
                            self.hold_at_stop(rows, idx, k, why[1], hold, hold_why, why, length):
                        restart = True                          # held at the last planned stop instead
                        break
                    elif rows[i][1] == "pass" and i not in forbid and self.can_wait(st, arr_i, d, length, rows[j][0]):
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
                        notes[st] = {"cross": "x", "overtake": "=", "follow": "=", "release": "=", "window": "="}[why[2]]
                        arr_i = t + loss_in
                        rows[i][2] = round(arr_i)
                        run = self.recover(tr, rows, i, j, self.run_time(tr, rows, i, j), d)
                        tknots = self.t_knots(tr, rows, i, j, run)
                        d = max(d, arr_i + self.min_wait)
                        for _ in range(200):
                            c = self.block_conflict(rows[i][0], rows[j][0], d, d + run, mid, train["type"], True, arr_i,
                                                    tknots)
                            if c is None:
                                break
                            d = max(c[0], d + 1)
                        waits.append((st, why[1], why[2], d - arr_i))
                    elif (self.displace and why[2] in ("cross", "overtake", "follow") and why[1]
                          and self.fit_forced(st, why[1], length)
                          and not any(n == why[1] for n, _ in self.duty_next.get(self.skip, []))
                          and self.skip in self.scheduled and why[1] in self.scheduled):
                        # user (long-train rule): T fits no track here but the other train does: the
                        # long train goes first; a crossing train waits in the loop, a faster train behind stays
                        # behind it (both re-planned after, whatever their number)
                        self.ignore.add(why[1])
                        self.displaced.add(why[1])
                        self.displaced_at.setdefault(why[1], st)
                        self.displaced_blk.setdefault(why[1], (rows[i][0], rows[j][0]))
                        self.forced.add(why[1])
                        restart = True
                        break
                    else:                                       # cannot wait here: wait at an earlier station
                        forbid.add(i)
                        prev = [n for n in idx[:k] if n not in forbid and self.kind.get(rows[n][0]) != "halt"]
                        if not prev or final:
                            waits.append((st, why[1], why[2] + " (no place to wait)", d - t))
                        else:
                            extra_stop.add(prev[-1])
                            notes[rows[prev[-1]][0]] = {"cross": "x", "overtake": "=", "follow": "=", "release": "=", "window": "="}[why[2]]
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
        return rows, waits                      # not reached: the last attempt never restarts


def hold_index(P, o, blk):
    """where a held train is held: its last timed station before it reaches the block it gave way at (its printed
    run before that stays as it is); 0 = from its origin"""
    if not blk:
        return 0
    st = o["stops"]
    pos = [k for k, r in enumerate(st) if r[0] in blk]
    if not pos:
        return 0
    for k in range(min(pos) - 1, -1, -1):
        r = st[k]
        if P.kind.get(r[0]) != "halt" and (r[3] is not None or r[2] is not None):
            return k
    return 0


def plan_from(P, o, dep, pf, k, printed_times=True):
    """plan train o, keeping its printed rows before index k and re-timing it from its stop k (its printed departure
    there is the earliest); k = 0: the whole train from dep"""
    if not k:
        return P.plan(o, dep, printed_times=printed_times, printed_from=pf)
    st = o["stops"]
    r = st[k]
    d0 = r[3] if r[3] is not None else r[2]
    sub = dict(o, stops=[list(x) for x in st[k:]])
    rows, waits = P.plan(sub, d0, printed_times=printed_times, printed_from=dict(pf or o, stops=st[k:]))
    head = [list(x) for x in st[:k]]
    if rows:
        rows[0][2] = r[2]                       # it arrived there as printed
    return head + rows, waits


def _end(rows):
    for r in reversed(rows):
        if r[2] is not None or r[3] is not None:
            return r[2] if r[2] is not None else r[3]
    return None


def plan_priority(pkg, tl, P, train, dep0, printed_times=True, printed_from=None, max_trains=30, ratio=None):
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
    P.root = train["no"]
    hold_k = {}                                 # held train -> index of its stop where it is held (re-timed from)
    saved = {}
    out = {}
    info_at = {}                                # displaced train -> (train it gave way to, station)
    queue = [(train, dep0, printed_from)]
    try:
        while queue and len(out) < max_trains:
            t, dep, pf = queue.pop(0)
            P.displace, P.ignore, P.displaced = True, set(), set()
            P.displaced_at, P.forced, P.displaced_blk = {}, set(), {}
            rows, waits = plan_from(P, t, dep, pf, hold_k.get(t["no"], 0), printed_times)
            info_at.update({o: (t["no"], st) for o, st in P.displaced_at.items()})
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
                hold_k[no] = hold_index(P, o, P.displaced_blk.get(no))
                queue.append((o, o["stops"][0][3], o))
            queue.sort(key=lambda q: P.prio(q[0]["no"]))
        # cap reached: the rest of the chain holds nobody for priority, only where the long-train rule forces it
        # (physical: it fits no loop), so no train is left out; a hard cap of 3 x max_trains ends it
        while queue and len(out) < 3 * max_trains:
            t, dep, pf = queue.pop(0)
            P.displace, P.ignore, P.displaced, P.tail = True, set(), set(), True
            P.displaced_at, P.forced, P.displaced_blk = {}, set(), {}
            rows, waits = plan_from(P, t, dep, pf, hold_k.get(t["no"], 0), printed_times)
            P.tail = False
            out[t["no"]] = (rows, waits)
            for no in sorted(P.displaced, key=P.prio):
                if no not in saved:
                    saved[no] = tl.trains.get(no)
            tl.set_rows(t["no"], rows)
            for no in P.displaced:
                tl.trains.pop(no, None)
            tl.index()
            done = set(out) | {q[0]["no"] for q in queue}
            for no in sorted(P.displaced - done, key=P.prio):
                o = byno[no]
                hold_k[no] = hold_index(P, o, P.displaced_blk.get(no))
                queue.append((o, o["stops"][0][3], o))
        while queue:                                # hard cap: plain (yields to all)
            t, dep, pf = queue.pop(0)
            P.displace, P.ignore, P.displaced = False, set(), set()
            rows, waits = plan_from(P, t, dep, pf, hold_k.get(t["no"], 0), printed_times)
            out[t["no"]] = (rows, waits)
            tl.set_rows(t["no"], rows)
            tl.index()
    finally:
        P.displace, P.ignore, P.displaced, P.root = False, set(), set(), None
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
    info = dict(gain_s=gain, cost_s=cost, delay_others_s=raw, displaced=[n for n in out if n != me], held_for=info_at,
                accepted=bool(len(out) > 1 and gain > 0 and cost <= gain and not queue))
    if not info["accepted"]:
        return {me: plain}, info
    return out, info


def dispatch(pkg, tl, P, train, dep0, printed_times=True, printed_from=None, max_trains=80, rounds=8, passes=3):
    """dispatching re-plan with a downstream check: after each pass, a hold whose held train ends up much later than
    the gain that justified it (final extra delay / weight > the holder's avoided wait; weight displace_cost_ratio when
    the holder clearly outranks it) is forbidden and the dispatch runs again (at most `passes`). See _dispatch_once."""
    byno = {t["no"]: t for t in pkg["trains"]}
    P.no_hold = set()
    for _ in range(passes):
        P.hold_gain = {}
        out, info = _dispatch_once(pkg, tl, P, train, dep0, printed_times, printed_from, max_trains, rounds)
        bad = set()
        for (holder, held), gain in P.hold_gain.items():
            if held not in out or info["held_for"].get(held, (None,))[0] != holder:
                continue
            e_new, e_old = _end(out[held][0]), _end(byno[held]["stops"])
            if e_new is None or e_old is None:
                continue
            w = P.displace_ratio if P.outranks(holder, held) else 1.0
            if (e_new - e_old) / w > gain + 60:
                bad.add((holder, held))
        if not bad:
            break
        P.no_hold |= bad
    info["forbidden_holds"] = sorted(P.no_hold)
    P.no_hold = set()
    return out, info


def _dispatch_once(pkg, tl, P, train, dep0, printed_times=True, printed_from=None, max_trains=80, rounds=8):
    """dispatching re-plan (the simulator's entry point for an edited scheduled train): the affected trains are
    re-planned TOGETHER, highest priority first, each against the fixed timetable plus the members already placed.
      - a member that would wait at a station it does not serve for a lower-priority train holds that train instead
        when it is worth it locally (Planner.worth_holding: your cost rule per conflict); the held train joins the set
      - the long-train rule (Planner.fit_forced) holds a train that fits a loop for one that does not
      - held trains are re-timed from where they are held (hold_index / plan_from); a unit's next trip joins the set
        when its arrival + turnaround is later than the next trip's departure (it leaves when the set is ready)
    Rounds repeat until no train joins (or max_trains / rounds). The timeline is restored before returning.
    Returns ({train: (rows, waits)}, info) with info['held_for'] = {held train: (train it gave way to, station)}."""
    byno = {t["no"]: t for t in pkg["trains"]}
    me = train["no"]
    members = {me: (train, dep0, printed_from or train)}
    hold_k, held_for, why_joined = {}, {}, {}
    saved = {}
    out = {}
    P.local = True
    try:
        for rnd in range(rounds):
            for no in members:
                if no not in saved:
                    saved[no] = tl.trains.get(no)
                tl.drop(no)
            new = {}
            out = {}
            for no in sorted(members, key=P.prio):
                t, dep, pf = members[no]
                P.displace, P.ignore, P.displaced, P.tail = True, set(), set(), False
                P.displaced_at, P.forced, P.displaced_blk, P.root = {}, set(), {}, me
                rows, waits = plan_from(P, t, dep, pf, hold_k.get(no, 0), printed_times)
                out[no] = (rows, waits)
                tl.put(no, rows)
                for y in sorted(P.displaced, key=P.prio):      # priority order (deterministic; the cap may bite)
                    if y not in members and y not in new and len(members) + len(new) < max_trains:
                        o = byno[y]
                        new[y] = (o, o["stops"][0][3], o)
                        hold_k[y] = hold_index(P, o, P.displaced_blk.get(y))
                        held_for[y] = (no, P.displaced_at.get(y))
                        why_joined[y] = "forced" if y in P.forced else "held"
                # rolling stock: the unit's next trip leaves only when the set is ready
                end = _end(rows)
                old = saved.get(no) or byno[no]["stops"]
                old_end = _end(old)
                for nxt, m in P.duty_next.get(no, []):
                    if nxt in members or nxt in new or end is None or len(members) + len(new) >= max_trains:
                        continue
                    rn = byno[nxt]["stops"]
                    start, ref = rn[0][3], old_end if old_end is not None else end
                    if start is None:
                        continue
                    while start < ref:
                        start += 86400
                    while start - 86400 >= ref:
                        start -= 86400
                    if start < end + m:
                        o = byno[nxt]
                        new[nxt] = (o, o["stops"][0][3], o)
                        why_joined[nxt] = "turnaround"
            if not new:
                break
            members.update(new)
    finally:
        P.local, P.displace, P.ignore, P.displaced, P.root = False, False, set(), set(), None
        for no, rows in saved.items():
            if rows is None:
                tl.drop(no)
            else:
                tl.put(no, rows, planned=False)
    late = {no: (_end(r) - _end(byno[no]["stops"])) for no, (r, w) in out.items()
            if _end(r) is not None and _end(byno[no]["stops"]) is not None}
    info = dict(accepted=True, displaced=[n for n in out if n != me and why_joined.get(n) in ("held", "forced")],
                held_for=held_for, joined=why_joined, rounds=rnd + 1,
                delay_others_s=sum(max(v, 0) for k, v in late.items() if k != me))
    return out, info


def plan_edit(pkg, tl, P, train, dep0, printed_times=True, printed_from=None, max_trains=80):
    """the simulator's entry point for one edited scheduled train: the dispatching re-plan (dispatch)"""
    return dispatch(pkg, tl, P, train, dep0, printed_times, printed_from, max_trains)


def plan_edit_chain(pkg, tl, P, train, dep0, printed_times=True, printed_from=None, max_trains=20):
    """the simulator's entry point for one edited / new train: plan_priority (priority + cost limit), then the
    rolling-stock consequences: when a planned train arrives too late for its unit's next train (arrival + fitted
    turnaround), that train is re-planned from the time its unit is ready (Planner.ready_time), and so on along the
    unit's day. Returns ({train: (rows, waits)}, info)."""
    byno = {t["no"]: t for t in pkg["trains"]}
    out, info = plan_priority(pkg, tl, P, train, dep0, printed_times, printed_from)
    saved = {}
    try:
        queue = list(out)
        while queue and len(out) < max_trains:
            no = queue.pop(0)
            rows = out[no][0]
            if no not in saved:
                saved[no] = tl.trains.get(no)
            old = saved[no]
            old_end = old[-1][2] if old else None             # the unit's day is read against the old arrival
            tl.set_rows(no, rows)
            tl.index()
            for nxt, m in P.duty_next.get(no, []):
                rn = tl.trains.get(nxt)
                end = _end(rows)
                if nxt in out or not rn or end is None:
                    continue
                start, ref = rn[0][3], old_end if old_end is not None else end
                while start < ref:
                    start += 86400
                while start - 86400 >= ref:
                    start -= 86400
                if start >= end + m:
                    continue
                o = byno[nxt]
                P.displace, P.ignore, P.displaced = False, set(), set()
                out[nxt] = P.plan(o, o["stops"][0][3], printed_times=printed_times, printed_from=o)
                info.setdefault("turnaround", []).append(nxt)
                queue.append(nxt)
    finally:
        for no, rows in saved.items():
            if rows is None:
                tl.trains.pop(no, None)
            else:
                tl.trains[no] = rows
        tl.index()
    return out, info


def capacity_warnings(pkg, tl):
    """user (rules/priority.csv event capacity): warn when a section's daily load (trains over its path, counted from
    the timeline as planned now) exceeds its capacity K (PDF pages 1866-1874); the trains still run. Returns
    [(a, b, load, capacity)] for sections over K."""
    out = []
    seqs = {no: [r[0] for r in rows] for no, rows in tl.trains.items()}
    for c in pkg["capacity"]:
        path = c.get("path") or [c["a"], c["b"]]
        if len(path) < 2 or not c.get("capacity"):
            continue
        a, b = path[0], path[-1]
        n = 0
        for no, seq in seqs.items():
            pos = {st: i for i, st in enumerate(seq)}
            if a in pos and b in pos and abs(pos[a] - pos[b]) >= 1:
                n += 1
        if n > c["capacity"]:
            out.append((a, b, n, c["capacity"]))
    return out


WAIT_KINDS = ("cross", "overtake", "follow", "release", "window")
def P_outranks(pkg, a, b):
    """Planner.outranks for the texts (no timeline needed)"""
    import re
    T = {t["no"]: t for t in pkg["trains"]}
    gap = int(float({x["param"]: x["value"] for x in pkg["rules"].get("engine_params", [])}
                    .get("priority_number_gap", 50)))

    def key(n):
        m = re.match(r"\D*(\d+)", n)
        t = T.get(n, {})
        return (t.get("type") == "dinas", int(m.group(1)) if m else 10 ** 6, bool(t.get("facultative")))
    ka, kb = key(a), key(b)
    if ka[0] != kb[0]:
        return ka[0] < kb[0]
    return kb[1] - ka[1] >= gap or (ka[1] == kb[1] and ka[2] < kb[2])


def explain(pkg, train_no, waits, lang="id", info=None):
    """user-facing sentences (texts msg.*) for a plan: one per wait [(station, other, kind, seconds)] of 30 s or more,
    plus the trains held for it (info['displaced']) and the unit's next trips moved (info['turnaround'])"""
    tx = pkg["texts"]
    name = {c: s.get("name") or c for c, s in pkg["stations"].items()}
    out = []
    for st, other, kind, sec in waits:
        k = kind.split(" ")[0]
        if sec < 30 or f"msg.{k}" not in tx:
            continue
        out.append(tx[f"msg.{k}"][lang].format(train=train_no, other=other or "-", station=name.get(st, st),
                                               minutes=round(sec / 60)))
    for other in ((info or {}).get("displaced", []) if (info or {}).get("accepted") else []):
        by, st = (info.get("held_for") or {}).get(other, (train_no, None))
        key = "msg.displaced" if P_outranks(pkg, by, other) else "msg.displaced_equal"
        out.append(tx[key][lang].format(train=by, other=other, station=name.get(st, st or "-")))
    return out


def replan_test(pkg, types=("intercity", "commuter", "krl"), physics=False, examples=0, on_train=None):
    """re-planning test (docs/simulator_package.md 5.3): every train is taken out and planned again from its printed
    departure against all other trains; its GAPEKA waits are cut to a 1-min stop; printed running times between its
    timed points are kept (physics=True: the package physics instead). Returns per type: trains, GAPEKA waits,
    planner waits, waits at the same station / with the same train, departure there within 1 min, trains without
    waits kept clean, end within 2 min, mean |end difference| (min). on_train(no, rows, waits): each plan (golden)."""
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
        if on_train:
            on_train(t["no"], rows, waits)
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
