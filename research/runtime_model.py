"""Learn a running-time model from the whole GAPEKA.

Every leg between two timed points (timed stop, origin/end, or a pass row with a printed time) is a sample:
  inputs : distance of every section, speed limit of every section (vmax printed on the row where the section starts),
           start at rest (stop) or at speed (pass), end at rest or at speed, untimed halts inside the leg
  target : printed running time
Models (all fitted per train class, scored on held-out trains):
  M1 km           T = a*km + b
  M2 km/vmax      T = c1*sum(km_i/v_i) + c2*[start at rest] + c3*[end at rest] + c4*n_halts + c0       (linear)
  M3 kinematic    simulate: accelerate a, brake b, cruise at k*limit, brake for lower limits, stop at halts,
                  dwell d per halt; T = sim*(1+m)                                                     (5 params)
"""
import pickle
import sqlite3
import sys
import zlib
from collections import defaultdict

import numpy as np
from scipy.optimize import minimize

sys.path.insert(0, "/home/user/Gapeka-parser")
import gapeka_pipeline as gp  # noqa: E402

DX = 100.0  # metres per simulation step


def train_class(service):
    s = service.lower()
    if s.startswith("commuter") or s in ("ka feeder", "bandara smo"):
        return "emu"
    if any(k in s for k in ("cargo", "tanker", "service", "trans", "kalmas", "ketandan")):
        return "freight"
    return "passenger"


def build_legs(db):
    dist = gp.make_distance(db)
    svc = dict(db.execute("select no,service from trains"))
    noted = {(r[0], r[1]) for r in db.execute("select train_no,station from notes")}
    legs = []
    for no, service in svc.items():
        rows = db.execute("select seq,station,kind,arr_s,dep_s,vmax from stops where train_no=? order by seq", (no,)).fetchall()
        timed = [i for i, r in enumerate(rows) if r[2] in ("stop", "origin_or_end") or (r[2] == "pass" and r[4] is not None)]
        for p, n in zip(timed, timed[1:]):
            A, B = rows[p], rows[n]
            t0 = A[4]
            t1 = B[3] if B[2] in ("stop", "origin_or_end") and B[3] is not None else B[4]
            if t0 is None or t1 is None:
                continue
            T = t1 - t0
            if T < 0:
                T += 86400
            secs = []
            ok = True
            for i in range(p, n):
                d = dist(rows[i][1], rows[i + 1][1])
                v = rows[i][5]
                if not d or not v:
                    ok = False
                    break
                secs.append((d, v))
            if not ok or not (0 < T < 4 * 3600):
                continue
            km = sum(d for d, _ in secs)
            vmx = max(v for _, v in secs)
            avg = km / (T / 3600)
            if not (10 <= avg <= vmx * 1.1):
                continue                      # distance contradicts timetable (bad link) or huge wait
            halts = []                        # index of section *after which* the train halts (position = cum km)
            cum = 0.0
            for i in range(p + 1, n):
                cum += secs[i - p - 1][0]
                if rows[i][2] == "halt":
                    halts.append(cum)
            legs.append(dict(train=no, service=service, cls=train_class(service), stations=[r[1] for r in rows[p:n + 1]],
                             secs=secs, km=km, T=T, start_rest=A[2] != "pass", end_rest=B[2] != "pass", halts=halts,
                             noted=(no, A[1]) in noted or (no, B[1]) in noted,
                             test=zlib.crc32(no.encode()) % 5 == 0))
    return legs


