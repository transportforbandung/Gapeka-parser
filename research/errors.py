"""Where are the errors? Held-out test legs, physics + section correction (no siding)."""
import csv
from collections import Counter, defaultdict
import numpy as np
import physics_model as pm
from siding_lib import legs, full_eval, PHYS, train_i, test_i, y

LINES = [(7, 12, "Duri–Tangerang, Tanahabang–Merak, Batuceper–Bandara SH"),
         (13, 18, "Jakartakota–Jatinegara–Bekasi, Tanjungpriuk"),
         (19, 24, "Kampungbandan–Duri–Tanahabang–Manggarai (OCR)"),
         (25, 27, "Cikampek–Padalarang–Bandung–Banjar, Cibatu–Garut"),
         (28, 28, "Sukabumi–Cianjur–Padalarang"),
         (29, 31, "Cikampek–Cirebon–Tegal, Cirebon–Prupuk"),
         (32, 34, "Tegal–Semarang–Brumbung–Cepu, Brumbung–Gundih"),
         (35, 37, "Prupuk–Purwokerto–Kroya–Kutoarjo (OCR)"),
         (38, 40, "Kutoarjo–Yogyakarta–Solo, YIA, Wonogiri, Gundih–Solo"),
         (41, 43, "Kertosono–Wlingi"),
         (44, 46, "Cepu–Surabaya Pasarturi, Gresik, Mojokerto–Surabaya"),
         (47, 49, "Wlingi–Malang–Bangil–Sidoarjo"),
         (50, 51, "Bangil–Kalisat–Ketapang, Panarukan")]
def line_of(page):
    return next((n for a, b, n in LINES if a <= page <= b), "?")
def page_of(a, b):
    p = pm.PROF.get(f"{a}-{b}") or pm.PROF.get(f"{b}-{a}")
    return p.get("page", 0)

s0 = np.zeros(len(legs)); by = defaultdict(list)
for i, g in enumerate(legs): by[g["grp"]].append(i)
for gr, idx in by.items():
    sub = [legs[i] for i in idx]
    s0[idx] = pm.simulate(pm.prepare(sub), len(sub), *PHYS[gr])
pc = full_eval(s0, "physics + section correction")

rows = []
for i in test_i:
    g = legs[i]
    pages = Counter(page_of(a, b) for a, b in zip(g["stations"], g["stations"][1:]))
    e = (pc[i] - y[i]) / 60
    rows.append(dict(train=g["train"], service=g["service"], group=g["grp"], line=line_of(pages.most_common(1)[0][0]),
                     frm=g["stations"][0], to=g["stations"][-1], via=len(g["stations"]) - 2, halts=len(g["halts"]),
                     ends=("stop" if g["start_rest"] else "pass") + "->" + ("stop" if g["end_rest"] else "pass"),
                     note=g["noted"], T_min=y[i] / 60, model_min=round(pc[i] / 60, 2), err_min=round(e, 2)))
with open("errors_by_leg.csv", "w", newline="") as f:
    w = csv.DictWriter(f, fieldnames=list(rows[0])); w.writeheader(); w.writerows(rows)

E = np.array([abs(r["err_min"]) for r in rows])
big, mid = E > 1, (E >= .5) & (E <= 1)
print(f"\ntest legs {len(rows)}: 0.5-1 min {mid.sum()}, >1 min {big.sum()}\n")
print(f"{'line (graph sheet)':58s} {'legs':>5s} {'0.5-1':>6s} {'>1':>5s} {'share of all >1':>16s} {'share of all 0.5-1':>19s}")
agg = defaultdict(list)
for r, e in zip(rows, E): agg[r["line"]].append(e)
for ln, v in sorted(agg.items(), key=lambda kv: -sum(np.array(kv[1]) > 1)):
    v = np.array(v)
    print(f"{ln:58s} {len(v):5d} {np.mean((v >= .5) & (v <= 1)):6.0%} {np.mean(v > 1):5.0%} {np.sum(v > 1) / big.sum():16.0%} {np.sum((v >= .5) & (v <= 1)) / mid.sum():19.0%}")

print("\nlegs (from -> to) with the most errors > 1 min   [count >1 / legs, mean signed error: + model slower, - GAPEKA slower]")
leg = defaultdict(list)
for r, e in zip(rows, E): leg[(r["frm"], r["to"], r["ends"], r["line"])].append(r["err_min"])
top = sorted(leg.items(), key=lambda kv: (-sum(abs(x) > 1 for x in kv[1]), -np.mean(np.abs(kv[1]))))[:25]
for (a, b, ends, ln), v in top:
    v = np.array(v)
    if (np.abs(v) > 1).sum() == 0: break
    print(f"  {a:>5s} -> {b:<5s} {ends:10s} {(np.abs(v) > 1).sum():3d}/{len(v):<3d} mean {v.mean():+5.1f}   {ln}")

print("\nstations at the start or end of legs with errors > 1 min (top 20)")
st, stn = Counter(), Counter()
for r, e in zip(rows, E):
    for s in (r["frm"], r["to"]):
        stn[s] += 1
        if e > 1: st[s] += 1
names = dict(pm.__dict__.get("db_names", {}))
import sqlite3
names = dict(sqlite3.connect("/home/user/Gapeka-parser/gapeka.db").execute("select code,name from stations"))
for s, n in st.most_common(20):
    print(f"  {s:5s} {names.get(s, ''):22s} {n:3d} of {stn[s]:4d} legs ({n / stn[s]:.0%})")

print("\nby rolling-stock group and leg type (share of legs > 1 min)")
grp = defaultdict(list)
for r, e in zip(rows, E): grp[(r["group"], r["ends"])].append(e)
for k, v in sorted(grp.items()):
    v = np.array(v)
    print(f"  {k[0]:9s} {k[1]:10s} legs {len(v):5d}   0.5-1 {np.mean((v >= .5) & (v <= 1)):4.0%}   >1 {np.mean(v > 1):4.0%}   share of all >1 {np.sum(v > 1) / big.sum():4.0%}")
nt = np.array([r["note"] for r in rows])
print(f"\nlegs with crossing/overtake note: {nt.sum()} legs, {np.sum(big & nt)} of the {big.sum()} errors > 1 min ({np.sum(big & nt) / big.sum():.0%})")
