import os
os.environ.setdefault("RULES", "rules.json")
from collections import defaultdict
import numpy as np
import relearn as R
legs, y, base, names = R.legs, R.y, R.base, R.names
Eb = (base - y)
def show(title, sel):
    groups = defaultdict(list)
    for i, g in enumerate(legs):
        if g["noted"]: continue
        k = sel(g)
        if k: groups[k].append(i)
    print(f"\n{title}  (physics only; err = model - GAPEKA, + = model slower)")
    for k, ii in sorted(groups.items(), key=lambda kv: -len(kv[1])):
        if len(ii) < 5: continue
        e = Eb[ii]
        print(f"   {str(k):60s} n={len(ii):4d}  mean {np.mean(e):+5.0f} s  >1 min {np.mean(np.abs(e) > 60):4.0%}   GAPEKA {np.median(y[ii]) / 60:4.1f} min  model {np.median(base[ii]) / 60:4.1f}")
# 3. Bandung Raya around Padalarang / Cimahi
show("3. commuter legs touching Padalarang / Gadobangkong / Cimahi",
     lambda g: (g["service"][:26], "-".join(g["stations"]), "stop" if g["start_rest"] else "pass", "stop" if g["end_rest"] else "pass")
     if g["type"] == "commuter" and set(g["stations"]) & {"PDL", "GK", "CMI"} else None)
# 4. Prupuk - Purwokerto
show("4. intercity legs around Linggapura / Karangsari / Karanggandul",
     lambda g: ("-".join(g["stations"]), "stop" if g["start_rest"] else "pass", "stop" if g["end_rest"] else "pass")
     if g["type"] == "intercity" and set(g["stations"]) & {"LG", "KRI", "KGD", "KRS"} and len(g["stations"]) <= 3 else None)
# 5. Universitas Indonesia
show("5. KRL legs at Univ. Indonesia",
     lambda g: ("-".join(g["stations"]),) if g["type"] == "krl" and "UI" in (g["stations"][0], g["stations"][-1]) else None)
