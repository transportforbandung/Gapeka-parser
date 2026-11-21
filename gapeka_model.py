"""Running-time model for the GAPEKA network (moved from research/physics_model.py + research/relearn.py).

Inputs: gapeka.db (timetable), infra.db (sections, Umum, restrictions, gradient), rules (station zones, buffers).
  speed cap per 50 m cell = min(train's timetable vmax for the section, Umum, local restriction on the train's track,
                                station zone)
  acceleration min(a0, power / v), braking b, fixed per rolling-stock group (KRL / locomotive+KRD / freight)
  calibration per train type (krl / commuter / intercity): margin, constant, per halt, terminus arrival, origin
  departure, start from / stop at a station, crossing / overtaking roles, km on steep sections
  corrections (ridge regression on training residuals): per directed section, station end and halt station, per type
Freight is not modelled (user: freight follows its timetable; times between timed points are shared by distance).

Halt times: the time between two timed points is shared by the model's sub-leg times (split, Bandung / Jabodetabek)
or taken from the previous departure (front, Surabaya); see rules/halt_rules.csv.
"""
import csv
import heapq
import json
import os
import re
import sqlite3
import zlib
from collections import defaultdict

import numpy as np

DX = 50.0
PHYS = {"krl": (0.45, 4.2, 0.85), "loco/krd": (0.15, 2.3, 0.15), "freight": (0.08, 1.2, 0.08)}
EMU_LIKE = {"Ka Feeder"}                     # non-KRL services with EMU performance (user / research)
TYPES = ("krl", "commuter", "intercity")     # calibrated types (freight follows the timetable, dinas the index)


def held_out(no):
    return zlib.crc32(no.encode()) % 5 == 0


def phys_group(ttype, service=""):
    if service in EMU_LIKE or ttype == "krl":
        return "krl"
    return "freight" if ttype == "freight" else "loco/krd"


def track_kind(note):
    """small note in a restriction cell: 'Jalur ...' = station tracks only; 'Dari/Ke Jalur ...' = EMU platform tracks"""
    n = note or ""
    if re.search(r"\b(dari|ke)\b[ /a-z]*jalur", n, re.I):
        return "platform-track"
    if re.search(r"jalur", n, re.I) or re.search(r"(^|\s)[IVX]{1,4},", n):
        return "siding"
    return "line"


