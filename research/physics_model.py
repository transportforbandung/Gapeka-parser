"""Physics running-time model on the GAPEKA infrastructure columns.

Speed cap per 50 m cell = min(train's timetable vmax for the section, line 'Umum', local restriction 'Dibatasi Sampai').
Acceleration a(v) = min(a0, p / v) - g * gradient   (a0 starting acceleration, p specific power in (m/s)^2/s = W/kg)
Braking        b(v) = b + g * gradient
Halt: v = 0, dwell d.   Running time = sim * (1 + m) + c
"""
import re
import json
import pickle
import sqlite3
import sys
import zlib
from collections import defaultdict

import numpy as np
from scipy.optimize import minimize

sys.path.insert(0, "/home/user/Gapeka-parser")
import gapeka_pipeline as gp  # noqa: E402
from runtime_model import train_class  # noqa: E402

DX = 50.0
import os as _os
GRADE = _os.environ.get('GRADE', '0') == '1'
G = 9.81
PROF = json.load(open("line_profile.json"))


def apply_overrides(prof, path="restriction_overrides.csv"):
    """manual changes on top of the sheet extraction (rules database: local restrictions, source = manual)"""
    import csv
    if not _os.path.exists(path):
        return 0
    n = 0
    for o in csv.DictReader(open(path)):
        p = prof.get(f"{o['from']}-{o['to']}")
        o0, o1 = float(o["offset_from_km"]), float(o["offset_to_km"])
        if p is None:                                   # stored the other way round: mirror the offsets
            p = prof.get(f"{o['to']}-{o['from']}")
            if p is None:
                continue
            o0, o1 = p["km"] - o1, p["km"] - o0
        for r in p["restr"]:
            if r["v"] == int(o["speed_kmh"]) and abs(r["o0"] - o0) < 0.05 and abs(r["o1"] - o1) < 0.05:
                r["tags"] = o["set_tags"]
                r["override"] = o["source"]
                n += 1
    return n


N_OVERRIDES = apply_overrides(PROF)


def apply_pair_overrides(prof, path="pair_overrides.csv"):
    """manual station pairs missing on the sheet; split the enclosing sheet pair's restrictions where possible"""
    import csv
    if not _os.path.exists(path):
        return 0
    n = 0
    for o in csv.DictReader(open(path)):
        k = f"{o['from']}-{o['to']}"
        if k in prof or f"{o['to']}-{o['from']}" in prof:
            continue
        umum = next((p["umum"] for kk, p in prof.items() if o["from"] in kk.split("-") or o["to"] in kk.split("-")), None)
        prof[k] = dict(a=o["from"], b=o["to"], up=True, km=float(o["km"]), umum=umum, restr=[], h_a=None, h_b=None,
                       blocks=[], source=o["source"], note=o["note"])
        n += 1
    return n


N_PAIR_OVERRIDES = apply_pair_overrides(PROF)


def track_kind(note):
    """classify the small note in a restriction cell"""
    n = note or ""
    if re.search(r"\b(dari|ke)\b[ /a-z]*jalur", n, re.I):
        return "platform-track"
    if re.search(r"jalur", n, re.I) or re.search(r"(^|\s)[IVX]{1,4},", n):
        return "siding"
    return "line"


_DBKM = None


def _dbkm(a, b):
    global _DBKM
    if _DBKM is None:
        import sqlite3
        _DBKM = {}
        for x, y, km, pg in sqlite3.connect("/home/user/Gapeka-parser/gapeka.db").execute("select a,b,km,page from station_links"):
            _DBKM.setdefault(frozenset((x, y)), km)
    return _DBKM.get(frozenset((a, b)))


def _oriented(a, b):
    """sheet pair a->b as (dict in travel direction, reversed?)"""
    p = PROF.get(f"{a}-{b}")
    if p is not None:
        return p, False
    p = PROF.get(f"{b}-{a}")
    return (p, True) if p is not None else (None, None)


