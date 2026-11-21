"""Error bands (<0.5, 0.5-1, >1 min) for: group params, per-service params, + learned section correction; worst-case analysis."""
import pickle, sqlite3, zlib
from collections import Counter, defaultdict
import numpy as np
from scipy.sparse import csr_matrix
from scipy.sparse.linalg import lsqr
import physics_model as pm

KRL = {"Commuter Line Rangkasbitung", "Commuter Line Cikarang", "Commuter Line Bogor", "Commuter Line Yogyakarta",
       "Commuter Line Tangerang", "Commuter Line Tanjungpriuk", "Commuter Line BST"}
def group(g):
    if g["service"] in KRL: return "krl"
    return "freight" if g["cls"] == "freight" else "loco/krd"

db = sqlite3.connect("/home/user/Gapeka-parser/gapeka.db")
legs, total = pm.build(db, "track")
for g in legs: g["grp"] = group(g)
print(f"legs with profile: {len(legs)} of {total}  " + str(Counter(g['grp'] for g in legs)))

def bands(err_s):
    a = np.abs(np.asarray(err_s)) / 60
    return f"<0.5: {np.mean(a < .5):4.0%}   0.5-1: {np.mean((a >= .5) & (a <= 1)):4.0%}   >1: {np.mean(a > 1):4.0%}   (n {len(a)})"

# A: one parameter set per rolling-stock group
gq = {}
for gr in ("krl", "loco/krd", "freight"):
    gq[gr] = pm.fit([g for g in legs if g["grp"] == gr])[0]
    print(gr, "params a0 %.2f power %.2f brake %.2f margin %+.3f const %.0f dwell %.0f" % tuple(gq[gr]))
# B: per service when enough data
cnt = Counter(g["service"] for g in legs if not g["test"] and not g["noted"])
sq = {}
for sv, n in cnt.items():
    if n >= 60:
        L = [g for g in legs if g["service"] == sv]
        sq[sv] = pm.fit(L, start=tuple(gq[L[0]["grp"]]), iters=200)[0]
def params(g, per_service): return sq.get(g["service"], gq[g["grp"]]) if per_service else gq[g["grp"]]

def predict_all(L, per_service):
    out = np.zeros(len(L))
    by = defaultdict(list)
    for i, g in enumerate(L): by[tuple(params(g, per_service))].append(i)
    for q, idx in by.items():
        sub = [L[i] for i in idx]
        p = pm.predict(pm.prepare(sub), len(sub), np.array([len(g["halts"]) for g in sub], float), q)
        out[idx] = p
    return out

train = [g for g in legs if not g["test"] and not g["noted"]]
test = [g for g in legs if g["test"]]
y_te = np.array([g["T"] for g in test], float)
y_tr = np.array([g["T"] for g in train], float)
res = {}
for lab, ps in (("A group params", False), ("B per-service params", True)):
    p_te = predict_all(test, ps); res[lab] = p_te
    print(f"{lab:34s} {bands(p_te - y_te)}")
# C: + per-section correction (seconds per second-at-limit, per group and directed pair), learned on training residuals
p_tr = predict_all(train, True)
def sec_feats(g):
    out, pos = defaultdict(float), 0
    st = g["stations"]
    cells = g["cap"]
    # cells were concatenated section by section; recover boundaries from profile lengths
    for a, b in zip(st, st[1:]):
        L = pm.profile(a, b)[0]; nc = max(1, int(round(L * 1000 / pm.DX)))
        out[(g["grp"], a, b)] += float(np.sum(pm.DX / (cells[pos:pos + nc] / 3.6))); pos += nc
    return out
idx, rows, cols, vals = {}, [], [], []
for r, g in enumerate(train):
    for k, v in sec_feats(g).items():
        rows.append(r); cols.append(idx.setdefault(k, len(idx))); vals.append(v)
A = csr_matrix((vals, (rows, cols)), shape=(len(train), len(idx)))
yres = y_tr - p_tr
keep = np.ones(len(yres), bool)
for _ in range(3):
    f = lsqr(A[keep], yres[keep], damp=3.0)[0]
    r = yres - A @ f
    keep = np.abs(r) < 90
def corr(g): return sum(v * f[idx[k]] for k, v in sec_feats(g).items() if k in idx)
p_c = res["B per-service params"] + np.array([corr(g) for g in test])
print(f"{'C per-service + section correction':34s} {bands(p_c - y_te)}")
for gr in ("krl", "loco/krd", "freight"):
    m = np.array([g["grp"] == gr for g in test])
    print(f"   {gr:9s} {bands((p_c - y_te)[m])}")
m = np.array([not g["noted"] for g in test])
print(f"{'C, legs without crossing/overtake note':34s} {bands((p_c - y_te)[m])}")
# worst cases
err = (p_c - y_te) / 60
order = np.argsort(-np.abs(err))
print("\nlargest errors (model minus GAPEKA, minutes):")
cause = Counter()
for i in order[:40]:
    g = test[i]
    print(f"  {err[i]:+6.1f}  {g['train']:>6s} {g['service'][:24]:24s} {'-'.join(g['stations'][:1] + ['..'] + g['stations'][-1:]) if len(g['stations']) > 3 else '-'.join(g['stations']):18s} "
          f"T {g['T']/60:5.1f}  km {len(g['cap'])*pm.DX/1000:5.1f}  {'stop' if g['start_rest'] else 'pass'}->{'stop' if g['end_rest'] else 'pass'}  halts {len(g['halts'])}  note {g['noted']}")
big = np.abs(err) > 1
print("\n>1 min cases:", int(big.sum()), " slower than model (GAPEKA longer):", int((err[big] < 0).sum()), " faster:", int((err[big] > 0).sum()),
      " with crossing/overtake note:", int(sum(test[i]['noted'] for i in np.where(big)[0])))
pickle.dump(dict(test=[(g['train'], g['stations'], g['T']) for g in test], pred=p_c, gq=gq, sq=sq), open("bands.pkl", "wb"))