# ================================================================================================ infrastructure
class Infra:
    """sections of infra.db in travel direction; missing pairs are derived (join through a sheet station, split a
    sheet section by gapeka.db distances or running times, last resort gapeka.db distance)"""

    def __init__(self, infra_path, ttdb):
        c = sqlite3.connect(infra_path)
        self.sec = defaultdict(list)
        for a, b, km, umum, ruling, ha, hb, var, ctx in c.execute(
                "select a,b,km,umum_kmh,ruling_gradient_permil,elevation_a_m,elevation_b_m,variant,context from sections"):
            self.sec[(a, b)].append(dict(a=a, b=b, km=km, umum=umum, ruling=ruling, h_a=ha, h_b=hb, variant=var or "",
                                         ctx=set((ctx or "").split()), restr=[]))
        for a, b, o0, o1, v, tracks, note, var in c.execute(
                "select a,b,offset_from_km,offset_to_km,speed_kmh,tracks,note,variant from restrictions"):
            for s in self.sec[(a, b)]:
                if s["variant"] == (var or ""):
                    s["restr"].append((o0, o1, v, tracks, note or ""))
        self.nbr = defaultdict(set)
        for a, b in self.sec:
            self.nbr[a].add(b)
            self.nbr[b].add(a)
        self.dbkm = {}
        for a, b, km in ttdb.execute("select a,b,km from station_links"):
            self.dbkm.setdefault(frozenset((a, b)), km)
        self.tt = {}
        prev = {}
        for no, st, ar, dp in ttdb.execute("select train_no,station,arr_s,dep_s from stops order by train_no,seq"):
            t_here = ar if ar is not None else dp
            p = prev.get(no)
            if p is not None and p[1] is not None and t_here is not None:
                T = t_here - p[1]
                if 0 < T < 3600:
                    k = frozenset((p[0], st))
                    self.tt[k] = min(self.tt.get(k, 1e9), T)
            prev[no] = (st, dp if dp is not None else ar)
        self._cache = {}

    # ---- one sheet section in travel direction; each restriction carries the travel direction on the sheet
    def _pick(self, a, b, ctx):
        vs = self.sec.get((a, b))
        rev = False
        if not vs:
            vs, rev = self.sec.get((b, a)), True
        if not vs:
            return None
        v = next((x for x in vs if x["variant"] and x["ctx"] & ctx), None) or \
            next((x for x in vs if not x["variant"]), vs[0])
        return v, rev

    def directed(self, a, b, ctx=frozenset()):
        p = self._pick(a, b, ctx)
        if p is None:
            return None
        s, rev = p
        L = s["km"]
        down = not rev                       # sheet order a -> b = top -> bottom = HULU track
        rs = [((L - o1, L - o0) if rev else (o0, o1)) + (v, tr, note, down) for o0, o1, v, tr, note in s["restr"]]
        g = s["ruling"]
        if g is None and s["h_a"] is not None and s["h_b"] is not None and L:
            g = abs(s["h_b"] - s["h_a"]) / L
        return dict(km=L, umum=s["umum"], restr=rs, ruling=g, derived=None)

    def _join(self, p1, p2):
        L1 = p1["km"]
        u = max([x for x in (p1["umum"], p2["umum"]) if x] or [0]) or None
        rs = list(p1["restr"]) + [(o0 + L1, o1 + L1, v, tr, n, d) for o0, o1, v, tr, n, d in p2["restr"]]
        if p1["umum"] and u and p1["umum"] < u:
            rs.append((0.0, L1, p1["umum"], "both", "", True))
        if p2["umum"] and u and p2["umum"] < u:
            rs.append((L1, L1 + p2["km"], p2["umum"], "both", "", True))
        g = None
        if p1["ruling"] is not None and p2["ruling"] is not None:
            g = (p1["ruling"] * L1 + p2["ruling"] * p2["km"]) / (L1 + p2["km"])
        return dict(km=L1 + p2["km"], umum=u, restr=rs, ruling=g, derived="joined")

    @staticmethod
    def _cut(p, lo, hi, how):
        rs = [(max(o0, lo) - lo, min(o1, hi) - lo, v, tr, n, d) for o0, o1, v, tr, n, d in p["restr"] if o1 > lo and o0 < hi]
        return dict(km=hi - lo, umum=p["umum"], restr=rs, ruling=p["ruling"], derived=how)

    def profile(self, a, b, ctx=frozenset()):
        key = (a, b, frozenset(ctx) & self._ctx_keys(a, b))
        if key in self._cache:
            return self._cache[key]
        p = self.directed(a, b, ctx)
        if p is None:
            p = self._derive(a, b, ctx)
        self._cache[key] = p
        return p

    def _ctx_keys(self, a, b):
        vs = self.sec.get((a, b)) or self.sec.get((b, a)) or []
        out = set()
        for v in vs:
            out |= v["ctx"] if v["variant"] else set()
        return out

    def _derive(self, a, b, ctx):
        for x in self.nbr[a]:                                     # a - x - b on the sheet
            if b in self.nbr[x]:
                p1, p2 = self.directed(a, x, ctx), self.directed(x, b, ctx)
                if p1 and p2:
                    return self._join(p1, p2)
        dab = self.dbkm.get(frozenset((a, b)))
        for x in self.nbr[a]:                                     # sheet section a - x, b in between
            pax, dbx = self.directed(a, x, ctx), self.dbkm.get(frozenset((b, x)))
            if pax and dab and dbx and abs(dab + dbx - pax["km"]) / pax["km"] < 0.15:
                return self._cut(pax, 0.0, pax["km"] * dab / (dab + dbx), "split by gapeka.db km")
        for x in self.nbr[b]:                                     # sheet section x - b, a in between
            pxb, dxa = self.directed(x, b, ctx), self.dbkm.get(frozenset((x, a)))
            if pxb and dab and dxa and abs(dxa + dab - pxb["km"]) / pxb["km"] < 0.15:
                return self._cut(pxb, pxb["km"] * dxa / (dxa + dab), pxb["km"], "split by gapeka.db km")
        for x in self.nbr[a]:                                     # split by shortest running times
            pax = self.directed(a, x, ctx)
            t1, t2 = self.tt.get(frozenset((a, b))), self.tt.get(frozenset((b, x)))
            if pax and t1 and t2:
                return self._cut(pax, 0.0, pax["km"] * t1 / (t1 + t2), "split by running time")
        for x in self.nbr[b]:
            pxb = self.directed(x, b, ctx)
            t1, t2 = self.tt.get(frozenset((x, a))), self.tt.get(frozenset((a, b)))
            if pxb and t1 and t2:
                return self._cut(pxb, pxb["km"] * t1 / (t1 + t2), pxb["km"], "split by running time")
        if dab and 0.3 < dab < 40:
            return dict(km=dab, umum=None, restr=[], ruling=None, derived="gapeka.db distance")
        return None

    def path(self, a, b, max_km=400):
        """shortest chain of sheet sections a .. b (KA Dinas routes); list of stations"""
        dist, prev, q = {a: 0.0}, {}, [(0.0, a)]
        while q:
            d, u = heapq.heappop(q)
            if u == b:
                break
            if d > dist.get(u, 1e9) or d > max_km:
                continue
            for v in self.nbr[u]:
                p = self.directed(u, v)
                nd = d + p["km"]
                if nd < dist.get(v, 1e9):
                    dist[v], prev[v] = nd, u
                    heapq.heappush(q, (nd, v))
        if b not in dist:
            return None
        out = [b]
        while out[-1] != a:
            out.append(prev[out[-1]])
        return out[::-1]


