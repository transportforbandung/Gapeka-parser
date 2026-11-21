"""Relearn the network: isolate errors per directed section and per station end, by train type and stop/pass.

Base: physics (fixed accel/brake per rolling-stock group) + linear calibration (margin, const, halt dwell) per TRAIN TYPE
      (krl / commuter / intercity / freight), so commuter and intercity no longer share corrections.
Corrections (ridge regression on training residuals, seconds):
  section[(a, b, type)]               every traversal of the directed pair a->b
  station[(s, 'dep'|'arr', 'stop'|'pass', type)]   at the first / last timed point of a leg
Writes station_effects.csv, section_effects.csv, relearn_test.csv; prints bands and a diagnosis.
"""
import csv
import json
import os
import sqlite3
from collections import defaultdict

import numpy as np
from scipy.sparse import csr_matrix
from scipy.sparse.linalg import lsqr

import physics_model as pm

KRL = {"Commuter Line Rangkasbitung", "Commuter Line Cikarang", "Commuter Line Bogor", "Commuter Line Yogyakarta",
       "Commuter Line Tangerang", "Commuter Line Tanjungpriuk", "Commuter Line BST"}
PHYS = {"krl": (0.45, 4.2, 0.85), "loco/krd": (0.15, 2.3, 0.15), "freight": (0.08, 1.2, 0.08)}
TYPES = ("krl", "commuter", "intercity", "freight")


def ttype(g):
    s = g["service"]
    if s in KRL:
        return "krl"
    if g["cls"] == "freight":
        return "freight"
    if s.startswith("Commuter") or s in ("Ka Feeder", "Bandara SMO"):
        return "commuter"
    return "intercity"


def phys_group(t):
    return "krl" if t == "krl" else ("freight" if t == "freight" else "loco/krd")


def apply_zones(g, rules):
    """station zones: speed cap for `length_m` on the side of `station` facing `towards` ('*' = any side),
    for the listed train types, for trains that stop / pass / either at that station"""
    cap, st = g["cap"], g["stations"]
    for z in rules:
        if z.get("kind") == "buffer" or g["type"] not in z["types"]:
            continue
        for side in z["sides"]:                                   # 'dep' = leg starts at the station, 'arr' = ends there
            L = z["dep_m"] if side == "dep" else z["arr_m"]
            n = min(int(L / pm.DX), len(cap))
            if not n:
                continue
            if side == "dep":
                at, nb, rest = st[0], st[1], g["start_rest"]
            else:
                at, nb, rest = st[-1], st[-2], g["end_rest"]
            if at != z["station"] or (z["towards"] != "*" and nb != z["towards"]):
                continue
            terminal = g["first"] if side == "dep" else g["last"]          # train starts / ends its trip here
            w = z["when"]
            if w == "stop" and not rest:                                    # any stop (incl. terminal)
                continue
            if w == "pass" and rest:
                continue
            if w == "midstop" and not (rest and not terminal):              # intermediate stop only
                continue
            if w == "terminal" and not (rest and terminal):
                continue
            cap = cap.copy()
            if side == "dep":
                cap[:n] = np.minimum(cap[:n], z["speed"])
            else:
                cap[-n:] = np.minimum(cap[-n:], z["speed"])
    return cap


RULES = json.load(open(os.environ.get("RULES", "rules.json"))) if os.path.exists(os.environ.get("RULES", "rules.json")) else []

db = sqlite3.connect("/home/user/Gapeka-parser/gapeka.db")
names = dict(db.execute("select code,name from stations"))
VMAX_RULE = os.environ.get("VMAX_RULE", "after")
legs, total = pm.build(db, "track", VMAX_RULE)
print("vmax rule:", VMAX_RULE)
for g in legs:
    g["type"] = ttype(g)
    g["cap"] = apply_zones(g, RULES)
print(f"legs with profile: {len(legs)} of {total}")

# ---- physics simulation per rolling-stock group
sim = np.zeros(len(legs))
by = defaultdict(list)
for i, g in enumerate(legs):
    by[phys_group(g["type"])].append(i)
