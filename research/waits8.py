"""Double track: is the waiting train's departure set by the NEXT train behind it (gap ahead) rather than the overtaker?"""
from collections import defaultdict
import numpy as np
exec(open("waits4.py").read().split("res = defaultdict")[0])
DOUBLE = {"Cikampek-Cirebon-Tegal, Prupuk", "Tegal-Semarang-Cepu, Gundih", "Jakartakota-Jatinegara-Bekasi, Tanjungpriuk", "Kutoarjo-Yogyakarta-Solo"}
rec = defaultdict(list)
for (s, w), evs in groups.items():
    if not any(k == "overtake" for k, _ in evs): continue
    ln = line(s)
    W = at(w, s)
    iw = next(i for i, r in enumerate(rows[w]) if r[0] == W[0])
    if iw + 1 >= len(rows[w]): continue
    n = rows[w][iw + 1][1]
    after_dep, before_dep = [], []
    for no, i, r in by_station[s]:
        if no == w: continue
        nx = [q[1] for q in rows[no][i + 1:i + 3]]
        if n not in nx: continue                                     # same direction
        ts = r[4] if r[4] is not None else r[3]
        if ts is None: continue
        d = wrap(ts - W[4]) / 60
        (after_dep if d > 0 else before_dep).append(d)
    if not after_dep or not before_dep: continue
    ahead = min(after_dep)                    # next same-direction train passing s after the waiter leaves
    behind = -max(before_dep)                 # last same-direction train that passed before it leaves
    rec["double" if ln in DOUBLE else "single"].append((behind, ahead, ttype(w)))
for k, v in rec.items():
    b = np.array([x[0] for x in v]); a = np.array([x[1] for x in v])
    print(f"{k:6s} n={len(v):4d}  minutes since the last train passed: median {np.median(b):4.1f} (IQR {np.percentile(b, 25):.1f}-{np.percentile(b, 75):.1f})"
          f"   minutes until the next train passes: median {np.median(a):4.1f} (IQR {np.percentile(a, 25):.1f}-{np.percentile(a, 75):.1f})")
    for lo in (0, 3, 4, 5, 6, 8):
        print(f"        share leaving at least {lo} min before the next train: {np.mean(a >= lo):4.0%}", end="")
    print()
    for tt in ("freight", "intercity", "commuter", "krl"):
        sel = np.array([x[2] == tt for x in v])
        if sel.sum() >= 8:
            print(f"        {tt:9s} n={sel.sum():3d}  since last: median {np.median(b[sel]):4.1f}   until next: median {np.median(a[sel]):4.1f}  IQR {np.percentile(a[sel], 25):.1f}-{np.percentile(a[sel], 75):.1f}")