# ================================================================================================ rules
def load_rules(rules_dir):
    """station zones and buffers from rules/*.csv (same structure as the research rules.json)"""
    R = []
    p = os.path.join(rules_dir, "station_zones.csv")
    for r in csv.DictReader(open(p)) if os.path.exists(p) else []:
        dep, arr = int(r["dep_length_m"] or 0), int(r["arr_length_m"] or 0)
        R.append(dict(station=r["station"], towards=r["towards"], sides=[s for s, L in (("dep", dep), ("arr", arr)) if L],
                      dep_m=dep, arr_m=arr, speed=int(r["speed_kmh"]), types=r["train_types"].split(),
                      when=r["applies_when"], source=r["source"]))
    p = os.path.join(rules_dir, "section_buffers.csv")
    for r in csv.DictReader(open(p)) if os.path.exists(p) else []:
        secs = [tuple(x.split("-")) for x in r["sections"].split()]
        val = float(r["seconds"].split()[0])
        if r["kind"] == "buffer_km":
            R.append(dict(kind="buffer_km", sections=secs, seconds_per_km=val, types=r["train_types"].split(), source=r["source"]))
        else:
            R.append(dict(kind="buffer", sections=secs, seconds=val, types=r["train_types"].split(), source=r["source"]))
    p = os.path.join(rules_dir, "station_buffers.csv")
    for r in csv.DictReader(open(p)) if os.path.exists(p) else []:
        R.append(dict(kind="station_buffer", station=r["station"], sides=[r["side"]], when=r["applies_when"],
                      seconds=float(r["seconds"]), types=r["train_types"].split(), source=r["source"]))
    return R


def apply_zones(g, rules):
    cap, st = g["cap"], g["stations"]
    for z in rules:
        if z.get("kind") or g["type"] not in z["types"]:
            continue
        for side in z["sides"]:
            L = z["dep_m"] if side == "dep" else z["arr_m"]
            n = min(int(L / DX), len(cap))
            if not n:
                continue
            at, nb, rest = (st[0], st[1], g["start_rest"]) if side == "dep" else (st[-1], st[-2], g["end_rest"])
            if at != z["station"] or (z["towards"] != "*" and nb != z["towards"]):
                continue
            terminal = g["first"] if side == "dep" else g["last"]
            w = z["when"]
            if (w == "stop" and not rest) or (w == "pass" and rest) or (w == "midstop" and not (rest and not terminal)) \
                    or (w == "terminal" and not (rest and terminal)):
                continue
            cap = cap.copy()
            if side == "dep":
                cap[:n] = np.minimum(cap[:n], z["speed"])
            else:
                cap[-n:] = np.minimum(cap[-n:], z["speed"])
    return cap


