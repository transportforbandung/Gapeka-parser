"""KAI halt times with the latest model (sheet data + derived pairs + overrides + station zones + calibration incl.
gradient allowance). Three ways to place the halt between the previous and next timed station."""
import os
os.environ.setdefault("RULES", "rules.json")
import csv, re, sqlite3
from collections import defaultdict
import numpy as np
import relearn as R
import physics_model as pm

db = sqlite3.connect("/home/user/Gapeka-parser/gapeka.db")
H = list(csv.DictReader(open("detail.csv")))
G = {(r["train"], r["halt"]): r for r in csv.DictReader(open("graph_halts.csv"))}
svc = dict(db.execute("select no,service from trains"))
c = R.calib["commuter"]
ncol = len(c)

def subleg(chain, vm, start_rest, end_rest, train):
    """sub-leg as a leg dict (same structure as physics_model.build) so the rules and calibration apply"""
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
    g = dict(cap=np.concatenate(caps), grade=None, halts=[], start_rest=start_rest, end_rest=end_rest, stations=chain,
             type="commuter", service=svc.get(train, ""), first=False, last=False, sec_grade=sec)
    g["grade"] = np.zeros(len(g["cap"]))
    g["cap"] = R.apply_zones(g, R.RULES)
    return g

def calibrated(g, s):
    """calibrated time of a sub-leg: same columns as relearn's X for the commuter type"""
    x = [s, 1.0, 0.0]                                             # sim, const, halts
    if R.TERM: x += [0.0, 0.0]                                    # last, first
    if R.ENDS: x += [float(g["start_rest"]), float(g["end_rest"])]
    if R.NOTE_FEATS: x += [0.0] * 6
    if R.GRADE_ALLOW: x += [R._km_band(g, 5, 10), R._km_band(g, 10, 99)]
    return float(np.dot(c, x))

res = defaultdict(list)
for h in H:
    chain = G[(h["train"], h["halt"])]["chain"].split("-")
    i = chain.index(h["halt"])
    vm = {r[0]: r[1] for r in db.execute("select station,vmax from stops where train_no=?", (h["train"],))}
    s1 = subleg(chain[:i + 1], vm, True, True, h["train"])
    s2 = subleg(chain[i:], vm, True, True, h["train"])
    if not s1 or not s2: continue
    sims = pm.simulate(pm.prepare([s1, s2]), 2, *R.PHYS[R.phys_group("commuter", svc.get(h["train"], ""))])
    k1, k2 = calibrated(s1, sims[0]), calibrated(s2, sims[1])
    Tmin = float(h["T_min"]) * 60
    region = "bandung" if h["service"].endswith(("Bandung Raya", "Garut")) else "surabaya"
    kai = float(h["kai"])
    res[(region, "A physics split")].append((Tmin * sims[0] / sims.sum() / 60, kai, h["halt"], h["prev"]))
    res[(region, "B calibrated split")].append((Tmin * k1 / (k1 + k2) / 60, kai, h["halt"], h["prev"]))
    res[(region, "C dep + calibrated run")].append((k1 / 60, kai, h["halt"], h["prev"]))
print("\nKAI halt times, latest model (published time = arrival at the halt; minutes after the previous departure)")
for (region, lab), v in sorted(res.items()):
    p = np.array([x[0] for x in v]); k = np.array([x[1] for x in v])
    for sh, nm in ((0, "arrival"), (1, "departure (+1)")):
        e = np.round(p) + sh - k
        print(f"  {region:9s} {lab:24s} as {nm:15s} exact {np.mean(e == 0):4.0%}  ±1 {np.mean(np.abs(e) <= 1):4.0%}  bias {np.mean(p + sh - k):+5.2f} min  n {len(v)}")
for region in ("bandung", "surabaya"):
    best = "B calibrated split"
    print(f"\n  {region}, {best}, per halt (arrival):")
    g = defaultdict(list)
    for p_, k_, hl, pv in res[(region, best)]: g[(hl, pv)].append((p_, k_))
    for key, v in sorted(g.items()):
        p = np.array([x for x, _ in v]); k = np.array([y for _, y in v])
        print(f"     {key[0]:4s} after {key[1]:4s} n={len(v):2d}  model {p.mean():5.2f}  KAI {k.mean():5.2f}  exact {np.mean(np.round(p) == k):4.0%}")
print("\ncombined rules (arrival):")
for region in ("bandung", "surabaya"):
    B = res[(region, "B calibrated split")]; C = res[(region, "C dep + calibrated run")]
    k = np.array([x[1] for x in B]); b = np.array([x[0] for x in B]); cc = np.array([x[0] for x in C])
    for lab, p in (("D earlier of split and run", np.minimum(b, cc)), ("E average of split and run", (b + cc) / 2)):
        e = np.round(p) - k
        print(f"  {region:9s} {lab:28s} exact {np.mean(e == 0):4.0%}  ±1 {np.mean(np.abs(e) <= 1):4.0%}  bias {np.mean(p - k):+5.2f}")