def _flip(p):
    L = p["km"]
    q = dict(p, a=p["b"], b=p["a"], h_a=p["h_b"], h_b=p["h_a"], up=not p["up"], _flipped=not p.get("_flipped", False))
    q["restr"] = [dict(r, o0=round(L - r["o1"], 3), o1=round(L - r["o0"], 3)) for r in p["restr"]]
    q["blocks"] = [dict(x, o=round(L - x["o"], 3)) for x in p.get("blocks", [])]
    return q


def _directed(a, b):
    p, rev = _oriented(a, b)
    if p is None:
        return None
    return _flip(p) if rev else p


def _join(p1, p2):
    L1 = p1["km"]
    u = max(x for x in (p1["umum"], p2["umum"]) if x) if (p1["umum"] or p2["umum"]) else None
    rs = list(p1["restr"]) + [dict(r, o0=round(r["o0"] + L1, 3), o1=round(r["o1"] + L1, 3)) for r in p2["restr"]]
    if p1["umum"] and u and p1["umum"] < u: rs.append(dict(o0=0.0, o1=L1, v=p1["umum"], tags="", note=""))
    if p2["umum"] and u and p2["umum"] < u: rs.append(dict(o0=L1, o1=L1 + p2["km"], v=p2["umum"], tags="", note=""))
    return dict(a=p1["a"], b=p2["b"], km=round(L1 + p2["km"], 3), umum=u, restr=rs, h_a=p1["h_a"], h_b=p2["h_b"],
                up=p1["up"], _flipped=p1.get("_flipped", False), blocks=[], derived="joined")


def _derive(a, b):
    """pair missing on the sheet: join through one sheet station, or split a sheet pair at a station the sheet
    does not show (proportions from gapeka.db distances, only if they add up to the sheet length)"""
    nbrs = {k.split("-")[1] for k in PROF if k.startswith(a + "-")} | {k.split("-")[0] for k in PROF if k.endswith("-" + a)}
    for x in nbrs:                                               # a - x - b on the sheet
        p1, p2 = _directed(a, x), _directed(x, b)
        if p1 is not None and p2 is not None and p1["_flipped"] if "_flipped" in p1 else False:
            pass
        if p1 is not None and p2 is not None:
            return _join(p1, p2)                              # sheet wins over gapeka.db (e.g. Cakung-Rawabebek-Kranji)
    for x in nbrs:                                               # sheet pair a - x with b in between
        pax = _directed(a, x)
        dab, dbx = _dbkm(a, b), _dbkm(b, x)
        if pax is None or not dab or not dbx:
            continue
        if abs(dab + dbx - pax["km"]) / pax["km"] < 0.15:
            f = dab / (dab + dbx)
            L = pax["km"] * f
            rs = [dict(r, o1=round(min(r["o1"], L), 3)) for r in pax["restr"] if r["o0"] < L]
            return dict(pax, b=b, km=round(L, 3), restr=rs, blocks=[x_ for x_ in pax.get("blocks", []) if x_["o"] < L], derived="split")
    nbrs_b = {k.split("-")[1] for k in PROF if k.startswith(b + "-")} | {k.split("-")[0] for k in PROF if k.endswith("-" + b)}
    for x in nbrs_b:                                             # sheet pair x - b with a in between
        pxb = _directed(x, b)
        dxa, dab = _dbkm(x, a), _dbkm(a, b)
        if pxb is None or not dxa or not dab:
            continue
        if abs(dxa + dab - pxb["km"]) / pxb["km"] < 0.15:
            f = dxa / (dxa + dab)
            L0 = pxb["km"] * f
            rs = [dict(r, o0=round(max(r["o0"] - L0, 0), 3), o1=round(r["o1"] - L0, 3)) for r in pxb["restr"] if r["o1"] > L0]
            return dict(pxb, a=a, km=round(pxb["km"] - L0, 3), restr=rs,
                        blocks=[dict(x_, o=round(x_["o"] - L0, 3)) for x_ in pxb.get("blocks", []) if x_["o"] > L0], derived="split")
    # split by the ratio of the shortest timetable running times when gapeka.db has no distances (Tambak-Ijo-Gombong)
    for x in nbrs:
        pax = _directed(a, x)
        if pax is None: continue
        t1, t2 = _tt(a, b), _tt(b, x)
        if t1 and t2:
            L = pax["km"] * t1 / (t1 + t2)
            rs = [dict(r, o1=round(min(r["o1"], L), 3)) for r in pax["restr"] if r["o0"] < L]
            return dict(pax, b=b, km=round(L, 3), restr=rs, blocks=[x_ for x_ in pax.get("blocks", []) if x_["o"] < L], derived="split by running time")
    for x in nbrs_b:
        pxb = _directed(x, b)
        if pxb is None: continue
        t1, t2 = _tt(x, a), _tt(a, b)
        if t1 and t2:
            L0 = pxb["km"] * t1 / (t1 + t2)
            rs = [dict(r, o0=round(max(r["o0"] - L0, 0), 3), o1=round(r["o1"] - L0, 3)) for r in pxb["restr"] if r["o1"] > L0]
            return dict(pxb, a=a, km=round(pxb["km"] - L0, 3), restr=rs,
                        blocks=[dict(x_, o=round(x_["o"] - L0, 3)) for x_ in pxb.get("blocks", []) if x_["o"] > L0], derived="split by running time")
    d = _dbkm(a, b)                                              # last resort: gapeka.db distance, no sheet details
    if d and 0.3 < d < 40:
        return dict(a=a, b=b, km=d, umum=None, restr=[], h_a=None, h_b=None, up=True, blocks=[], derived="gapeka.db distance")
    return None


