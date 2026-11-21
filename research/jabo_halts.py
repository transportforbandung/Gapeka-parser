"""KAI Jabodetabek KRL halt times vs the model.

For every run of halts h1..hk between two GAPEKA-timed points p (departure) and n (arrival) of a KRL train:
  sub-legs p-h1, h1-h2, ..., hk-n, each from rest to rest, calibrated running time r_i (relearn, type krl)
  front:  arr(h_j) = dep_p + r_1 + ... + r_j + (j-1)*dwell                    (Surabaya pattern)
  split:  the same, scaled so that the chain fills GAPEKA's time p -> n        (Bandung pattern)
KAI prints one time per halt: tested as arrival and as departure (= arrival + dwell), rounded / floored.
Also: actual KAI halt-to-halt times vs the model's sub-leg times (bias per sub-leg).
Error sign: model minus KAI (minus = KAI later).
"""
import os
os.environ.setdefault("RULES", "rules.json")
import csv, re, sqlite3, sys, zlib
from collections import defaultdict
import numpy as np
import relearn as R
import physics_model as pm

db = sqlite3.connect("/home/user/Gapeka-parser/gapeka.db")
svc = dict(db.execute("select no,service from trains"))
c = R.calib["krl"]
STOPS = defaultdict(list)
for no, seq, st, kind, a, d, vm in db.execute("select train_no,seq,station,kind,arr_s,dep_s,vmax from stops order by train_no,seq"):
    STOPS[no].append((st, kind, a, d, vm))

# KAI cells -> (gapeka train, station) -> minutes
KAI = {}
for r in csv.DictReader(open("/home/user/Gapeka-parser/reference/kai_krl_jabodetabek_2026.csv")):
    if r["time"] == "Ls": continue
    h, m = map(int, r["time"].split(":"))
    for no in (r["train"], r["train2"]):
        if no and any(s[0] == r["station"] for s in STOPS.get(no, [])):
            KAI[(no, r["station"])] = h * 60 + m
            break


def subleg(chain, vm, train):
    caps, sec = [], []
    for a, b in zip(chain, chain[1:]):
        pr = pm.profile(a, b, "emu")
        if pr is None: return None
        L, umum, rs, _ = pr
        nc = max(1, int(round(L * 1000 / pm.DX)))
        cap = np.full(nc, float(min(vm[a], umum or vm[a])))
        for o0, o1, rv, tags, down in rs:
            t = [s for s in re.split(r"[\s\-/]+", tags) if s]
            if len(t) == 1 and not t[0].startswith("hu" if down else "hi"): continue
            i0 = int(o0 * 1000 / pm.DX); i1 = max(int(o1 * 1000 / pm.DX), i0 + 1)
            cap[i0:i1] = np.minimum(cap[i0:i1], rv)
        caps.append(cap)
        pp = pm.PROF.get(f"{a}-{b}") or pm.PROF.get(f"{b}-{a}") or {}
        g = pp.get("ruling")
        if g is None and pp.get("h_a") is not None and pp.get("h_b") is not None: g = abs(pp["h_b"] - pp["h_a"]) / L
        sec.append((L, g))
    g = dict(cap=np.concatenate(caps), grade=None, halts=[], start_rest=True, end_rest=True, stations=chain,
             type="krl", service=svc.get(train, ""), first=False, last=False, sec_grade=sec)
    g["grade"] = np.zeros(len(g["cap"]))
    g["cap"] = R.apply_zones(g, R.RULES)
    g["km"] = sum(L for L, _ in sec)
    return g


def calibrated(g, s):
    x = [s, 1.0, 0.0]
    if R.TERM: x += [0.0, 0.0]
    if R.ENDS: x += [1.0, 1.0]
    if R.NOTE_FEATS: x += [0.0] * 6
    if R.GRADE_ALLOW: x += [R._km_band(g, 5, 10), R._km_band(g, 10, 99)]
    return float(np.dot(c, x))


# collect halt chains
chains = []
for no, rows in STOPS.items():
    if not any((no, r[0]) in KAI for r in rows): continue
    timed = [i for i, r in enumerate(rows) if r[2] is not None or r[3] is not None]
    for i0, i1 in zip(timed, timed[1:]):
        mid = rows[i0 + 1:i1]
        if not mid or any(r[1] != "halt" for r in mid): continue
        if not all((no, r[0]) in KAI for r in mid): continue
        p, n = rows[i0], rows[i1]
        dep = p[3] if p[3] is not None else p[2]
        arr = n[2] if n[2] is not None else n[3]
        chains.append(dict(no=no, pts=[r[0] for r in rows[i0:i1 + 1]], dep=dep, arr=arr,
                           kai=[KAI[(no, r[0])] for r in mid], vm={r[0]: r[4] for r in rows}))
print(f"halt chains: {len(chains)}, halts: {sum(len(ch['kai']) for ch in chains)}")

# sub-leg model times (cache identical sub-legs)
cache, legs, idx = {}, [], []
for ch in chains:
    ks = []
    for a, b in zip(ch["pts"], ch["pts"][1:]):
        key = (a, b, ch["vm"][a])
        if key not in cache:
            g = subleg([a, b], ch["vm"], ch["no"])
            cache[key] = len(legs) if g else None
            if g: legs.append(g)
        ks.append(cache[key])
    ch["legs"] = ks
sims = pm.simulate(pm.prepare(legs), len(legs), *R.PHYS[R.phys_group("krl", "")])
RUN = [calibrated(g, s) for g, s in zip(legs, sims)]
chains = [ch for ch in chains if all(k is not None for k in ch["legs"])]
print(f"chains with model data: {len(chains)}")

test = lambda no: zlib.crc32(no.encode()) % 5 == 0