def buffer_s(g, rules):
    pairs = list(zip(g["stations"], g["stations"][1:]))
    secs = set(pairs) | {(b, a) for a, b in pairs}
    tot = 0.0
    for b in rules:
        k = b.get("kind")
        if k not in ("buffer", "buffer_km", "station_buffer") or g["type"] not in b["types"]:
            continue
        if k == "buffer" and secs & set(b["sections"]):
            tot += b["seconds"]
        elif k == "buffer_km":
            want = {frozenset(p) for p in b["sections"]}
            tot += b["seconds_per_km"] * sum(L for (a_, b_), (L, _) in zip(pairs, g["sec_grade"]) if frozenset((a_, b_)) in want)
        elif k == "station_buffer":
            for side in b["sides"]:
                at, rest, term = (g["stations"][0], g["start_rest"], g["first"]) if side == "dep" else \
                    (g["stations"][-1], g["end_rest"], g["last"])
                if at != b["station"]:
                    continue
                w = b["when"]
                if (w == "terminal" and rest and term) or (w == "stop" and rest) or w == "any":
                    tot += b["seconds"]
    return tot


# ================================================================================================ physics
def prepare(legs):
    order = sorted(range(len(legs)), key=lambda i: len(legs[i]["cap"]))
    out = []
    for s in range(0, len(order), 300):
        idx = order[s:s + 300]
        Lc = max(len(legs[i]["cap"]) for i in idx)
        cap = np.full((len(idx), Lc), 300.0)
        zero = np.zeros((len(idx), Lc + 1), bool)
        valid = np.zeros((len(idx), Lc), bool)
        for r, i in enumerate(idx):
            g = legs[i]
            n = len(g["cap"])
            cap[r, :n] = g["cap"]
            valid[r, :n] = True
            zero[r, 0] = g["start_rest"]
            zero[r, n] = g["end_rest"]
            for h in g["halts"]:
                zero[r, min(max(h, 1), n - 1)] = True
        out.append(dict(idx=np.array(idx), cap=cap / 3.6, zero=zero, valid=valid))
    return out


def simulate(batches, n, a0, p, b, cumulative=False):
    """running time per leg (s); cumulative=True also returns the time at every cell boundary"""
    res, cum = np.zeros(n), [None] * n
    for B in batches:
        cap = B["cap"]
        N, Lc = cap.shape
        node = np.empty((N, Lc + 1))
        node[:, 0] = cap[:, 0]
        node[:, 1:-1] = np.minimum(cap[:, :-1], cap[:, 1:])
        node[:, -1] = cap[:, -1]
        node[B["zero"]] = 0.0
        v = np.empty((N, Lc + 1))
        v[:, 0] = node[:, 0]
        for j in range(Lc):
            acc = np.minimum(a0, p / np.maximum(v[:, j], 1.0))
            v[:, j + 1] = np.minimum(node[:, j + 1], np.sqrt(np.maximum(v[:, j] ** 2 + 2 * DX * acc, (8 / 3.6) ** 2)))
        for j in range(Lc - 1, -1, -1):
            v[:, j] = np.minimum(v[:, j], np.sqrt(v[:, j + 1] ** 2 + 2 * DX * b))
        t = 2 * DX / np.maximum(v[:, :-1] + v[:, 1:], 1e-3) * B["valid"]
        res[B["idx"]] = t.sum(1)
        if cumulative:
            ct = np.concatenate([np.zeros((N, 1)), np.cumsum(t, 1)], 1)
            for r, i in enumerate(B["idx"]):
                cum[i] = ct[r]
    return (res, cum) if cumulative else res


def lad(X, y):
    c = np.linalg.lstsq(X, y, rcond=None)[0]
    for _ in range(40):
        w = 1 / np.maximum(np.abs(y - X @ c), 5.0)
        c = np.linalg.lstsq(X * w[:, None] ** .5, y * w ** .5, rcond=None)[0]
    return c