for gr, idx in by.items():
    sub = [legs[i] for i in idx]
    sim[idx] = pm.simulate(pm.prepare(sub), len(sub), *PHYS[gr])

y = np.array([g["T"] for g in legs], float)
nh = np.array([len(g["halts"]) for g in legs], float)
typ = np.array([g["type"] for g in legs])
train = np.array([not g["test"] and not g["noted"] for g in legs])
test = np.array([g["test"] for g in legs])
noted = np.array([g["noted"] for g in legs])


def lad(X, yy):
    c = np.linalg.lstsq(X, yy, rcond=None)[0]
    for _ in range(40):
        w = 1 / np.maximum(np.abs(yy - X @ c), 5.0)
        c = np.linalg.lstsq(X * w[:, None] ** .5, yy * w ** .5, rcond=None)[0]
    return c


first = np.array([float(g["first"]) for g in legs]); last = np.array([float(g["last"]) for g in legs])
TERM = os.environ.get("TERMINAL", "1") == "1"
srest = np.array([float(g["start_rest"]) for g in legs]); erest = np.array([float(g["end_rest"]) for g in legs])
ENDS = os.environ.get("ENDS", "1") == "1"         # separate fixed time for starting from / stopping at a station
X = np.column_stack([sim, np.ones(len(legs)), nh] + ([last, first] if TERM else []) + ([srest, erest] if ENDS else []))
calib = {t: lad(X[train & (typ == t)], y[train & (typ == t)]) for t in TYPES}
base = np.array([X[i] @ calib[typ[i]] for i in range(len(legs))])
# section buffers (timetable padding, not physics): extra seconds per leg that runs over the listed sections
BUFFERS = [z for z in RULES if z.get("kind") == "buffer"]
def buffer_s(g):
    secs = set(zip(g["stations"], g["stations"][1:])) | set(zip(g["stations"][1:], g["stations"]))
    return sum(b["seconds"] for b in BUFFERS if g["type"] in b["types"] and secs & {tuple(p) for p in b["sections"]})
if BUFFERS:
    buf = np.array([buffer_s(g) for g in legs], float)
    # refit calibration with the buffer already added, so calibration is not distorted by the padded legs
    calib = {t: lad(X[train & (typ == t)], (y - buf)[train & (typ == t)]) for t in TYPES}
    base = np.array([X[i] @ calib[typ[i]] for i in range(len(legs))]) + buf
for t in TYPES:
    c = calib[t]
    extra = f"  terminus arrival {c[3]:+4.0f} s  origin departure {c[4]:+4.0f} s" if TERM else ""
    if ENDS:
        extra += f"  start from stop {c[-2]:+4.0f} s  stop at end {c[-1]:+4.0f} s"
    print(f"calibration {t:9s}: margin {c[0] - 1:+.1%}  const {c[1]:4.0f} s  per halt {c[2]:3.0f} s{extra}")

# ---- correction features
def feats(g):
    f = []
    st, t = g["stations"], g["type"]
    for a, b in zip(st, st[1:]):
        f.append(("sec", a, b, t))
    if os.environ.get("HALT_DWELL", "1") == "1":
        for h in g.get("halt_st", []):
            f.append(("halt", h, t))                       # dwell (+ stop/start) at each halt, per halt station and type
    f.append(("dep", st[0], "stop" if g["start_rest"] else "pass", t))
    f.append(("arr", st[-1], "stop" if g["end_rest"] else "pass", t))
    return f


F = [feats(g) for g in legs]
col = {}
rows, cols = [], []
tr_idx = np.where(train)[0]
for r, i in enumerate(tr_idx):
    for k in F[i]:
        rows.append(r)
        cols.append(col.setdefault(k, len(col)))
A = csr_matrix((np.ones(len(rows)), (rows, cols)), shape=(len(tr_idx), len(col)))
res = y[tr_idx] - base[tr_idx]
keep = np.ones(len(res), bool)
for _ in range(3):
    eff = lsqr(A[keep], res[keep], damp=2.0)[0]
    keep = np.abs(res - A @ eff) < 150
support = np.asarray(A[keep].sum(0)).ravel()


def correction(i):
    return sum(eff[col[k]] for k in F[i] if k in col)


