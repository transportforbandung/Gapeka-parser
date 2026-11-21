import csv, pickle, sqlite3
from collections import defaultdict
import numpy as np
import physics_model as pm

db = sqlite3.connect("/home/user/Gapeka-parser/gapeka.db")
P = pickle.load(open("physics_services.pkl", "rb"))
svcq = {r[0]: r[3] for r in P["svc"]}
H = list(csv.DictReader(open("detail.csv")))
G = {(r["train"], r["halt"]): r for r in csv.DictReader(open("graph_halts.csv"))}

def subleg(chain, vm):
    caps = []
    for a, b in zip(chain, chain[1:]):
        pr = pm.profile(a, b)
        if pr is None:
            return None
        L, umum, rs, _ = pr
        nc = max(1, int(round(L * 1000 / pm.DX)))
        cap = np.full(nc, float(min(vm[a], umum or vm[a])))
        for o0, o1, rv, tags, down in rs:
            t = tags.split()
            if len(t) == 1 and not t[0].startswith("hu" if down else "hi"):
                continue
            i0 = int(o0 * 1000 / pm.DX); cap[i0:max(int(o1 * 1000 / pm.DX), i0 + 1)] = np.minimum(cap[i0:max(int(o1 * 1000 / pm.DX), i0 + 1)], rv)
        caps.append(cap)
    cap = np.concatenate(caps)
    return dict(cap=cap, grade=np.zeros(len(cap)), halts=[], start_rest=True, end_rest=True)

res = defaultdict(list)
for h in H:
    chain = G[(h["train"], h["halt"])]["chain"].split("-")
    i = chain.index(h["halt"])
    vm = dict(db.execute("select station,vmax from stops where train_no=?", (h["train"],)).fetchall())
    s1, s2 = subleg(chain[:i + 1], vm), subleg(chain[i:], vm)
    if not s1 or not s2:
        continue
    q = svcq.get(h["service"], P["cls"]["emu"])
    t = pm.simulate(pm.prepare([s1, s2]), 2, q[0], q[1], q[2])
    share = float(h["T_min"]) * t[0] / t.sum()
    run = (t[0] * (1 + q[3]) + q[4]) / 60
    region = "bandung" if h["service"].endswith(("Bandung Raya", "Garut")) else "surabaya"
    res[(region, "physics share of min leg time")].append((share, float(h["kai"])))
    res[(region, "physics run time prev->halt")].append((run, float(h["kai"])))
for (region, lab), v in sorted(res.items()):
    p = np.array([x for x, _ in v]); k = np.array([y for _, y in v])
    for sh, nm in ((0, "arrival"), (1, "departure (+1)")):
        e = np.round(p) + sh - k
        print(f"{region:9s} {lab:32s} {nm:15s} exact {np.mean(e==0):4.0%}  ±1 {np.mean(np.abs(e)<=1):4.0%}  bias {np.mean(p+sh-k):+.2f}  n {len(v)}")
