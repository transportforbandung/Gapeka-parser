import os, json
os.environ.setdefault("RULES", "rules.json")
from collections import defaultdict
import numpy as np
import relearn as R
legs, y, base, pred, names = R.legs, R.y, R.base, R.pred, R.names
P = json.load(open("line_profile.json"))
def pg(a, b):
    p = P.get(f"{a}-{b}") or P.get(f"{b}-{a}"); return p.get("page") if p else None
grp = defaultdict(list)
for i, g in enumerate(legs):
    if g["noted"]: continue
    pages = {pg(a, b) for a, b in zip(g["stations"], g["stations"][1:])}
    if pages & {47, 48, 49, 50, 51}:
        key = ("-".join(g["stations"][:1] + g["stations"][-1:]) if len(g["stations"]) > 2 else "-".join(g["stations"]))
        grp[(key, g["type"], ("stop" if g["start_rest"] else "pass") + ">" + ("stop" if g["end_rest"] else "pass"))].append(i)
rows = []
for k, ii in grp.items():
    if len(ii) >= 4:
        e = base[ii] - y[ii]; ef = pred[ii] - y[ii]
        rows.append((np.mean(np.abs(ef) > 60), k, len(ii), np.mean(e), np.median(y[ii]) / 60, np.median(base[ii]) / 60, np.std(y[ii]) / 60))
print("East Java single track (sheets 47-51): legs sorted by share > 1 min in the FULL model")
print("   leg / type / ends                           n    full>1   physics mean   GAPEKA med  model med  GAPEKA spread (sd)")
for big, k, n, e, gy, gm, sd in sorted(rows, key=lambda r: -r[0])[:22]:
    print(f"   {str(k):44s} {n:4d}   {big:4.0%}   {e:+6.0f} s      {gy:5.1f}      {gm:5.1f}      {sd:4.1f} min")
print("\nextracted limits on these sheets (pairs with restrictions):")
for k, p in P.items():
    if p.get("page") in (47, 50) and p["restr"]:
        print(f"   {k:12s} {p['km']:6.2f} km umum {p['umum']}  " + "  ".join(f"{r['o0']:.2f}-{r['o1']:.2f}@{r['v']}{('/' + r['tags']) if r['tags'] else ''}" for r in p["restr"]))