pred = base + np.array([correction(i) for i in range(len(legs))])


def bands(e):
    a = np.abs(e) / 60
    return f"<0.5 {np.mean(a < .5):4.0%}  0.5-1 {np.mean((a >= .5) & (a <= 1)):4.0%}  >1 {np.mean(a > 1):4.0%}  (n {len(a)})"


print("\nheld-out trains, legs without crossing/overtake note")
m = test & ~noted
print(f"  physics + calibration only      {bands((base - y)[m])}")
print(f"  + section/station corrections   {bands((pred - y)[m])}")
for t in TYPES:
    mm = m & (typ == t)
    print(f"     {t:9s}                    {bands((pred - y)[mm])}")
print(f"  legs with a note                {bands((pred - y)[test & noted])}")

# ---- outputs
SUFFIX = "" if VMAX_RULE == "after" else "_" + VMAX_RULE
with open(f"station_effects{SUFFIX}.csv", "w", newline="") as f:
    w = csv.writer(f)
    w.writerow(["station", "name", "end", "stop_or_pass", "train_type", "effect_s", "legs"])
    for k, j in col.items():
        if k[0] in ("dep", "arr"):
            w.writerow([k[1], names.get(k[1], ""), k[0], k[2], k[3], round(eff[j]), int(support[j])])
        if k[0] == "halt":
            w.writerow([k[1], names.get(k[1], ""), "halt", "stop", k[2], round(eff[j]), int(support[j])])
with open(f"section_effects{SUFFIX}.csv", "w", newline="") as f:
    w = csv.writer(f)
    w.writerow(["from", "to", "train_type", "effect_s", "legs"])
    for k, j in col.items():
        if k[0] == "sec":
            w.writerow([k[1], k[2], k[3], round(eff[j]), int(support[j])])
with open(f"relearn_test{SUFFIX}.csv", "w", newline="") as f:
    w = csv.writer(f)
    w.writerow(["train", "service", "type", "from", "to", "ends", "note", "T_min", "pred_min", "err_min"])
    for i in np.where(test)[0]:
        g = legs[i]
        w.writerow([g["train"], g["service"], g["type"], g["stations"][0], g["stations"][-1],
                    ("stop" if g["start_rest"] else "pass") + "->" + ("stop" if g["end_rest"] else "pass"),
                    g["noted"], y[i] / 60, round(pred[i] / 60, 2), round((pred[i] - y[i]) / 60, 2)])

# ---- diagnosis: station ends -- which station effects are big, and do they depend on type / stop-pass / side?
print("\nSTATION ENDS: effects >= 45 s with >= 5 legs (seconds; + = GAPEKA slower than physics)")
st_eff = defaultdict(dict)
for k, j in col.items():
    if k[0] in ("dep", "arr") and support[j] >= 5:
        st_eff[k[1]][(k[0], k[2], k[3])] = (eff[j], int(support[j]))
rank = sorted(st_eff, key=lambda s: -max(abs(v[0]) for v in st_eff[s].values()))
for s in rank[:30]:
    big = {k: v for k, v in st_eff[s].items() if abs(v[0]) >= 45}
    if not big:
        break
    allv = st_eff[s]
    cells = "  ".join(f"{e}/{sp}/{t}:{v[0]:+.0f}({v[1]})" for (e, sp, t), v in sorted(allv.items()))
    # classify the pattern
    tags = []
    ts = {k[2] for k in big}
    if len({k[2] for k in allv}) > 1 and len(ts) < len({k[2] for k in allv}):
        tags.append("TYPE-SPECIFIC:" + ",".join(sorted(ts)))
    if {k[1] for k in big} == {"stop"} and any(k[1] == "pass" for k in allv):
        tags.append("STOP-ONLY")
    if {k[1] for k in big} == {"pass"} and any(k[1] == "stop" for k in allv):
        tags.append("PASS-ONLY")
    if len({k[0] for k in big}) == 1 and len({k[0] for k in allv}) == 2:
        tags.append("ONE-SIDE:" + next(iter({k[0] for k in big})))
    print(f"  {s:5s} {names.get(s, ''):18s} {' '.join(tags) or 'GENERAL'}")
    print(f"        {cells}")

