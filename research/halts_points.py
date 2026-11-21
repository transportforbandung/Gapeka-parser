"""KAI halt times with restriction fixes and points zones (learned) / siding (flat 300 m)."""
import re
import csv, pickle, sqlite3
from collections import defaultdict
import numpy as np
import physics_model as pm

PHYS = {"loco/krd": (0.15, 2.3, 0.15)}
svcq = {r[0]: r[3] for r in pickle.load(open("physics_services.pkl", "rb"))["svc"]}
learned = pickle.load(open("points_learned.pkl", "rb"))
db = sqlite3.connect("/home/user/Gapeka-parser/gapeka.db")
H = list(csv.DictReader(open("detail.csv")))
G = {(r["train"], r["halt"]): r for r in csv.DictReader(open("graph_halts.csv"))}

def subleg(chain, vm, z_start, z_end):
    caps = []
    for a, b in zip(chain, chain[1:]):
        pr = pm.profile(a, b)
        if pr is None: return None
        L, umum, rs, _ = pr
        nc = max(1, int(round(L * 1000 / pm.DX)))
        cap = np.full(nc, float(min(vm[a], umum or vm[a])))
        for o0, o1, rv, tags, down in rs:   # (profile() already drops 'Jalur ...' siding-only restrictions)
            t = [x for x in re.split(r"[\s\-/]+", tags) if x]
            if len(t) == 1 and not t[0].startswith("hu" if down else "hi"): continue
            i0 = int(o0 * 1000 / pm.DX); i1 = max(int(o1 * 1000 / pm.DX), i0 + 1)
            cap[i0:i1] = np.minimum(cap[i0:i1], rv)
        caps.append(cap)
    cap = np.concatenate(caps)
    na, nb = min(int(z_start / pm.DX), len(cap)), min(int(z_end / pm.DX), len(cap))
    if na: cap[:na] = np.minimum(cap[:na], 30.0)
    if nb: cap[-nb:] = np.minimum(cap[-nb:], 30.0)
    return dict(cap=cap, grade=np.zeros(len(cap)), halts=[], start_rest=True, end_rest=True)

kinds = {}
res = defaultdict(list)
for h in H:
    chain = G[(h["train"], h["halt"])]["chain"].split("-")
    i = chain.index(h["halt"])
    rows = db.execute("select station,kind,vmax from stops where train_no=? order by seq", (h["train"],)).fetchall()
    vm = {r[0]: r[2] for r in rows}; kd = {r[0]: r[1] for r in rows}
    nxt_stop = kd[chain[-1]] in ("stop", "origin_or_end")
    region = "bandung" if h["service"].endswith(("Bandung Raya", "Garut")) else "surabaya"
    for zname in ("no zones", "learned points zones", "flat 300 m siding at stops"):
        if zname == "no zones":
            za = zb = 0
        elif zname == "learned points zones":
            za = learned.get(("dep", chain[0], "stop", "loco/krd"), 0)
            zb = learned.get(("arr", chain[-1], "stop" if nxt_stop else "pass", "loco/krd"), 0)
        else:
            za, zb = 300, (300 if nxt_stop else 0)
        s1, s2 = subleg(chain[:i + 1], vm, za, 0), subleg(chain[i:], vm, 0, zb)
        if not s1 or not s2: break
        for pname in ("per-service accel/brake", "fixed group accel/brake"):
            q = svcq.get(h["service"]) if pname.startswith("per") else None
            a0, p, b = (q[0], q[1], q[2]) if q is not None else PHYS["loco/krd"]
            t = pm.simulate(pm.prepare([s1, s2]), 2, a0, p, b)
            share = float(h["T_min"]) * t[0] / t.sum()
            res[(region, pname, zname)].append((share, float(h["kai"]), h["halt"], h["prev"]))
            if region == "bandung" and pname.startswith("fixed") and h["train"] in ("390", "391"):
                kinds[(h["halt"], h["prev"], zname)] = (round(share, 2), float(h["kai"]), za, zb)
for (region, pname, zname), v in sorted(res.items()):
    p = np.array([x[0] for x in v]); k = np.array([x[1] for x in v])
    for sh, nm in ((0, "arrival"), (1, "departure+1")):
        if (region == "bandung") != (sh == 0): continue      # Bandung publishes arrival, Surabaya departure (earlier finding)
        e = np.round(p) + sh - k
        a = np.abs(p + sh - k)
        print(f"{region:9s} {pname:24s} {zname:28s} exact {np.mean(e == 0):4.0%}  ±1 {np.mean(np.abs(e) <= 1):4.0%}  "
              f"|err|<0.5 {np.mean(a < .5):4.0%}  0.5-1 {np.mean((a >= .5) & (a <= 1)):4.0%}  >1 {np.mean(a > 1):4.0%}  n {len(v)}")
print("\nBandung examples (trains 390/391): model share, KAI, zone at previous-station departure, zone at next-station arrival")
for k, v in sorted(kinds.items()): print("  ", k, v)
