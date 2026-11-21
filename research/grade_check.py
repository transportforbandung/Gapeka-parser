"""Do steep sections get (1) lower timetable speed limits, or (2) extra time beyond the limits? Uphill vs downhill."""
import sqlite3
from collections import defaultdict
import numpy as np
import physics_model as pm
from runtime_model import train_class

db = sqlite3.connect("/home/user/Gapeka-parser/gapeka.db")
svc = dict(db.execute("select no,service from trains"))
rec = []
for no, service in svc.items():
    rows = db.execute("select station,kind,arr_s,dep_s,vmax from stops where train_no=? order by seq", (no,)).fetchall()
    for x, z in zip(rows, rows[1:]):
        # single section, train passing at both ends with printed times: pure running, no start/stop
        if x[1] != "pass" or z[1] != "pass" or x[3] is None or z[3] is None or not x[4]:
            continue
        T = (z[3] - x[3]) % 86400
        if not (30 <= T <= 3600):
            continue
        p, rev = pm._oriented(x[0], z[0])
        if p is None or p.get("h_a") is None or p.get("h_b") is None or p.get("derived"):
            continue
        L = p["km"]
        dh = (p["h_b"] - p["h_a"]) * (-1 if rev else 1)
        grade = dh / (L * 1000) * 1000                     # per mil, + = uphill in travel direction
        pr = pm.profile(x[0], z[0], train_class(service))
        if pr is None: continue
        Lp, umum, rs, _ = pr
        # time at the speed cap (no acceleration needed when passing at both ends; cap varies along the section)
        n = max(1, int(round(Lp * 1000 / pm.DX)))
        cap = np.full(n, float(min(x[4], umum or x[4])))
        for o0, o1, rv, tags, down in rs:
            t = [s for s in __import__("re").split(r"[\s\-/]+", tags) if s]
            if len(t) == 1 and not t[0].startswith("hu" if down else "hi"): continue
            i0 = int(o0 * 1000 / pm.DX); cap[i0:max(int(o1 * 1000 / pm.DX), i0 + 1)] = np.minimum(cap[i0:max(int(o1 * 1000 / pm.DX), i0 + 1)], rv)
        t_cap = float(np.sum(pm.DX / (cap / 3.6)))
        rec.append(dict(grade=grade, ratio=T / t_cap, extra=T - t_cap, vmax=x[4], umum=umum, L=L, cls=train_class(service),
                        pair=f"{x[0]}-{z[0]}"))
print(f"single-section pass->pass legs with elevations: {len(rec)}")
bins = [(-99, -15), (-15, -10), (-10, -5), (-5, -2), (-2, 2), (2, 5), (5, 10), (10, 15), (15, 99)]
for cls in ("passenger", "freight"):
    print(f"\n{cls.upper()}: GAPEKA time / time at the speed limit (1.00 = runs at the limit), by gradient in travel direction")
    print(f"  {'gradient permil':>16s} {'n':>5s} {'time ratio':>11s} {'extra s/km':>11s} {'vmax < Umum':>12s}")
    for lo, hi in bins:
        v = [r for r in rec if r["cls"] == cls and lo <= r["grade"] < hi]
        if len(v) < 15: continue
        print(f"  {f'{lo}..{hi}':>16s} {len(v):5d} {np.median([r['ratio'] for r in v]):11.2f} {np.median([r['extra'] / r['L'] for r in v]):11.1f} "
              f"{np.mean([bool(r['umum']) and r['vmax'] < r['umum'] for r in v]):12.0%}")