# ---- diagnosis: directed sections -- direction asymmetry and type dependence
print("\nSECTIONS: largest effects with >= 5 legs, with the opposite direction alongside (seconds)")
sec = {}
for k, j in col.items():
    if k[0] == "sec" and support[j] >= 5:
        sec[(k[1], k[2], k[3])] = (eff[j], int(support[j]))
for (a, b, t), (e, n) in sorted(sec.items(), key=lambda kv: -abs(kv[1][0]))[:30]:
    if abs(e) < 30:
        break
    opp = sec.get((b, a, t))
    other_types = {tt: sec[(a, b, tt)][0] for tt in TYPES if tt != t and (a, b, tt) in sec}
    print(f"  {a:>5s} -> {b:<5s} {t:9s} {e:+5.0f} s ({n:3d})   opposite: {('%+.0f' % opp[0]) if opp else '  -':>5s}   "
          f"other types: {', '.join(f'{k} {v:+.0f}' for k, v in other_types.items()) or '-'}")

print("\nRULE STATIONS (all legs, no note): mean error s, share |err|<0.5 / >1 min")
_seen = set()
for z in [z for z in RULES if z.get("kind") != "buffer"]:
    if (z["station"], z["towards"]) in _seen:
        continue
    _seen.add((z["station"], z["towards"]))
    for side in z["sides"]:
        for tt in TYPES:
            for case in ("pass", "midstop", "terminal"):
                def ok(g):
                    rest = g["start_rest"] if side == "dep" else g["end_rest"]
                    term = g["first"] if side == "dep" else g["last"]
                    return {"pass": not rest, "midstop": rest and not term, "terminal": rest and term}[case]
                ii = [i for i, g in enumerate(legs) if not g["noted"] and g["type"] == tt and ok(g) and
                      ((side == "dep" and g["stations"][0] == z["station"] and (z["towards"] == "*" or g["stations"][1] == z["towards"])) or
                       (side == "arr" and g["stations"][-1] == z["station"] and (z["towards"] == "*" or g["stations"][-2] == z["towards"])))]
                if len(ii) < 3:
                    continue
                e = (base[ii] - y[ii]) / 60
                print(f"  {z['station']:4s} {side} {z['towards']:4s} {tt:9s} {case:8s} n={len(ii):3d}  physics only: mean {np.mean(e) * 60:+5.0f} s  <0.5 {np.mean(np.abs(e) < .5):4.0%}  >1 {np.mean(np.abs(e) > 1):4.0%}")

print("\nSUKABUMI LINE (PDF page 28 sections), physics only, no note")
sk = {"SI","RI","GDS","CRG","LP","SSI","CBB","CJ","MLB","TPR","SLJ","CRJ","CPY","RM","CPT","PDL"}
for tt in ("intercity", "commuter"):
    for lab, f in (("stop->stop", lambda g: g["start_rest"] and g["end_rest"]), ("other", lambda g: not (g["start_rest"] and g["end_rest"]))):
        ii = [i for i, g in enumerate(legs) if not g["noted"] and g["type"] == tt and set(g["stations"]) <= sk and f(g)]
        if len(ii) >= 3:
            e = (base[ii] - y[ii]) / 60
            print(f"  {tt:9s} {lab:10s} n={len(ii):3d}  mean {np.mean(e) * 60:+5.0f} s  <0.5 {np.mean(np.abs(e) < .5):4.0%}  >1 {np.mean(np.abs(e) > 1):4.0%}")

print("\nNAMBO BRANCH KRL (physics only, no note)")
ii = [i for i, g in enumerate(legs) if g["type"] == "krl" and not g["noted"] and set(g["stations"]) & {"CBN", "NMO", "GPI", "PDRG"}]
if ii:
    e = (base[ii] - y[ii]) / 60
    print(f"  n={len(ii)}  mean {np.mean(e) * 60:+5.0f} s  <0.5 {np.mean(np.abs(e) < .5):4.0%}  >1 {np.mean(np.abs(e) > 1):4.0%}")
