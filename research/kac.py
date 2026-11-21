"""Kiaracondong crossover zone: 30 km/h for Z m after a stop at KAC, for trains departing towards Gedebage."""
from collections import defaultdict
import numpy as np
import physics_model as pm
from siding_lib import legs, full_eval, PHYS, test_i, y

def run(Z, reverse=False):
    L = []
    for g in legs:
        h = dict(g); cap = g["cap"]
        st = g["stations"]
        n = min(int(Z / pm.DX), len(cap))
        if Z and g["start_rest"] and st[0] == "KAC" and st[1] == "GDB":
            cap = cap.copy(); cap[:n] = np.minimum(cap[:n], 30.0)
        if Z and reverse and g["end_rest"] and st[-1] == "KAC" and st[-2] == "GDB":
            cap = cap.copy(); cap[-n:] = np.minimum(cap[-n:], 30.0)
        h["cap"] = cap; L.append(h)
    s = np.zeros(len(L)); by = defaultdict(list)
    for i, g in enumerate(L): by[g["grp"]].append(i)
    for gr, idx in by.items():
        sub = [L[i] for i in idx]
        s[idx] = pm.simulate(pm.prepare(sub), len(sub), *PHYS[gr])
    lab = f"KAC zone {Z} m" + (" + arrivals from GDB" if reverse else "")
    pc = full_eval(s, lab if Z else "no KAC zone")
    for nm, sel in (("stop at KAC -> GDB", lambda g: g["start_rest"] and g["stations"][0] == "KAC" and g["stations"][1] == "GDB"),
                    ("GDB -> stop at KAC", lambda g: g["end_rest"] and g["stations"][-1] == "KAC" and g["stations"][-2] == "GDB"),
                    ("pass KAC -> GDB", lambda g: not g["start_rest"] and g["stations"][0] == "KAC" and g["stations"][1] == "GDB")):
        ii = [i for i in range(len(legs)) if sel(legs[i])]
        tt = [i for i in ii if i in set(test_i)]
        e = (pc[ii] - y[ii]) / 60
        et = (pc[tt] - y[tt]) / 60 if tt else np.array([])
        print(f"      {nm:20s} all legs n={len(ii):3d} mean err {e.mean():+5.2f}  |err|>1 {np.mean(np.abs(e) > 1):4.0%}   test n={len(tt):2d} |err|>1 {np.mean(np.abs(et) > 1) if len(et) else float('nan'):4.0%}", flush=True)

for Z in (0, 590, 840, 1180):
    run(Z)
run(840, reverse=True)