# ---------------------------------------------------------------- kinematic simulator (vectorised over legs)
def prepare(legs):
    """discretise legs into node arrays, grouped in batches of similar length"""
    order = sorted(range(len(legs)), key=lambda i: legs[i]["km"])
    batches = []
    for s in range(0, len(order), 400):
        idx = order[s:s + 400]
        nn = [max(2, int(round(legs[i]["km"] * 1000 / DX)) + 1) for i in idx]
        L = max(nn)
        lim = np.full((len(idx), L - 1), 300.0)      # cell speed limit km/h (padding: unlimited)
        zero = np.zeros((len(idx), L), bool)         # nodes where speed must be 0
        valid = np.zeros((len(idx), L - 1), bool)
        sp_start = np.zeros(len(idx), bool)
        sp_end_node = np.array(nn) - 1
        for r, i in enumerate(idx):
            g = legs[i]
            n = nn[r]
            scale = (n - 1) * DX / (g["km"] * 1000)   # stretch so that the leg has whole cells
            x = 0.0
            for d, v in g["secs"]:
                a = int(round(x * 1000 / DX / scale))
                x += d
                b = int(round(x * 1000 / DX / scale))
                lim[r, a:max(b, a + 1)] = v
            valid[r, :n - 1] = True
            if g["start_rest"]:
                zero[r, 0] = True
            else:
                sp_start[r] = True
            if g["end_rest"]:
                zero[r, n - 1] = True
            for h in g["halts"]:
                zero[r, min(n - 2, max(1, int(round(h * 1000 / DX / scale))))] = True
        batches.append(dict(idx=np.array(idx), lim=lim, zero=zero, valid=valid, nn=np.array(nn),
                            dx=np.array([legs[i]["km"] * 1000 / (nn[r] - 1) for r, i in enumerate(idx)]),
                            nh=np.array([len(legs[i]["halts"]) for i in idx])))
    return batches


def simulate(batches, n, acc, brk, k):
    """minimum running time (s) of every leg at accel acc, brake brk (m/s2), cruising at k * limit"""
    out = np.zeros(n)
    for B in batches:
        lim = B["lim"] * k / 3.6
        cap = np.minimum(np.concatenate([lim[:, :1], lim], 1), np.concatenate([lim, lim[:, -1:]], 1))
        cap[B["zero"]] = 0.0
        dx = B["dx"][:, None]
        N, L = cap.shape
        v = np.empty((N, L))
        v[:, 0] = cap[:, 0]
        a2 = 2 * acc * B["dx"]
        b2 = 2 * brk * B["dx"]
        for j in range(1, L):
            v[:, j] = np.minimum(cap[:, j], np.sqrt(v[:, j - 1] ** 2 + a2))
        for j in range(L - 2, -1, -1):
            v[:, j] = np.minimum(v[:, j], np.sqrt(v[:, j + 1] ** 2 + b2))
        t = 2 * dx / np.maximum(v[:, :-1] + v[:, 1:], 1e-3)
        out[B["idx"]] = (t * B["valid"]).sum(1)
    return out


def kin_predict(batches, n, nh, p):
    acc, brk, k, m, d = p
    return simulate(batches, n, acc, brk, k) * (1 + m) + d * nh


# ---------------------------------------------------------------- linear models
def feats_km(g):
    return [g["km"], 1.0]


def feats_vmax(g):
    return [sum(d / v for d, v in g["secs"]) * 3600, float(g["start_rest"]), float(g["end_rest"]), float(len(g["halts"])), 1.0]


def lad(X, y):
    """least absolute deviation via IRLS"""
    w = np.ones(len(y))
    c = np.linalg.lstsq(X, y, rcond=None)[0]
    for _ in range(50):
        r = np.abs(y - X @ c)
        w = 1 / np.maximum(r, 5.0)
        c = np.linalg.lstsq(X * w[:, None] ** .5, y * w ** .5, rcond=None)[0]
    return c


def metrics(pred, y):
    e = (np.round(pred / 60) - np.round(y / 60))
    return dict(mae_min=float(np.mean(np.abs(pred - y)) / 60), exact=float(np.mean(e == 0)), within1=float(np.mean(np.abs(e) <= 1)),
                median_min=float(np.median(np.abs(pred - y)) / 60), n=len(y))


