"""Kiaracondong: 1180 m crossover zone (30 km/h) for intercity trains stopping there, shorter zone for commuters."""
from collections import defaultdict
import numpy as np
import physics_model as pm
from siding_lib import legs, full_eval, PHYS, test_i, y

def commuter(g): return g["service"].startswith("Commuter") or g["service"] in ("Ka Feeder",)
def run(z_ic, z_cl):
    L = []
    for g in legs:
        h = dict(g); cap = g["cap"]; st = g["stations"]
        Z = z_cl if commuter(g) else z_ic
        n = min(int(Z / pm.DX), len(cap))
        if n and g["start_rest"] and st[0] == "KAC" and st[1] == "GDB":
            cap = cap.copy(); cap[:n] = np.minimum(cap[:n], 30.0)
        if n and g["end_rest"] and st[-1] == "KAC" and st[-2] == "GDB":
            cap = cap.copy(); cap[-n:] = np.minimum(cap[-n:], 30.0)
        h["cap"] = cap; L.append(h)
    s = np.zeros(len(L)); by = defaultdict(list)
    for i, g in enumerate(L): by[g["grp"]].append(i)
    for gr, idx in by.items():
        sub = [L[i] for i in idx]
        s[idx] = pm.simulate(pm.prepare(sub), len(sub), *PHYS[gr])
    pc = full_eval(s, f"intercity {z_ic} m, commuter {z_cl} m")
    for nm, sel in (("stop KAC -> GDB", lambda g: g["start_rest"] and g["stations"][:2] == ["KAC", "GDB"]),
                    ("GDB -> stop KAC", lambda g: g["end_rest"] and g["stations"][-2:] == ["GDB", "KAC"])):
        for kind, cf in (("intercity", lambda g: not commuter(g)), ("commuter", commuter)):
            ii = [i for i in range(len(legs)) if sel(legs[i]) and cf(legs[i])]
            if not ii: continue
            e = (pc[ii] - y[ii]) / 60
            print(f"      {nm:16s} {kind:9s} n={len(ii):2d}  mean err {e.mean():+5.2f}  |err|<0.5 {np.mean(np.abs(e) < .5):4.0%}  >1 {np.mean(np.abs(e) > 1):4.0%}", flush=True)

for z_ic, z_cl in ((0, 0), (1180, 0), (1180, 300), (1180, 590), (1180, 1180)):
    run(z_ic, z_cl)
