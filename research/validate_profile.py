"""Check every station-pair length in line_profile.json against the timetable and gapeka.db.

For a pair a-b, trains with timed points at both a and b (adjacent rows) give a running time T and a limit vmax.
A length L is plausible if L / T_min <= 1.15 * vmax (not faster than allowed) and L / T_median >= 8 km/h.
Implausible lengths are replaced by gapeka.db's value when that one is plausible, otherwise the pair is flagged.
Writes line_profile.json (validated) and keeps the raw file as line_profile_raw.json.
"""
import json
import shutil
import sqlite3
from collections import defaultdict

shutil.copy("line_profile.json", "line_profile_raw.json")
P = json.load(open("line_profile_raw.json"))
db = sqlite3.connect("/home/user/Gapeka-parser/gapeka.db")
dbkm = {}
for a, b, km, pg in db.execute("select a,b,km,page from station_links"):
    dbkm.setdefault(frozenset((a, b)), km)

obs = defaultdict(list)                     # unordered pair -> [(T seconds, vmax)]
for (no,) in db.execute("select no from trains"):
    rows = db.execute("select station,kind,arr_s,dep_s,vmax from stops where train_no=? order by seq", (no,)).fetchall()
    for x, z in zip(rows, rows[1:]):
        t0 = x[3] if x[3] is not None else None
        t1 = z[2] if z[2] is not None else z[3]
        if t0 is None or t1 is None or not x[4]:
            continue
        T = (t1 - t0) % 86400
        if 0 < T < 3600:
            obs[frozenset((x[0], z[0]))].append((T, x[4]))


def plausible(L, pair):
    o = obs.get(pair)
    if not o:
        return None                         # cannot check
    fast = min(o)                           # shortest time
    med = sorted(t for t, _ in o)[len(o) // 2]   # typical time (the slowest can include signal waits, e.g. Ciroyom-Bandung)
    return L / (fast[0] / 3600) <= 1.15 * fast[1] and L / (med / 3600) >= 8


out, report = {}, []
for k, p in P.items():
    a, b = k.split("-")
    pair = frozenset((a, b))
    ok = plausible(p["km"], pair)
    other = P.get(f"{b}-{a}")
    if ok is False:
        alt = dbkm.get(pair)
        if other and plausible(other["km"], pair):
            report.append((k, p["km"], f"replaced by reverse-orientation entry {other['km']}"))
            continue                         # the reverse entry is kept and used for both directions
        if alt and plausible(alt, pair):
            scale = alt / p["km"]
            p = dict(p, km=alt, restr=[dict(r, o0=round(r["o0"] * scale, 3), o1=round(r["o1"] * scale, 3)) for r in p["restr"]],
                     km_source="gapeka.db (sheet value implausible)")
            report.append((k, P[k]["km"], f"replaced by gapeka.db {alt}"))
        else:
            report.append((k, p["km"], "implausible, dropped"))
            continue
    elif ok is None and other and abs(other["km"] - p["km"]) > 0.3:
        # both orientations, different lengths, no timetable check: keep the one closer to gapeka.db, else the shorter
        alt = dbkm.get(pair)
        mine_better = abs(p["km"] - alt) <= abs(other["km"] - alt) if alt else p["km"] <= other["km"]
        if not mine_better:
            report.append((k, p["km"], f"conflicts with reverse entry {other['km']}, dropped"))
            continue
    out[k] = p
json.dump(out, open("line_profile.json", "w"), indent=0)
print(f"pairs in {len(P)}, out {len(out)}; changes: {len(report)}")
for r in report:
    print("  ", r)
checked = sum(1 for k in out if plausible(out[k]["km"], frozenset(k.split("-"))) is not None)
print(f"pairs checked against the timetable: {checked} of {len(out)}")