_TT = None


def _tt(a, b):
    """shortest running time between adjacent timed rows a and b (either direction), seconds"""
    global _TT
    if _TT is None:
        import sqlite3
        _TT = {}
        db = sqlite3.connect("/home/user/Gapeka-parser/gapeka.db")
        prev = {}
        for no, st, kind, ar, dp in db.execute("select train_no,station,kind,arr_s,dep_s from stops order by train_no,seq"):
            t_here = ar if ar is not None else dp
            p = prev.get(no)
            if p is not None and p[1] is not None and t_here is not None:
                T = (t_here - p[1]) % 86400
                if 0 < T < 3600:
                    k = frozenset((p[0], st))
                    _TT[k] = min(_TT.get(k, 1e9), T)
            prev[no] = (st, dp if dp is not None else ar) if (dp is not None or ar is not None) else (st, None)
    return _TT.get(frozenset((a, b)))


def profile(a, b, cls=None):
    """profile of the section a->b in travel direction: (km, umum, [(o0,o1,v,tags,train_goes_up_chainage)], grade)"""
    p, rev = PROF.get(f"{a}-{b}"), False
    if p is None:
        p, rev = PROF.get(f"{b}-{a}"), True
    if p is None:
        p = _derive(a, b)
        if p is None:
            return None
        rev = p.get("_flipped", False)
        if rev:                                     # derived dicts are in travel direction; express in sheet order
            p = _flip(p)
    L = p["km"]
    down = not rev                    # pairs are stored in sheet order (top -> bottom): moving down the sheet = hulu track
    rs = []
    for r in p["restr"]:
        kind = track_kind(r.get("note", ""))
        if kind == "siding":
            continue                  # restriction on named station tracks (loops/sidings) only, not the main line
        if kind == "platform-track" and cls not in (None, "emu"):
            continue                  # "Dari/Ke Jalur X..": commuter platform tracks (Manggarai X-XIII, Rangkasbitung V): EMUs only
        o0, o1 = (L - r["o1"], L - r["o0"]) if rev else (r["o0"], r["o1"])
        rs.append((o0, o1, r["v"], r["tags"], down))
    grade = None
    if p["h_a"] is not None and p["h_b"] is not None:
        dh = (p["h_b"] - p["h_a"]) * (-1 if rev else 1)
        grade = dh / (L * 1000)
    return L, p["umum"], rs, grade