def main():
    db = sqlite3.connect("/home/user/Gapeka-parser/gapeka.db")
    legs = build_legs(db)
    print(f"legs usable: {len(legs)}")
    res = {}
    for cls in ("emu", "passenger", "freight"):
        G = [g for g in legs if g["cls"] == cls]
        tr = [g for g in G if not g["test"] and not g["noted"]]
        te = [g for g in G if g["test"]]
        y_tr = np.array([g["T"] for g in tr], float)
        y_te = np.array([g["T"] for g in te], float)
        print(f"\n=== {cls}: train {len(tr)} legs, test {len(te)} legs "
              f"(stop-stop {sum(g['start_rest'] and g['end_rest'] for g in G)}, pass-pass {sum(not g['start_rest'] and not g['end_rest'] for g in G)}, "
              f"with halts {sum(bool(g['halts']) for g in G)})")
        out = {}
        c1 = lad(np.array([feats_km(g) for g in tr]), y_tr)
        out["M1 km only"] = (np.array([feats_km(g) for g in te]) @ c1, c1)
        c2 = lad(np.array([feats_vmax(g) for g in tr]), y_tr)
        out["M2 km/vmax + stop flags"] = (np.array([feats_vmax(g) for g in te]) @ c2, c2)
        btr, bte = prepare(tr), prepare(te)
        nh_tr = np.array([len(g["halts"]) for g in tr], float)
        nh_te = np.array([len(g["halts"]) for g in te], float)

        def loss(q):
            acc, brk, k, m, d = q
            if not (0.05 <= acc <= 1.5 and 0.05 <= brk <= 1.5 and 0.5 <= k <= 1.05 and -0.1 <= m <= 0.5 and 0 <= d <= 300):
                return 1e9
            return float(np.mean(np.abs(kin_predict(btr, len(tr), nh_tr, q) - y_tr)))
        start = {"emu": [0.5, 0.5, 0.95, 0.05, 40], "passenger": [0.3, 0.4, 0.95, 0.05, 60], "freight": [0.15, 0.3, 0.9, 0.05, 60]}[cls]
        best = minimize(loss, start, method="Nelder-Mead", options=dict(maxiter=400, xatol=1e-3, fatol=0.05))
        p = best.x
        out["M3 kinematic"] = (kin_predict(bte, len(te), nh_te, p), p)
        # ablations of the kinematic model (refit nothing, switch one effect off)
        sim_flat = np.array([g["km"] * 1000 / (max(v for _, v in g["secs"]) * p[2] / 3.6) for g in te]) * (1 + p[3]) + p[4] * nh_te
        out["  ablation: one speed (train max) for whole leg"] = (sim_flat, None)
        for name, pr in out.items():
            mt = metrics(pr[0], y_te)
            print(f"  {name:48s} MAE {mt['mae_min']:.2f} min  median {mt['median_min']:.2f}  exact-minute {mt['exact']:.0%}  ±1 min {mt['within1']:.0%}")
        print(f"  M1: {c1[0]:.1f} s/km + {c1[1]:.0f} s")
        print(f"  M2: {c2[0]:.3f} x (km/vmax time)  start-at-rest +{c2[1]:.0f} s  end-at-rest +{c2[2]:.0f} s  per halt +{c2[3]:.0f} s  const {c2[4]:.0f} s")
        print(f"  M3: accel {p[0]:.2f} m/s2  brake {p[1]:.2f} m/s2  cruise {p[2]:.0%} of limit  recovery margin {p[3]:+.1%}  halt dwell {p[4]:.0f} s")
        # breakdown by leg type
        for lab, f in (("stop->stop", lambda g: g["start_rest"] and g["end_rest"]), ("pass->pass", lambda g: not g["start_rest"] and not g["end_rest"]),
                       ("mixed", lambda g: g["start_rest"] != g["end_rest"]), ("with halts", lambda g: bool(g["halts"]))):
            m = np.array([f(g) for g in te])
            if m.sum() >= 10:
                a1, a3 = metrics(out["M1 km only"][0][m], y_te[m]), metrics(out["M3 kinematic"][0][m], y_te[m])
                print(f"    {lab:11s} n={m.sum():5d}   M1 MAE {a1['mae_min']:.2f} exact {a1['exact']:.0%}   M3 MAE {a3['mae_min']:.2f} exact {a3['exact']:.0%}")
        res[cls] = dict(p=p, c1=c1, c2=c2)
    pickle.dump(res, open("runtime_params.pkl", "wb"))


if __name__ == "__main__":
    main()
