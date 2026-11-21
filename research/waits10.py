"""Short automatic blocks: the waiting train may need the overtaker k blocks ahead (k = 1..5)."""
import numpy as np
from collections import defaultdict
exec(open("waits9.py").read().split("res = defaultdict")[0])
res = defaultdict(list); nblocks = []
for (s, w), evs in groups.items():
    W = at(w, s)
    for kind, ov in evs:
        if kind != "overtake": continue
        O = at(ov, s)
        tS = O[4] if O[4] is not None else O[3]
        iw = next(i for i, r in enumerate(rows[w]) if r[0] == W[0])
        if iw + 1 >= len(rows[w]) or tS is None: continue
        n = rows[w][iw + 1][1]
        q = at(ov, n)
        if q is None or t_at(q) is None: continue
        offs, L = blocks_from(s, n)
        if not offs: continue
        nblocks.append(len(offs))
        tN = t_at(q)
        for k in range(1, 6):
            d = offs[k - 1] if k <= len(offs) else L          # fewer signals than k: the next station
            ref = tS + wrap(tN - tS) * min(d / L, 1.0)
            res[k].append(wrap(W[4] - ref) / 60)
        res["next"].append(wrap(W[4] - tN) / 60)
print(f"overtakes with block data: {len(nblocks)}; signals on the waiting train's track per section: median {np.median(nblocks):.0f} (range {min(nblocks)}-{max(nblocks)})")
for k in list(range(1, 6)) + ["next"]:
    v = np.array(res[k]); md = np.median(v)
    lab = "next station" if k == "next" else f"overtaker {k} block{'s' if k > 1 else ''} ahead"
    print(f"  {lab:26s} typical gap {md:+5.1f} min   within ±0.5 {np.mean(np.abs(v - md) <= .5):4.0%}   within ±1 {np.mean(np.abs(v - md) <= 1):4.0%}")
