"""Double-track block rule vs GAPEKA (user: "trains usually only need the block ahead to clear").
Block points = stations (not halts) + the block signals of the track in the travel direction (package
sections.block_signals); a train's time at a block point by distance between its times at the halts in the block.
Prints, for consecutive GAPEKA trains in each double-track block:
  1. the margin between the leader clearing block k (passing point k+1) and the follower reaching point k
     (percentiles, min), by blocks with / without signals, follower faster or not
  2. pairs that break the rule (approach_blocks 0 and 1: a faster follower checked one block point earlier)
  3. the tightest spacing per block (headways.double_block_spacing_s): where the sheet shows too few signals
Run: python research/block_clear.py"""
import collections, gzip, json, sys
sys.path.insert(0, "/home/user/Gapeka-parser")
import planner
from planner import knot_time

pkg = json.load(gzip.open("/home/user/Gapeka-parser/dist/gapeka-2026/package.json.gz"))
tl = planner.Timeline(pkg)
ty = {t["no"]: t["type"] for t in pkg["trains"]}
TOL = 59
margins = collections.defaultdict(list)
viol = {0: collections.Counter(), 1: collections.Counter()}
pairs = 0
for (a, b), occ in tl.block_base.items():
    if not tl.block_double[(a, b)]:
        continue
    f = tl.bpts[(a, b)]
    n = len(f) - 1
    occ = sorted(o for o in occ if ty.get(o[2]) != "dinas")
    for i, (e1, x1, n1) in enumerate(occ):
        k1 = tl.knots.get((a, b, n1))
        A = lambda g, c: e1 + knot_time(k1, g, x1 - e1, c)
        for m, (e2, x2, n2) in enumerate(occ[i + 1:]):
            if e2 > x1 + 600:
                break
            k2 = tl.knots.get((a, b, n2))
            B = lambda g, c: e2 + knot_time(k2, g, x2 - e2, c)
            faster = (x2 - e2) < (x1 - e1) - 30
            if m == 0 and x2 >= x1:
                margins[("signals" if n > 1 else "stations only", "faster follower" if faster else "same / slower")] \
                    .append(min(B(f[k], False) - A(f[k + 1], True) for k in range(n)))
            pairs += 1
            for ab in (0, 1):
                a2 = ab if faster else 0
                beh = all(B(f[max(k - a2, 0)], False) >= A(f[k + 1], True) - TOL for k in range(n))
                a1 = ab if (x1 - e1) < (x2 - e2) - 30 else 0
                ahd = all(A(f[max(k - a1, 0)], False) >= B(f[k + 1], True) - TOL for k in range(n))
                if not (beh or ahd):
                    viol[ab][(a, b)] += 1
print("1. margin follower at point k - leader past point k+1 (min); percentiles 0.1 / 1 / 5 / 25 / 50 %")
for k, v in sorted(margins.items()):
    v.sort()
    print(f"   {k[0]:14s} {k[1]:16s} n {len(v):5d}:", [round(v[int(q * (len(v) - 1))] / 60, 1)
                                                      for q in (0.001, 0.01, 0.05, 0.25, 0.5)])
for ab in (0, 1):
    print(f"2. approach_blocks {ab}: GAPEKA pairs breaking the rule {sum(viol[ab].values())} of {pairs}:",
          viol[ab].most_common(6))
h = sorted(tl.block_hmin.items(), key=lambda x: x[1])
print("3. tightest GAPEKA spacing per block (s):", len(h), "blocks; tightest", h[:6])
