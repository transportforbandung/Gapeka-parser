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


def track_kind(note):
    """classify the small note in a restriction cell"""
    n = note or ""
    if re.search(r"\b(dari|ke)\b[ /a-z]*jalur", n, re.I):
        return "platform-track"
    if re.search(r"jalur", n, re.I) or re.search(r"(^|\s)[IVX]{1,4},", n):
        return "siding"
    return "line"


def profile(a, b, cls=None):
    """profile of the section a->b in travel direction: (km, umum, [(o0,o1,v,tags,train_goes_up_chainage)], grade)"""
    p, rev = PROF.get(f"{a}-{b}"), False
    if p is None:
        p, rev = PROF.get(f"{b}-{a}"), True
    if p is None:
        return None
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
