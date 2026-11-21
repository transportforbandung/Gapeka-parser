"""Overtaking: waiting train departs g minutes after the overtaker reaches a point D km beyond the station
(next block / home signal on double track; next station on single track). Fit D and g per line group."""
import json
from collections import defaultdict
import numpy as np
exec(open("waits3.py").read().split("tab = defaultdict")[0])
P = json.load(open("line_profile.json"))
def km(a, b):
    p = P.get(f"{a}-{b}") or P.get(f"{b}-{a}")
    return p["km"] if p else None
cases = defaultdict(list)          # line -> list of (W_dep, t_S, t_N, L)
for (s, w), evs in groups.items():
    W = at(w, s)
    for kind, ov in evs:
        if kind != "overtake": continue
        O = at(ov, s)
        tS = O[4] if O[4] is not None else O[3]
        iw = next(i for i, r in enumerate(rows[w]) if r[0] == W[0])
        if iw + 1 >= len(rows[w]): continue
        n = rows[w][iw + 1][1]                       # the very next station on the route
        q = at(ov, n)
        if q is None or t_at(q) is None or tS is None: continue
        L = km(s, n)
        if not L: continue
        cases[line(s)].append((W[4], tS, t_at(q), L, ttype(w)))
D_GRID = (0, 0.5, 1.0, 1.5, 2.0, 2.5, 3.0, 4.0, 6.0, 99)
print(f"{'line':45s} {'n':>4s}   " + "  ".join(f"{('next st.' if d == 99 else f'{d:.1f} km'):>8s}" for d in D_GRID))
best_all = {}
for ln in sorted(cases, key=lambda l: -len(cases[l])):
    cs = cases[ln]
    if len(cs) < 8: continue
    cells = []
    best = None
    for D in D_GRID:
        g = []
        for wd, ts, tn, L, tt in cs:
            frac = min(D / L, 1.0)
            ref = ts + wrap(tn - ts) * frac
            g.append(wrap(wd - ref) / 60)
        g = np.array(g)
        md = np.median(g)
        share = np.mean(np.abs(g - md) <= .5)
        cells.append(f"{share:4.0%}@{md:+.1f}")
        if best is None or share > best[0]:
            best = (share, D, md)
    best_all[ln] = best
    print(f"{ln:45s} {len(cs):4d}   " + "  ".join(f"{c:>8s}" for c in cells))
print("\n(each cell: share of waiting departures within ±0.5 min of the typical gap @ that typical gap in minutes)")
print("\nbest distance per line:")
for ln, (share, D, md) in best_all.items():
    print(f"  {ln:45s} D = {'next station' if D == 99 else f'{D:.1f} km':12s}  gap {md:+.1f} min  within ±0.5: {share:.0%}")
