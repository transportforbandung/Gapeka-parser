import os
os.environ.setdefault("RULES", "rules_none.json")
import numpy as np
from collections import defaultdict
import relearn as R
legs, y, base, sim = R.legs, R.y, R.base, R.sim
print("\nKRL legs, physics + calibration (no corrections): residual = GAPEKA - model, by number of halts in the leg")
for nh in range(0, 7):
    ii = [i for i, g in enumerate(legs) if g["type"] == "krl" and not g["noted"] and len(g["halts"]) == nh]
    if len(ii) > 20:
        r = (y[ii] - base[ii])
        print(f"  halts {nh}: n={len(ii):4d}  mean {r.mean():+5.0f} s  median {np.median(r):+5.0f} s")
print("\nKRL legs starting at PSM / UI and neighbours (no corrections)")
by = defaultdict(list)
for i, g in enumerate(legs):
    if g["type"] == "krl" and not g["noted"] and g["stations"][0] in ("PSM", "UI", "DPB", "MRI", "TNT", "LNA") :
        by[(g["stations"][0], g["stations"][-1], len(g["halts"]))].append(i)
for k, ii in sorted(by.items(), key=lambda kv: -len(kv[1]))[:14]:
    r = y[ii] - base[ii]
    g = legs[ii[0]]
    print(f"  {k[0]:4s}->{k[1]:4s} halts {k[2]}  n={len(ii):3d}  GAPEKA {np.median(y[ii]) / 60:4.1f} min  model {np.median(base[ii]) / 60:4.1f} min  "
          f"resid {np.median(r):+4.0f} s   via {'-'.join(g['stations'])}")