def build(db, tag_rule, vmax_rule="after"):
    """legs between timed points whose every section has a profile"""
    svc = dict(db.execute("select no,service from trains"))
    noted = {(r[0], r[1]) for r in db.execute("select train_no,station from notes")}
    legs, total = [], 0
    for no, service in svc.items():
        rows = db.execute("select seq,station,kind,arr_s,dep_s,vmax from stops where train_no=? order by seq", (no,)).fetchall()
        timed = [i for i, r in enumerate(rows) if r[2] in ("stop", "origin_or_end") or (r[2] == "pass" and r[4] is not None)]
        for p, n in zip(timed, timed[1:]):
            A, B = rows[p], rows[n]
            t0 = A[4]
            t1 = B[3] if B[2] in ("stop", "origin_or_end") and B[3] is not None else B[4]
            if t0 is None or t1 is None:
                continue
            total += 1
            T = (t1 - t0) % 86400
            cells_cap, cells_grade, halts, ok, x = [], [], [], True, 0.0
            halt_st = []
            for i in range(p, n):
                pr = profile(rows[i][1], rows[i + 1][1], train_class(service))
                va, vb = rows[i][5], rows[i + 1][5]
                if vmax_rule == "before" and vb:        # limit printed on the row of the station the section leads to
                    v = vb
                elif vmax_rule == "min" and va and vb:
                    v = min(va, vb)
                else:                                   # limit printed on the row of the station the section starts at
                    v = va
                if pr is None or not v:
                    ok = False
                    break
                L, umum, rs, grade = pr
                nc = max(1, int(round(L * 1000 / DX)))
                cap = np.full(nc, float(min(v, umum or v)))
                for o0, o1, rv, tags, down in rs:
                    t = [x for x in re.split(r"[\s\-/]+", tags) if x]
                    applies = tag_rule != "none"
                    if tag_rule == "track" and len(t) == 1:          # only one track restricted
                        applies = t[0].startswith("hu" if down else "hi")
                    if applies:
                        cap[int(o0 * 1000 / DX):max(int(o1 * 1000 / DX), int(o0 * 1000 / DX) + 1)] = np.minimum(
                            cap[int(o0 * 1000 / DX):max(int(o1 * 1000 / DX), int(o0 * 1000 / DX) + 1)], rv)
                cells_cap.append(cap)
                # gradient/curves are represented by the local limits (user); GRADE=1 tests acceleration on grades anyway
                cells_grade.append(np.full(nc, grade if (GRADE and grade is not None) else 0.0))
                x += L
                if i + 1 < n and rows[i + 1][2] == "halt":
                    halts.append(sum(len(c) for c in cells_cap))
                    halt_st.append(rows[i + 1][1])
            if not ok:
                continue
            cap = np.concatenate(cells_cap)
            km = len(cap) * DX / 1000
            if not (0 < T < 4 * 3600) or km / (T / 3600) > cap.max() * 1.1 or km / (T / 3600) < 8:
                continue
            legs.append(dict(train=no, service=service, cls=train_class(service), cap=cap, grade=np.concatenate(cells_grade),
                             halts=halts, start_rest=A[2] != "pass", end_rest=B[2] != "pass", T=T,
                             stations=[r[1] for r in rows[p:n + 1]], noted=(no, A[1]) in noted or (no, B[1]) in noted, note_a=(no, A[1]) in noted, note_b=(no, B[1]) in noted, first=(p == 0), last=(n == len(rows) - 1), halt_st=halt_st,
                             test=zlib.crc32(no.encode()) % 5 == 0))
    return legs, total


def prepare(legs):
    order = sorted(range(len(legs)), key=lambda i: len(legs[i]["cap"]))
    out = []
    for s in range(0, len(order), 300):
        idx = order[s:s + 300]
        Lc = max(len(legs[i]["cap"]) for i in idx)
        cap = np.full((len(idx), Lc), 300.0)
        gr = np.zeros((len(idx), Lc))
        zero = np.zeros((len(idx), Lc + 1), bool)
        valid = np.zeros((len(idx), Lc), bool)
        for r, i in enumerate(idx):
            g = legs[i]
            n = len(g["cap"])
            cap[r, :n] = g["cap"]
            gr[r, :n] = g["grade"]
            valid[r, :n] = True
            zero[r, 0] = g["start_rest"]
            zero[r, n] = g["end_rest"]
            for h in g["halts"]:
                zero[r, min(max(h, 1), n - 1)] = True
        out.append(dict(idx=np.array(idx), cap=cap / 3.6, gr=gr, zero=zero, valid=valid,
                        nh=np.array([len(legs[i]["halts"]) for i in idx], float)))
    return out


