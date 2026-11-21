import csv, numpy as np
from collections import defaultdict
import halts_points as hp   # reruns the halt test; reuse its results
r = hp.res[("surabaya", "fixed group accel/brake", "no zones")]
g = defaultdict(list)
for share, kai, halt, prev in r: g[(halt, prev)].append((share + 1, kai))   # Surabaya shows departure (+1 min dwell)
print("\nSurabaya halts: model (departure = arrival + 1) vs KAI, minutes after previous departure")
for k, v in sorted(g.items()):
    m = np.array([x for x, _ in v]); kk = np.array([y for _, y in v])
    print(f"  {k[0]:4s} after {k[1]:4s} n={len(v):2d}  model {m.mean():5.2f}  KAI {kk.mean():5.2f}  rounded exact {np.mean(np.round(m) == kk):4.0%}")
