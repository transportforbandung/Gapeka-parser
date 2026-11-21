"""Linear model + learned per-section speed factor (line geometry: gradients, curves, local restrictions)."""
import csv, pickle, sqlite3, sys
from collections import defaultdict
import numpy as np
from scipy.sparse import csr_matrix
from scipy.sparse.linalg import lsqr
from runtime_model import build_legs, lad, metrics
from runtime_model2 import leg_feats, feats
sys.path.insert(0, "/home/user/Gapeka-parser")
import gapeka_pipeline as gp

db = sqlite3.connect("/home/user/Gapeka-parser/gapeka.db")
legs = build_legs(db)
coef = pickle.load(open("runtime_linear.pkl", "rb"))
for g in legs:
    g["base"] = float(np.dot(coef[g["cls"]], leg_feats(g)))

def sections(g, directed):
    out = []
    for k, (d, v) in enumerate(g["secs"]):
        a, b = g["stations"][k], g["stations"][k + 1]
        out.append(((a, b) if directed else tuple(sorted((a, b))), d / v * 3600))
    return out

def fit_factors(train, directed, damp, by_class):
    idx = {}
    rows, cols, vals, y = [], [], [], []
    for r, g in enumerate(train):
        for s, c in sections(g, directed):
            key = (g["cls"], s) if by_class else s
            j = idx.setdefault(key, len(idx))
            rows.append(r); cols.append(j); vals.append(c)
        y.append(g["T"] - g["base"])
    A = csr_matrix((vals, (rows, cols)), shape=(len(train), len(idx)))
    y = np.array(y)
    keep = np.ones(len(y), bool)
    for _ in range(3):                       # trim waits/outliers and refit
        f = lsqr(A[keep], y[keep], damp=damp)[0]
        res = y - A @ f
        keep = np.abs(res) < max(90, 3 * np.median(np.abs(res)) * 1.48)
    return idx, f

def predict(g, idx, f, directed, by_class):
    t = g["base"]
    for s, c in sections(g, directed):
        key = (g["cls"], s) if by_class else s
        if key in idx:
            t += c * f[idx[key]]
    return t

train = [g for g in legs if not g["test"] and not g["noted"]]
test = [g for g in legs if g["test"]]
y = np.array([g["T"] for g in test], float)
base = metrics(np.array([g["base"] for g in test]), y)
print(f"no section factor                      MAE {base['mae_min']:.2f}  exact {base['exact']:.0%}  ±1 {base['within1']:.0%}")
best = None
for directed in (False, True):
    for by_class in (False, True):
        for damp in (1.0, 3.0, 10.0):
            idx, f = fit_factors(train, directed, damp, by_class)
            m = metrics(np.array([predict(g, idx, f, directed, by_class) for g in test]), y)
            print(f"section factor directed={directed!s:5s} per-class={by_class!s:5s} damp={damp:4.1f}  MAE {m['mae_min']:.2f}  exact {m['exact']:.0%}  ±1 {m['within1']:.0%}  sections {len(idx)}")
            if best is None or m["mae_min"] < best[0]:
                best = (m["mae_min"], directed, by_class, damp, idx, f)
_, directed, by_class, damp, idx, f = best
print("best:", directed, by_class, damp)
for cls in ("emu", "passenger", "freight"):
    T = [g for g in test if g["cls"] == cls]
    m = metrics(np.array([predict(g, idx, f, directed, by_class) for g in T]), np.array([g["T"] for g in T], float))
    print(f"   {cls:10s} MAE {m['mae_min']:.2f}  exact {m['exact']:.0%}  ±1 {m['within1']:.0%}  n {m['n']}")
# section factors on the lines of the KAI halts
show = [("PDL","GK"),("GK","CMI"),("BD","CTH"),("CTH","KAC"),("GDB","CMK"),("CMK","RCK"),("RCK","HRP"),("HRP","CCL"),("SPJ","BH"),("BH","KRN"),("KRN","KDN"),("KDN","TRK")]
print("learned factor (extra seconds per second of limit-speed running), both directions:")
for a, b in show:
    vals = []
    for s in ((a, b), (b, a)):
        key = s if directed else tuple(sorted(s))
        key = ("emu", key) if by_class else key
        vals.append(f"{f[idx[key]]:+.2f}" if key in idx else "  - ")
    print(f"   {a}-{b}: {vals[0]} / {vals[1]}")
pickle.dump(dict(idx=idx, f=f, directed=directed, by_class=by_class), open("section_factors.pkl", "wb"))

# KAI halts with section-corrected sub-leg times
dist = gp.make_distance(db)
H = list(csv.DictReader(open("detail.csv")))
G = {(r["train"], r["halt"]): r for r in csv.DictReader(open("graph_halts.csv"))}
res = defaultdict(list)
for h in H:
    chain = G[(h["train"], h["halt"])]["chain"].split("-")
    i = chain.index(h["halt"])
    vm = dict(db.execute("select station,vmax from stops where train_no=?", (h["train"],)).fetchall())
    secs = [(dist(chain[k], chain[k + 1]), vm[chain[k]]) for k in range(len(chain) - 1)]
    if not all(d for d, _ in secs):
        continue
    def sub(lo, hi):
        g = dict(cls="emu", secs=secs[lo:hi], stations=chain[lo:hi + 1])
        g["base"] = float(np.dot(coef["emu"], feats(secs[lo:hi], True, True, [])))
        return predict(g, idx, f, directed, by_class)
    t1, t2 = sub(0, i), sub(i, len(secs))
    share = float(h["T_min"]) * t1 / (t1 + t2)
    region = "bandung" if h["service"].endswith(("Bandung Raya", "Garut")) else "surabaya"
    res[(region, "run time prev->halt")].append((t1 / 60, float(h["kai"])))
    res[(region, "share of min leg time")].append((share, float(h["kai"])))
print("KAI halts with section factors:")
for (region, lab), v in sorted(res.items()):
    p = np.array([x for x, _ in v]); k = np.array([y for _, y in v])
    for sh, nm in ((0, "arrival"), (1, "departure (+1)")):
        e = np.round(p) + sh - k
        print(f"  {region:9s} {lab:24s} {nm:15s} exact {np.mean(e==0):4.0%}  ±1 {np.mean(np.abs(e)<=1):4.0%}  bias {np.mean(p+sh-k):+.2f}")
