import os
os.environ.setdefault("RULES", "rules.json"); os.environ.setdefault("HALT_DWELL", "1")
import numpy as np
from collections import defaultdict
import relearn as Rl
exec(open("waits2.py").read().split("# 1) crossing where both stop")[0])
legs, y, pred = Rl.legs, Rl.y, Rl.pred
leg_by = defaultdict(list)
for i, g in enumerate(legs):
    leg_by[(g["train"], g["stations"][0], "start")].append(i)
    leg_by[(g["train"], g["stations"][-1], "end")].append(i)

def corr(x, r):
    x, r = np.array(x), np.array(r)
    return np.corrcoef(x, r)[0, 1] if len(x) > 3 else float("nan")
# H1: leg INTO the waiting station; margin = other train's reference time - waiter's arrival
H1 = defaultdict(lambda: ([], []))
H2 = defaultdict(lambda: ([], []))
for (s, w), evs in groups.items():
    W = at(w, s)
    for kind, ov in evs:
        c = ref_times(kind, s, w, ov)
        ref = c.get("other arrives/passes") if kind == "cross" else c.get("overtaker passes station")
        if ref is None: continue
        for i in leg_by.get((w, s, "end"), []):
            margin = wrap(ref - W[3]) / 60
            H1[kind][0].append(margin); H1[kind][1].append((y[i] - pred[i]) / 60)
        if kind != "overtake": continue
        # H2: leg OUT of the waiting station; d = waiter's arrival at next station - overtaker's time at that station
        for i in leg_by.get((w, s, "start"), []):
            nst = legs[i]["stations"][-1]
            q = at(ov, nst)
            if q is None or t_at(q) is None: continue
            tq = q[4] if q[4] is not None else q[3]                 # overtaker departs / passes the next station
            Wn = at(w, nst)
            wa = Wn[3] if Wn[3] is not None else Wn[4]
            phys_arr = W[4] + pred[i]                               # model arrival if it simply ran its normal time
            H2["all"][0].append(wrap(phys_arr - tq) / 60); H2["all"][1].append((y[i] - pred[i]) / 60)
print("\nH1: leg INTO the waiting station.  residual = GAPEKA - model (+ = GAPEKA slower)")
for k, (m, r) in H1.items():
    m, r = np.array(m), np.array(r)
    print(f"  {k:9s} n={len(r):4d}  residual mean {r.mean():+5.2f} min  |r|>1 {np.mean(np.abs(r) > 1):4.0%}  corr(margin, residual) {corr(m, r):+.2f}")
    for lo, hi in ((-99, 0), (0, 2), (2, 5), (5, 15), (15, 999)):
        sel = (m >= lo) & (m < hi)
        if sel.sum() >= 5:
            print(f"      margin {lo:>4}..{hi:<4} min: n={sel.sum():4d}  residual mean {r[sel].mean():+5.2f}  |r|>1 {np.mean(np.abs(r[sel]) > 1):4.0%}")
print("\nH2: leg OUT of the station after being overtaken.  x = model arrival at next station - overtaker leaving that station")
m, r = map(np.array, H2["all"])
print(f"  n={len(r)}  residual mean {r.mean():+5.2f}  |r|>1 {np.mean(np.abs(r) > 1):4.0%}  corr(x, residual) {corr(m, r):+.2f}")
for lo, hi in ((-99, -2), (-2, 0), (0, 2), (2, 5), (5, 999)):
    sel = (m >= lo) & (m < hi)
    if sel.sum() >= 5:
        print(f"      x {lo:>4}..{hi:<4} min: n={sel.sum():4d}  residual mean {r[sel].mean():+5.2f}  |r|>1 {np.mean(np.abs(r[sel]) > 1):4.0%}")
