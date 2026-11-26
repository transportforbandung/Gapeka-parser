"""Following gaps between consecutive same-direction trains in GAPEKA 2026 (planner.Timeline: all stations, untimed
passes interpolated). Single track: follower's departure at a - leader's arrival at b of the same block (block clear).
Double track: follower's time - leader's time at the block entry (departure / pass) and at the exit (arrival)."""
import gzip, json, sys
from collections import defaultdict
import numpy as np
sys.path.insert(0, "/home/user/Gapeka-parser")
import planner
pkg = json.load(gzip.open("/home/user/Gapeka-parser/dist/gapeka-2026/package.json.gz"))
typ = {t["no"]: t["type"] for t in pkg["trains"]}
tl = planner.Timeline(pkg)
single, dbl_in, dbl_out = [], [], []
per_block = defaultdict(list)
for (a, b), occ in tl.block.items():
    d = tl.block_double[(a, b)]
    occ = sorted(occ)
    for (e1, x1, n1), (e2, x2, n2) in zip(occ, occ[1:]):
        if typ.get(n1) == "dinas" or typ.get(n2) == "dinas":
            continue
        if d:
            dbl_in.append((e2 - e1) / 60)
            dbl_out.append((x2 - x1) / 60)
        else:
            single.append((e2 - x1) / 60)
            per_block[(a, b)].append((e2 - x1) / 60)
for lab, v in (("single: follower departs - leader clears", single), ("double: entry gap", dbl_in), ("double: exit gap", dbl_out)):
    v = np.array(v)
    print(f"{lab:42s} n {len(v):6d}  <0 {np.mean(v < 0):5.1%}  p1 {np.percentile(v, 1):5.1f}  p5 {np.percentile(v, 5):5.1f}"
          f"  p10 {np.percentile(v, 10):5.1f}  median {np.median(v):5.1f} min")
v = np.array(single)
print("single track, share of gaps:", {g: f"{np.mean((v >= g) & (v < g + 1)):.1%}" for g in range(0, 6)})
# crossing on single track: an opposing train enters the block (b -> a) after the other has cleared it at b
cross = []
for (a, b), occ in tl.block.items():
    if tl.block_double[(a, b)]:
        continue
    opp = tl.block.get((b, a), [])
    for e1, x1, n1 in occ:
        for e2, x2, n2 in opp:
            if typ.get(n1) == "dinas" or typ.get(n2) == "dinas":
                continue
            if e2 >= x1 and e2 - x1 < 1800:          # opposing train enters at b after n1 arrived at b
                cross.append((e2 - x1) / 60)
            if e2 < x1 and x2 > e1:
                cross.append(-1.0)                   # overlap = two trains in one single-track block
v = np.array(cross)
print(f"single crossing: opposing train enters after the other clears  n {len(v)}  overlap {np.mean(v < 0):.2%}  "
      f"p1 {np.percentile(v[v >= 0], 1):.1f}  p5 {np.percentile(v[v >= 0], 5):.1f}  "
      f"share 0-1 / 1-2 / 2-3 min {np.mean((v >= 0) & (v < 1)):.1%} / {np.mean((v >= 1) & (v < 2)):.1%} / {np.mean((v >= 2) & (v < 3)):.1%}")
# per block minimum following gap on double track
mins = []
for (a, b), occ in tl.block.items():
    if not tl.block_double[(a, b)] or len(occ) < 10:
        continue
    occ = sorted(o for o in occ if typ.get(o[2]) != "dinas")
    g = [min(e2 - e1, x2 - x1) / 60 for (e1, x1, _), (e2, x2, _) in zip(occ, occ[1:])]
    if g:
        mins.append(min(g))
m = np.array(mins)
print(f"double track, minimum following gap per block (n {len(m)}): p10 {np.percentile(m, 10):.1f}  median {np.median(m):.1f}  "
      f"p90 {np.percentile(m, 90):.1f}  values {sorted(set(np.round(m).astype(int)))}")
