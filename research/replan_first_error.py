"""First planner wait that GAPEKA does not have, per re-planned train: kind, track, how long, the other train's
relation (re-planning test setup)."""
import gzip, json, sys
from collections import Counter, defaultdict
sys.path.insert(0, "/home/user/Gapeka-parser")
import planner
pkg = json.load(gzip.open("/home/user/Gapeka-parser/dist/gapeka-2026/package.json.gz"))
tl = planner.Timeline(pkg); P = planner.Planner(pkg, tl)
T = {t["no"]: t for t in pkg["trains"]}
gw = defaultdict(set)
for w in pkg["waits"]:
    if w.get("waiter"):
        gw[w["waiter"]].add(w["station"])
c = Counter(); ex = defaultdict(list)
for t in pkg["trains"]:
    if t["type"] not in ("intercity", "commuter") or t["no"] not in tl.trains:
        continue
    stops = [list(r) for r in t["stops"]]
    for r in stops:
        if r[0] in gw[t["no"]] and r[1] == "stop" and r[2] is not None and r[3] is not None:
            r[3] = r[2] + 60
    rows, waits = P.plan(dict(t, stops=stops, notes={}), stops[0][3], printed_times=True, printed_from=t)
    for w in waits:
        if w[3] < 30 or w[0] in gw[t["no"]]:
            continue
        rel = T.get(w[1], {}).get("type", "?")
        k = (w[2], "double" if any(tl.block_double.get((w[0], r[0])) for r in rows) else "single", rel)
        c[k] += 1
        ex[k].append((t["no"], w[0], w[1], round(w[3] / 60, 1)))
        break
for k, n in c.most_common(12):
    print(n, k, ex[k][:5])
