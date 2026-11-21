"""Learned per-station points zones (30 km/h for Z m next to the station), separately for stopping / passing trains
and per rolling-stock group. Additive approximation: sim(za, zb) = base + dA(za) + dB(zb)."""
from collections import defaultdict
import numpy as np
import physics_model as pm
from siding_lib import legs, full_eval, PHYS, train_i, test_i, y, nh, grp

ZS = (0, 300, 600, 1000, 1500)
V = 30.0
def sim_variant(za, zb):
    L = []
    for g in legs:
        h = dict(g); cap = g["cap"].copy()
        na, nb = min(int(za / pm.DX), len(cap)), min(int(zb / pm.DX), len(cap))
        if na: cap[:na] = np.minimum(cap[:na], V)
        if nb: cap[-nb:] = np.minimum(cap[-nb:], V)
        h["cap"] = cap; L.append(h)
    s = np.zeros(len(L)); by = defaultdict(list)
    for i, g in enumerate(L): by[g["grp"]].append(i)
    for gr, idx in by.items():
        sub = [L[i] for i in idx]
        s[idx] = pm.simulate(pm.prepare(sub), len(sub), *PHYS[gr])
    return s
base = sim_variant(0, 0)
dA = {z: sim_variant(z, 0) - base for z in ZS[1:]}; dA[0] = np.zeros(len(legs))
dB = {z: sim_variant(0, z) - base for z in ZS[1:]}; dB[0] = np.zeros(len(legs))
full_eval(base, "no points zones (restriction fixes only)")

st_a = np.array([g["stations"][0] for g in legs]); st_b = np.array([g["stations"][-1] for g in legs])
rest_a = np.array([g["start_rest"] for g in legs]); rest_b = np.array([g["end_rest"] for g in legs])
za = np.zeros(len(legs), int); zb = np.zeros(len(legs), int)
tr = np.zeros(len(legs), bool); tr[train_i] = True
def fit_lin(s, mask):
    X = np.column_stack([s[mask], np.ones(mask.sum()), nh[mask]]); yy = y[mask]
    c = np.linalg.lstsq(X, yy, rcond=None)[0]
    for _ in range(30):
        w = 1 / np.maximum(np.abs(yy - X @ c), 5.0); c = np.linalg.lstsq(X * w[:, None] ** .5, yy * w ** .5, rcond=None)[0]
    return c
def current():
    return base + np.array([dA[z][i] for i, z in enumerate(za)]) + np.array([dB[z][i] for i, z in enumerate(zb)])
learned = {}
for it in range(3):
    s = current()
    coef = {gr: fit_lin(s, tr & (grp == gr)) for gr in PHYS}
    cg = np.array([coef[g] for g in grp])
    changed = 0
    for end, zz, stv, rest, D in (("dep", za, st_a, rest_a, dA), ("arr", zb, st_b, rest_b, dB)):
        keys = defaultdict(list)
        for i in np.where(tr)[0]: keys[(stv[i], "stop" if rest[i] else "pass", grp[i])].append(i)
        for k, ii in keys.items():
            if len(ii) < 4: continue
            ii = np.array(ii)
            other = s[ii] - D[0][ii] - np.array([D[z][i] for i, z in zip(ii, zz[ii])])   # sim without this end's zone
            errs = {z: np.abs(cg[ii, 0] * (other + D[z][ii]) + cg[ii, 1] + cg[ii, 2] * nh[ii] - y[ii]).sum() for z in ZS}
            bz = min(ZS, key=lambda z: errs[z] if z == 0 else errs[z] * 1.03)   # zone must beat none by 3 %
            allii = np.where((stv == k[0]) & (rest if k[1] == "stop" else ~rest) & (grp == k[2]))[0]
            if (zz[allii] != bz).any(): changed += 1
            zz[allii] = bz
            learned[(end,) + k] = bz
    print(f"   iteration {it + 1}: {changed} station decisions changed", flush=True)
pc = full_eval(current(), "+ learned points zones (30 km/h)")
z = {k: v for k, v in learned.items() if v}
print(f"\nstations with a points zone: {len(z)} of {len(learned)} station/end/stop-pass/group cases")
import sqlite3
names = dict(sqlite3.connect("/home/user/Gapeka-parser/gapeka.db").execute("select code,name from stations"))
for k in sorted(z, key=lambda k: -z[k])[:30]:
    print(f"   {names.get(k[1], k[1]):20s} {k[0]:3s} {k[2]:4s} {k[3]:9s} {z[k]:5d} m")
for k in [k for k in learned if k[1] in ("KAC", "GDB", "BKS")]:
    print(f"   check {names.get(k[1]):14s} {k[0]:3s} {k[2]:4s} {k[3]:9s} {learned[k]:5d} m")
np.save("points_pred.npy", pc)
import pickle
pickle.dump(learned, open("points_learned.pkl", "wb"))