# ================================================================================================ model
class Model:
    def __init__(self, ttdb, infra, rules):
        self.db, self.infra, self.rules = ttdb, infra, rules
        self.ttype, self.service = {}, {}
        for no, svc, typ in ttdb.execute("select no, service, type from trains"):
            self.service[no], self.ttype[no] = svc or "", typ or "intercity"
        self.sym = defaultdict(set)
        for no, st, s in ttdb.execute("select train_no, station, symbol from notes"):
            self.sym[(no, st)].add(s)
        self.calib, self.eff = {}, {}

    # ---- one leg (list of stations, vmax per row), cap per 50 m cell
    def make_leg(self, no, stations, vmaxes, start_rest, end_rest, halt_idx=(), first=False, last=False, ttype=None):
        typ = ttype or self.ttype.get(no, "intercity")
        emu = phys_group(typ, self.service.get(no, "")) == "krl"
        caps, sec_grade, halts = [], [], []
        ctx = frozenset(stations)
        for i, (a, b) in enumerate(zip(stations, stations[1:])):
            pr = self.infra.profile(a, b, ctx)
            v = vmaxes[i]
            if pr is None or not v:
                return None
            L = pr["km"]
            nc = max(1, int(round(L * 1000 / DX)))
            cap = np.full(nc, float(min(v, pr["umum"] or v)))
            for o0, o1, rv, tracks, note, down in pr["restr"]:
                k = track_kind(note)
                if k == "siding" or (k == "platform-track" and not emu):
                    continue
                if tracks == "hu" and not down or tracks == "hi" and down:
                    continue                     # restriction on the other track of a double track
                i0 = int(o0 * 1000 / DX)
                i1 = max(int(o1 * 1000 / DX), i0 + 1)
                cap[i0:i1] = np.minimum(cap[i0:i1], rv)
            caps.append(cap)
            sec_grade.append((L, pr["ruling"]))
            if i + 1 in halt_idx:
                halts.append(sum(len(c) for c in caps))
        g = dict(train=no, type=typ, service=self.service.get(no, ""), cap=np.concatenate(caps), halts=halts,
                 start_rest=start_rest, end_rest=end_rest, stations=list(stations), first=first, last=last,
                 sec_grade=sec_grade, halt_st=[stations[i] for i in halt_idx])
        g["cap"] = apply_zones(g, self.rules)
        g["km"] = len(g["cap"]) * DX / 1000
        return g

    def features(self, g, sim):
        def km_band(lo, hi):
            return sum(L for L, gr in g["sec_grade"] if gr is not None and lo <= gr < hi)

        def role(end):
            st, rest = (g["stations"][0], g["start_rest"]) if end == "start" else (g["stations"][-1], g["end_rest"])
            s = self.sym.get((g["train"], st), set())
            return [float(rest and "x" in s), float(rest and "=" in s), float((not rest) and bool(s & {"x", "//"}))]
        return np.array([sim, 1.0, len(g["halts"]), float(g["last"]), float(g["first"]), float(g["start_rest"]),
                         float(g["end_rest"])] + role("start") + role("end") + [km_band(5, 10), km_band(10, 99)])

    FEATURE_NAMES = ["sim", "const", "per_halt", "terminus_arrival", "origin_departure", "start_from_stop",
                     "stop_at_end", "start_wait_cross", "start_wait_overtaken", "start_pass_cross", "end_wait_cross",
                     "end_wait_overtaken", "end_pass_cross", "km_5_10_permil", "km_10plus_permil"]

    def corr_keys(self, g):
        st, t = g["stations"], g["type"]
        f = [("sec", a, b, t) for a, b in zip(st, st[1:])]
        f += [("halt", h, t) for h in g["halt_st"]]
        f.append(("dep", st[0], "stop" if g["start_rest"] else "pass", t))
        f.append(("arr", st[-1], "stop" if g["end_rest"] else "pass", t))
        return f

    def run_sim(self, legs, cumulative=False):
        by = defaultdict(list)
        for i, g in enumerate(legs):
            by[phys_group(g["type"], g["service"])].append(i)
        sim, cum = np.zeros(len(legs)), [None] * len(legs)
        for gr, idx in by.items():
            sub = [legs[i] for i in idx]
            r = simulate(prepare(sub), len(sub), *PHYS[gr], cumulative=cumulative)
            if cumulative:
                r, c = r
                for k, i in enumerate(idx):
                    cum[i] = c[k]
            sim[idx] = r
        return (sim, cum) if cumulative else sim

    # ---- legs between timed points of the timetable
    def timetable_legs(self):
        legs = []
        for no, typ in self.ttype.items():
            if typ not in TYPES:
                continue
            rows = self.db.execute("select station,kind,arr_s,dep_s,vmax from stops where train_no=? order by seq", (no,)).fetchall()
            timed = [i for i, r in enumerate(rows) if r[1] in ("stop", "origin_or_end") or (r[1] == "pass" and r[3] is not None)]
            for p, n in zip(timed, timed[1:]):
                A, B = rows[p], rows[n]
                t0 = A[3]
                t1 = B[2] if B[1] in ("stop", "origin_or_end") and B[2] is not None else B[3]
                if t0 is None or t1 is None:
                    continue
                T = t1 - t0
                hidx = [i - p for i in range(p + 1, n) if rows[i][1] == "halt"]
                g = self.make_leg(no, [r[0] for r in rows[p:n + 1]], [r[4] for r in rows[p:n + 1]], A[1] != "pass",
                                  B[1] != "pass", hidx, p == 0, n == len(rows) - 1)
                if g is None or not (0 < T < 4 * 3600):
                    continue
                spd = g["km"] / (T / 3600)
                if spd > g["cap"].max() * 1.1 or spd < 8:
                    continue
                g["T"] = T
                g["test"] = held_out(no)
                g["noted"] = bool(self.sym.get((no, A[0])) or self.sym.get((no, B[0])))
                legs.append(g)
        return legs

    def fit(self, log=print):
        legs = self.timetable_legs()
        sim = self.run_sim(legs)
        X = np.array([self.features(g, s) for g, s in zip(legs, sim)])
        y = np.array([g["T"] for g in legs], float)
        typ = np.array([g["type"] for g in legs])
        train = np.array([not g["test"] for g in legs])
        test = ~train
        buf = np.array([buffer_s(g, self.rules) for g in legs])
        self.calib = {t: lad(X[train & (typ == t)], (y - buf)[train & (typ == t)]) for t in TYPES if np.any(train & (typ == t))}
        base = np.array([X[i] @ self.calib[typ[i]] for i in range(len(legs))]) + buf
        # ridge corrections on training residuals
        from scipy.sparse import csr_matrix
        from scipy.sparse.linalg import lsqr
        F = [self.corr_keys(g) for g in legs]
        col, rr, cc = {}, [], []
        tr_idx = np.where(train)[0]
        for r, i in enumerate(tr_idx):
            for k in F[i]:
                rr.append(r)
                cc.append(col.setdefault(k, len(col)))
        A = csr_matrix((np.ones(len(rr)), (rr, cc)), shape=(len(tr_idx), len(col)))
        res = y[tr_idx] - base[tr_idx]
        keep = np.ones(len(res), bool)
        for _ in range(3):
            eff = lsqr(A[keep], res[keep], damp=2.0)[0]
            keep = np.abs(res - A @ eff) < 150
        support = np.asarray(A[keep].sum(0)).ravel()
        self.eff = {k: (float(eff[j]), int(support[j])) for k, j in col.items()}
        pred = base + np.array([sum(self.eff.get(k, (0, 0))[0] for k in F[i]) for i in range(len(legs))])
        noted = np.array([g["noted"] for g in legs])

        def bands(e):
            a = np.abs(e) / 60
            return (float(np.mean(a < .5)), float(np.mean((a >= .5) & (a <= 1))), float(np.mean(a > 1)), int(len(a)))
        stats = dict(legs=len(legs), physics_calibration=bands((base - y)[test & ~noted]),
                     full=bands((pred - y)[test & ~noted]), noted=bands((pred - y)[test & noted]),
                     by_type={t: bands((pred - y)[test & ~noted & (typ == t)]) for t in TYPES})
        return stats

    # ---- calibrated running time of one leg (no corrections), for sharing a leg time between halts
    def calibrated(self, g, sim):
        c = self.calib.get(g["type"]) if g["type"] in self.calib else self.calib.get("intercity")
        return float(self.features(g, sim) @ c) + buffer_s(g, self.rules)

    # ---- persistence (rules.db)
    def save(self, rdb):
        rdb.executescript("""
        DROP TABLE IF EXISTS fitted_calibration; DROP TABLE IF EXISTS fitted_corrections;
        CREATE TABLE fitted_calibration(train_type TEXT, feature TEXT, value REAL, source TEXT);
        CREATE TABLE fitted_corrections(kind TEXT, a TEXT, b TEXT, end_kind TEXT, train_type TEXT, seconds REAL,
                                        support INTEGER, source TEXT);
        """)
        for t, c in self.calib.items():
            for nm, v in zip(self.FEATURE_NAMES, c):
                rdb.execute("insert into fitted_calibration values(?,?,?,?)", (t, nm, float(v), "fitted"))
        for k, (v, n) in self.eff.items():
            if k[0] == "sec":
                row = ("section", k[1], k[2], None, k[3])
            elif k[0] == "halt":
                row = ("halt", k[1], None, None, k[2])
            else:
                row = (k[0], k[1], None, k[2], k[3])
            rdb.execute("insert into fitted_corrections values(?,?,?,?,?,?,?,?)", row + (round(v, 1), n, "fitted"))


