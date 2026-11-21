import numpy as np
from collections import defaultdict
import halts_points as hp
print("\n--- KAI halts, fixed group accel/brake, no zones, published time = arrival at the halt")
for region in ("bandung", "surabaya"):
    r = hp.res[(region, "fixed group accel/brake", "no zones")]
    p = np.array([x[0] for x in r]); k = np.array([x[1] for x in r]); a = np.abs(p - k)
    print(f"{region:9s} exact {np.mean(np.round(p) == k):4.0%}  ±1 {np.mean(np.abs(np.round(p) - k) <= 1):4.0%}  |err|<0.5 {np.mean(a < .5):4.0%}  0.5-1 {np.mean((a >= .5) & (a <= 1)):4.0%}  >1 {np.mean(a > 1):4.0%}  n {len(r)}")
    g = defaultdict(list)
    for share, kai, halt, prev in r: g[(halt, prev)].append((share, kai))
    for kk, v in sorted(g.items()):
        m = np.array([x for x, _ in v]); q = np.array([y for _, y in v])
        print(f"     {kk[0]:4s} after {kk[1]:4s} n={len(v):2d}  model {m.mean():5.2f}  KAI {q.mean():5.2f}  exact {np.mean(np.round(m) == q):4.0%}")