def simulate(batches, n, a0, p, b):
    res = np.zeros(n)
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
            acc = np.minimum(a0, p / np.maximum(v[:, j], 1.0)) - G * B["gr"][:, j]
            v2 = np.maximum(v[:, j] ** 2 + 2 * DX * acc, (8 / 3.6) ** 2)      # never below 8 km/h on a climb
            v[:, j + 1] = np.minimum(node[:, j + 1], np.sqrt(v2))
        for j in range(Lc - 1, -1, -1):
            dec = np.maximum(b + G * B["gr"][:, j], 0.05)
            v[:, j] = np.minimum(v[:, j], np.sqrt(v[:, j + 1] ** 2 + 2 * DX * dec))
        t = 2 * DX / np.maximum(v[:, :-1] + v[:, 1:], 1e-3)
        res[B["idx"]] = (t * B["valid"]).sum(1)
    return res


def predict(batches, n, nh, q):
    a0, p, b, m, c, d = q
    return simulate(batches, n, a0, p, b) * (1 + m) + c + d * nh


def fit(legs, start=(0.6, 6.0, 0.6, 0.05, 20, 40), iters=300):
    tr = [g for g in legs if not g["test"] and not g["noted"]]
    if len(tr) < 25:
        return None
    B = prepare(tr)
    y = np.array([g["T"] for g in tr], float)
    nh = np.array([len(g["halts"]) for g in tr], float)

    def loss(q):
        a0, p, b, m, c, d = q
        if not (0.03 <= a0 <= 1.5 and 0.2 <= p <= 40 and 0.05 <= b <= 1.5 and -0.1 <= m <= 0.4 and -60 <= c <= 240 and 0 <= d <= 240):
            return 1e9
        return float(np.mean(np.abs(predict(B, len(tr), nh, q) - y)))
    r = minimize(loss, start, method="Nelder-Mead", options=dict(maxiter=iters, xatol=1e-3, fatol=0.02))
    return r.x, r.fun


def score(legs, q):
    te = [g for g in legs if g["test"]]
    if not te:
        return None
    B = prepare(te)
    pr = predict(B, len(te), np.array([len(g["halts"]) for g in te], float), q)
    y = np.array([g["T"] for g in te], float)
    e = np.round(pr / 60) - np.round(y / 60)
    return dict(mae=float(np.mean(np.abs(pr - y)) / 60), exact=float(np.mean(e == 0)), w1=float(np.mean(np.abs(e) <= 1)), n=len(te))


if __name__ == "__main__":
    db = sqlite3.connect("/home/user/Gapeka-parser/gapeka.db")
    out = {}
    for rule in ("all", "up=hu", "up=hi", "none"):
        legs, total = build(db, rule)
        if rule == "all":
            print(f"legs with full infrastructure profile: {len(legs)} of {total} timed legs; "
                  f"by class {dict((c, sum(g['cls'] == c for g in legs)) for c in ('emu', 'passenger', 'freight'))}")
        res = []
        for cls in ("emu", "passenger", "freight"):
            L = [g for g in legs if g["cls"] == cls]
            q, _ = fit(L)
            s = score(L, q)
            res.append((cls, q, s))
        tot = sum(s["mae"] * s["n"] for _, _, s in res) / sum(s["n"] for _, _, s in res)
        print(f"restriction rule {rule:6s}: overall MAE {tot:.2f}  " + "  ".join(f"{c}: {s['mae']:.2f}/{s['exact']:.0%}" for c, _, s in res))
        out[rule] = res
    pickle.dump(out, open("physics_rules.pkl", "wb"))