def predict(ch, dwell, method):
    r = np.array([RUN[k] for k in ch["legs"]])
    m = len(ch["kai"])
    if method == "split":
        T = (ch["arr"] - ch["dep"]) - m * dwell
        r = r * T / r.sum()
    arr = ch["dep"] + np.cumsum(r)[:-1] + dwell * np.arange(m)
    return arr                                        # seconds, arrival at each halt


def score(dwell, method, at, rnd, sel=lambda ch: True):
    e = []
    for ch in chains:
        if not sel(ch): continue
        t = predict(ch, dwell, method) + (dwell if at == "departure" else 0)
        mins = np.floor(t / 60) if rnd == "floor" else np.round(t / 60)
        e.extend((mins - np.array(ch["kai"])) % 1440)
    e = (np.array(e) + 720) % 1440 - 720
    return e


print("\nwhich convention fits (all chains; dwell 30 s): exact / ±1 / bias min")
for method in ("front", "split"):
    for at in ("arrival", "departure"):
        for rnd in ("round", "floor"):
            e = score(30, method, at, rnd)
            print(f"  {method:5s} {at:9s} {rnd:5s} exact {np.mean(e == 0):4.0%}  ±1 {np.mean(np.abs(e) <= 1):4.0%}  bias {e.mean():+.2f}")
# dwell fitted on training trains, scored on held-out trains
best = None
for method in ("front", "split"):
    for at in ("arrival", "departure"):
        for rnd in ("round", "floor"):
            for dw in range(0, 61, 5):
                e = score(dw, method, at, rnd, lambda ch: not test(ch["no"]))
                s_ = np.mean(e == 0)
                if best is None or s_ > best[0]: best = (s_, method, at, rnd, dw)
_, M, AT, RND, DW = best
e = score(DW, M, AT, RND, lambda ch: test(ch["no"]))
print(f"\nbest on training trains: {M}, KAI time = {AT}, {RND}, dwell {DW} s;  held-out: exact {np.mean(e == 0):.0%}  "
      f"±1 {np.mean(np.abs(e) <= 1):.0%}  bias {e.mean():+.2f} min  (n {len(e)})")

# per line and per halt
line_of = lambda no: svc.get(no, "")
print("\nper service (held-out + training, best rule):")
by = defaultdict(list)
for ch in chains:
    t = predict(ch, DW, M) + (DW if AT == "departure" else 0)
    mins = np.floor(t / 60) if RND == "floor" else np.round(t / 60)
    ee = (mins - np.array(ch["kai"]) + 720) % 1440 - 720
    for h, x in zip(ch["pts"][1:-1], ee):
        by[line_of(ch["no"])].append(x)
        by[("halt", h)].append(x)
for k, v in sorted(by.items(), key=lambda kv: str(kv[0])):
    if isinstance(k, str):
        v = np.array(v)
        print(f"  {k:34s} n={len(v):5d} exact {np.mean(v == 0):4.0%}  ±1 {np.mean(np.abs(v) <= 1):4.0%}  bias {v.mean():+.2f}")
print("\nworst halts (bias, n >= 20):")
hs = [(np.mean(v), k[1], len(v), np.mean(np.array(v) == 0)) for k, v in by.items() if isinstance(k, tuple) and len(v) >= 20]
for b, h, n, ex in sorted(hs, key=lambda x: -abs(x[0]))[:15]:
    print(f"  {h:5s} n={n:4d} bias {b:+.2f} min  exact {ex:4.0%}")

# actual KAI segment times vs model sub-leg times (whole chain known in KAI)
print("\nKAI halt-to-halt times vs model running time (+dwell): first sub-leg dep_p -> h1 and h_j -> h_j+1")
seg = defaultdict(list)
for ch in chains:
    k = [ch["dep"] / 60] + ch["kai"]
    for j in range(len(ch["kai"])):
        dt = ((k[j + 1] - k[j]) % 1440) * 60
        model = RUN[ch["legs"][j]] + (0 if j == 0 else DW)
        seg["first (from timed station)" if j == 0 else "between halts"].append(model - dt)
for k, v in seg.items():
    v = np.array(v)
    print(f"  {k:28s} n={len(v):5d}  model - KAI: mean {v.mean():+5.1f} s, median {np.median(v):+5.1f} s, |e|<=30 s {np.mean(np.abs(v) <= 30):4.0%}")

# ---- per halt offsets (halt, previous point, next point): learned on training trains (median residual in s)
print("\nper-halt offset (halt + direction), learned on training trains, scored on held-out trains")
res = defaultdict(list)
for ch in chains:
    if test(ch["no"]): continue
    t = predict(ch, DW, M)
    for j, h in enumerate(ch["pts"][1:-1]):
        res[(h, ch["pts"][j], ch["pts"][j + 2])].append(ch["kai"][j] * 60 + 30 - t[j])   # KAI minute centre - model
OFF = {k: np.median(v) for k, v in res.items() if len(v) >= 3}
for shrink in (0.0, 0.5, 1.0):
    e = []
    for ch in chains:
        if not test(ch["no"]): continue
        t = predict(ch, DW, M)
        for j, h in enumerate(ch["pts"][1:-1]):
            o = OFF.get((h, ch["pts"][j], ch["pts"][j + 2]), 0.0) * shrink
            mins = np.floor((t[j] + o) / 60) if shrink else np.round(t[j] / 60)
            e.append((mins - ch["kai"][j] + 720) % 1440 - 720)
    e = np.array(e)
    print(f"  offset x {shrink:.1f}: exact {np.mean(e == 0):4.0%}  ±1 {np.mean(np.abs(e) <= 1):4.0%}  (n {len(e)})")
