import os
os.environ.setdefault("RULES", "rules.json")
from collections import Counter, defaultdict
import numpy as np
import relearn as R
import physics_model as pm
legs, y, pred, base, test, noted, typ, names = R.legs, R.y, R.pred, R.base, R.test, R.noted, R.typ, R.names
exec(open("waits3.py").read().split("def line(s)")[0].split("P = json.load")[1].join(["P = json.load", ""]) if False else "")
import json
P = json.load(open("line_profile.json"))
LINES = [(7, 12, "Duri-Tangerang, Merak, Bandara"), (13, 18, "Jakartakota-Jatinegara-Bekasi, Tanjungpriuk"),
         (19, 24, "Manggarai-Tanahabang-Duri, Bogor"), (25, 27, "Cikampek-Bandung-Banjar, Garut"), (28, 28, "Sukabumi-Padalarang"),
         (29, 31, "Cikampek-Cirebon-Tegal, Prupuk"), (32, 34, "Tegal-Semarang-Cepu, Gundih"), (35, 37, "Prupuk-Purwokerto-Kutoarjo"),
         (38, 40, "Kutoarjo-Yogyakarta-Solo"), (41, 43, "Kertosono-Wlingi"), (44, 46, "Cepu-Surabaya, Mojokerto"),
         (47, 49, "Malang-Bangil-Sidoarjo"), (50, 51, "Bangil-Kalisat-Ketapang")]
def page_of(a, b):
    p = P.get(f"{a}-{b}") or P.get(f"{b}-{a}"); return p.get("page", 0) if p else 0
def line_of(g):
    pg = Counter(page_of(a, b) for a, b in zip(g["stations"], g["stations"][1:])).most_common(1)[0][0]
    return next((n for a, b, n in LINES if a <= pg <= b), "?")
E = (pred - y) / 60; Eb = (base - y) / 60
m = test & ~noted
print("\n=== A. full model, held-out legs without notes, by line (sorted by share > 1 min)")
by = defaultdict(list)
for i in np.where(m)[0]: by[line_of(legs[i])].append(i)
tot_big = np.sum(np.abs(E[m]) > 1)
for ln, ii in sorted(by.items(), key=lambda kv: -np.mean(np.abs(E[kv[1]]) > 1)):
    e = E[ii]
    print(f"  {ln:45s} n={len(ii):4d}  <0.5 {np.mean(np.abs(e) < .5):4.0%}  0.5-1 {np.mean((np.abs(e) >= .5) & (np.abs(e) <= 1)):4.0%}  >1 {np.mean(np.abs(e) > 1):4.0%}  share of all >1 {np.sum(np.abs(e) > 1) / tot_big:4.0%}")
print("\n=== B. physics only (no learned corrections), all legs without notes: worst station ends (n >= 10, |mean| >= 40 s)")
cells = defaultdict(list)
for i, g in enumerate(legs):
    if g["noted"]: continue
    for end, st, rest in (("dep", g["stations"][0], g["start_rest"]), ("arr", g["stations"][-1], g["end_rest"])):
        cells[(st, end, "stop" if rest else "pass", g["type"])].append(Eb[i])
rows = [(k, np.mean(v) * 60, len(v), np.mean(np.abs(np.array(v)) > 1)) for k, v in cells.items() if len(v) >= 10 and abs(np.mean(v) * 60) >= 40]
for k, mu, n, big in sorted(rows, key=lambda r: -abs(r[1]) * r[2] ** .5)[:30]:
    print(f"  {names.get(k[0], k[0]):20s} {k[1]} {k[2]:4s} {k[3]:9s} n={n:4d}  mean {mu:+5.0f} s  >1 min {big:4.0%}")
print("\n=== C. coverage: timed legs without full sheet data")
import sqlite3
db = sqlite3.connect("/home/user/Gapeka-parser/gapeka.db")
miss = Counter(); missing_legs = 0; tot = 0
svc = dict(db.execute("select no,service from trains"))
for no in svc:
    rr = db.execute("select seq,station,kind,arr_s,dep_s from stops where train_no=? order by seq", (no,)).fetchall()
    timed = [i for i, r in enumerate(rr) if r[2] in ("stop", "origin_or_end") or (r[2] == "pass" and r[4] is not None)]
    for p, n in zip(timed, timed[1:]):
        tot += 1
        bad = [(rr[i][1], rr[i + 1][1]) for i in range(p, n) if pm.profile(rr[i][1], rr[i + 1][1]) is None]
        if bad:
            missing_legs += 1
            for a, b in bad: miss[tuple(sorted((a, b)))] += 1
print(f"  timed legs {tot}, without full sheet data {missing_legs} ({missing_legs / tot:.0%}); distinct station pairs missing {len(miss)}")
for (a, b), c in miss.most_common(25):
    print(f"    {a:5s}-{b:5s} {names.get(a, ''):18s} - {names.get(b, ''):18s} used by {c:4d} legs")