# ================================================================================================ halt times
def halt_rule(rules_dir, no, service, ttype):
    """(region, method, dwell) from rules/halt_rules.csv: the first matching row"""
    for r in csv.DictReader(open(os.path.join(rules_dir, "halt_rules.csv"))):
        a = r["applies_to"]
        if a == "*" or (a.startswith("service:") and service in a[8:].split(";")) or \
                (a.startswith("type:") and ttype in a[5:].split(";")):
            return r["region"], r["method"], float(r["dwell_s"])
    return "default", "split", 20.0


def estimate_halts(model, rules_dir, halt_offsets=None, log=print):
    """halt times for every run of halts between two timed points -> [(train, seq, station, arr_s, dep_s, method)]"""
    db = model.db
    out, jobs = [], []
    for no, typ in model.ttype.items():
        if typ == "dinas":
            continue
        rows = db.execute("select seq,station,kind,arr_s,dep_s,vmax from stops where train_no=? order by seq", (no,)).fetchall()
        timed = [i for i, r in enumerate(rows) if r[3] is not None or r[4] is not None]
        for i0, i1 in zip(timed, timed[1:]):
            hs = [i for i in range(i0 + 1, i1) if rows[i][2] == "halt"]
            if not hs:
                continue
            p, n = rows[i0], rows[i1]
            dep = p[4] if p[4] is not None else p[3]
            arr = n[3] if n[3] is not None else n[4]
            if arr is None or dep is None or arr <= dep:
                continue
            jobs.append((no, typ, rows, i0, i1, hs, dep, arr))
    # sub-legs (stop to stop) for every job, simulated in one batch per rolling-stock group
    legs, ref = [], []
    for j, (no, typ, rows, i0, i1, hs, dep, arr) in enumerate(jobs):
        pts = [i0] + hs + [i1]
        for a, b in zip(pts, pts[1:]):
            g = None
            if typ != "freight":
                g = model.make_leg(no, [r[1] for r in rows[a:b + 1]], [r[5] for r in rows[a:b + 1]], True, True)
            if g is not None:
                ref.append((j, len(legs)))
                legs.append(g)
            else:
                ref.append((j, None))
    sim = model.run_sim(legs) if legs else []
    run = defaultdict(list)
    for (j, k) in ref:
        run[j].append(None if k is None else model.calibrated(legs[k], sim[k]))
    # shortest time for the same leg (same stations from p to n, same halts, same type): KAI Bandung shares this
    # minimum, counted from the train's own departure (spare time sits after the halts)
    tmin = {}
    for job in jobs:
        k = (job[1], tuple(r[1] for r in job[2][job[3]:job[4] + 1]), tuple(i - job[3] for i in job[5]))
        tmin[k] = min(tmin.get(k, 1e9), job[7] - job[6])
    counts = defaultdict(int)
    for j, (no, typ, rows, i0, i1, hs, dep, arr) in enumerate(jobs):
        region, method, dwell = halt_rule(rules_dir, no, model.service.get(no, ""), typ)
        r = run[j]
        if any(x is None for x in r):
            # freight (user: follow the timetable) or no profile: share the leg time by distance
            km = []
            pts = [i0] + hs + [i1]
            for a, b in zip(pts, pts[1:]):
                d = sum((model.infra.profile(rows[k][1], rows[k + 1][1]) or {}).get("km", 0) or 0 for k in range(a, b))
                km.append(d)
            if not all(km):
                continue
            r, method, how = km, "split", "distance"
        else:
            how = "model"
        r = np.array(r, float)
        m = len(hs)
        if method == "front" and how == "model":
            t = dep + np.cumsum(r)[:-1] + dwell * np.arange(m)
            if t[-1] + dwell + r[-1] * 0.5 > arr:            # does not fit: fall back to sharing the leg time
                method = "split"
        if method == "split-min" and how == "model":
            T = tmin[(typ, tuple(x[1] for x in rows[i0:i1 + 1]), tuple(i - i0 for i in hs))] - m * dwell
            if T > 0:
                r = r * T / r.sum()
                t = dep + np.cumsum(r)[:-1] + dwell * np.arange(m)
            else:
                method = "split"
        if method == "split" or how == "distance":
            T = (arr - dep) - m * dwell
            if T <= 0:
                continue
            r = r * T / r.sum()
            t = dep + np.cumsum(r)[:-1] + dwell * np.arange(m)
        for k, i in enumerate(hs):
            a_ = float(t[k])
            tag = f"{how}-{method}"
            off = (halt_offsets or {}).get((rows[i][1], rows[i - 1][1] if i else None, rows[i + 1][1] if i + 1 < len(rows) else None))
            if off is not None and typ == "krl":
                a_ += off
                tag += "+offset"
            out.append((no, rows[i][0], rows[i][1], int(round(a_)), int(round(a_ + dwell)), tag))
            counts[tag] += 1
    return out, dict(counts)


