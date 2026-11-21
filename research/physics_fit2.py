"""Per-class and per-service physics fit (track rule), baselines on the same legs, clustering of rolling stock."""
import pickle, sqlite3, sys
import numpy as np
from physics_model import build, fit, score, prepare, predict, DX
from runtime_model import lad

db = sqlite3.connect("/home/user/Gapeka-parser/gapeka.db")
legs, total = build(db, "track")
print(f"legs with profile: {len(legs)} of {total}")

def lin_feats(g, restricted):
    cap = g["cap"] if restricted else np.full(len(g["cap"]), g["cap"].max())
    return [np.sum(DX / (cap / 3.6)), float(g["start_rest"]), float(g["end_rest"]), float(len(g["halts"])), 1.0]

def lin(L, restricted):
    tr = [g for g in L if not g["test"] and not g["noted"]]; te = [g for g in L if g["test"]]
    c = lad(np.array([lin_feats(g, restricted) for g in tr]), np.array([g["T"] for g in tr], float))
    p = np.array([lin_feats(g, restricted) for g in te]) @ c
    y = np.array([g["T"] for g in te], float); e = np.round(p / 60) - np.round(y / 60)
    return dict(mae=np.mean(np.abs(p - y)) / 60, exact=np.mean(e == 0), w1=np.mean(np.abs(e) <= 1))

print(f"{'class':10s} {'linear, no restrictions':>24s} {'linear + restrictions':>24s} {'physics + restrictions':>24s}")
cls_params = {}
for cls in ("emu", "passenger", "freight"):
    L = [g for g in legs if g["cls"] == cls]
    q, _ = fit(L)
    s = score(L, q)
    cls_params[cls] = q
    a, b = lin(L, False), lin(L, True)
    f = lambda m: f"{m['mae']:.2f} / {m['exact']:.0%} / {m['w1']:.0%}"
    print(f"{cls:10s} {f(a):>24s} {f(b):>24s} {f(s):>24s}    (MAE min / exact / ±1)")
    print(f"           a0 {q[0]:.2f} m/s2, power {q[1]:.1f} W/kg, brake {q[2]:.2f} m/s2, margin {q[3]:+.1%}, const {q[4]:.0f} s, halt dwell {q[5]:.0f} s")

# per service
from collections import Counter
cnt = Counter(g["service"] for g in legs if not g["test"] and not g["noted"])
rows = []
for sv, n in cnt.most_common():
    if n < 60:
        continue
    L = [g for g in legs if g["service"] == sv]
    cls = L[0]["cls"]
    r = fit(L, start=tuple(cls_params[cls]), iters=250)
    if r is None:
        continue
    q = r[0]
    s = score(L, q) or dict(mae=np.nan, exact=np.nan)
    stops = np.mean([g["start_rest"] and g["end_rest"] for g in L])
    rows.append((sv, cls, n, q, s, stops))
    print(f"  {sv:30s} {cls:9s} n={n:5d} stop-stop {stops:4.0%}  a0 {q[0]:.2f}  power {q[1]:5.1f}  brake {q[2]:.2f}  margin {q[3]:+.0%}  const {q[4]:4.0f}  dwell {q[5]:4.0f}  test MAE {s['mae']:.2f}", flush=True)
pickle.dump(dict(cls=cls_params, svc=rows), open("physics_services.pkl", "wb"))