def fit_halt_offsets(model, rules_dir, kai_rows, log=print):
    """per (halt, previous station, next station) offset for KRL, from published KAI halt times (Jabodetabek):
    median of (KAI minute centre - model arrival) on training trains -> seconds to add to the model arrival"""
    est, _ = estimate_halts(model, rules_dir, None)
    E = {(no, st): (a, seq) for no, seq, st, a, d, m in est}
    seqst = {}
    for no, seq, st in model.db.execute("select train_no, seq, station from stops"):
        seqst[(no, seq)] = st
    res = defaultdict(list)
    for no, st, hhmm in kai_rows:
        if (no, st) not in E or held_out(no):
            continue
        a, seq = E[(no, st)]
        h, m = map(int, hhmm.split(":"))
        k = h * 60 + m
        d = (k * 60 + 30) - (a % 86400)
        d = (d + 43200) % 86400 - 43200
        res[(st, seqst.get((no, seq - 1)), seqst.get((no, seq + 1)))].append(d - 30)
    return {k: float(np.median(v)) for k, v in res.items() if len(v) >= 3}


# ================================================================================================ KA Dinas
def estimate_dinas(model, log=print):
    """KA Dinas: path through the sheet sections between the route stations; passing times from one simulated run
    (locomotive group; KRL trainsets as KRL), scaled to the index running time. Returns (stops, times)."""
    db = model.db
    stops_out, times_out, n_ok = [], [], 0
    for no, typ in model.ttype.items():
        if typ != "dinas":
            continue
        rows = db.execute("select seq,station,kind,arr_s,dep_s from stops where train_no=? order by seq", (no,)).fetchall()
        dep, arr = rows[0][4], rows[-1][3]
        route = [r[1] for r in rows]
        path = [route[0]]
        for a, b in zip(route, route[1:]):
            seg = model.infra.path(a, b)
            if seg is None:
                path = None
                break
            path += seg[1:]
        if not path or dep is None or arr is None:
            continue
        svc = model.service.get(no, "")
        ttype = "krl" if re.search(r"commuter|krl", svc, re.I) else "intercity"
        g = model.make_leg(no, path, [100] * len(path), True, True, (), True, True, ttype=ttype)
        if g is None:
            continue
        sim, cum = model.run_sim([g], cumulative=True)
        c = cum[0]
        # cell index of every station on the path
        idx, x = [0], 0.0
        for a, b in zip(path, path[1:]):
            x += model.infra.profile(a, b, frozenset(path))["km"]
            idx.append(min(int(round(x * 1000 / DX)), len(c) - 1))
        tt = np.array([c[i] for i in idx])
        tt = dep + tt * (arr - dep) / tt[-1]
        stops_out.append((no, path))
        times_out.append((no, [int(round(v)) for v in tt]))
        n_ok += 1
    return stops_out, times_out, n_ok
