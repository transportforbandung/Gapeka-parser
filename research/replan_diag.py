"""Re-planning test diagnosis: GAPEKA waits the planner misses (what the planner did there) and planner waits GAPEKA
does not have (kind, track, other train type)."""
import gzip, json, sys
from collections import Counter, defaultdict
sys.path.insert(0, "/home/user/Gapeka-parser")
import planner
pkg = json.load(gzip.open("/home/user/Gapeka-parser/dist/gapeka-2026/package.json.gz"))
tl = planner.Timeline(pkg); P = planner.Planner(pkg, tl)
T = {t["no"]: t for t in pkg["trains"]}
gw = defaultdict(dict)
for w in pkg["waits"]:
    if w.get("waiter"):
        o = [n for n in w["trains"] if n and n != w["waiter"]]
        gw[w["waiter"]][w["station"]] = (o[0] if o else None, w["kind"])
miss, extra = Counter(), Counter()
mex, eex = defaultdict(list), defaultdict(list)
for t in pkg["trains"]:
    if t["type"] not in sys.argv[1:] or t["no"] not in tl.trains:
        continue
    stops = [list(r) for r in t["stops"]]
    for r in stops:
        if r[0] in gw[t["no"]] and r[1] == "stop" and r[2] is not None and r[3] is not None:
            r[3] = r[2] + 60
    rows, waits = P.plan(dict(t, stops=stops, notes={}), stops[0][3], printed_times=True, printed_from=t)
    pw = {w[0]: w for w in waits if w[3] >= 30}
    for st, (o, kind) in gw[t["no"]].items():
        if st not in pw:
            dbl = any(tl.block_double.get((st, r[0])) or tl.block_double.get((r[0], st)) for r in rows)
            k = (kind, "double" if dbl else "single")
            miss[k] += 1
            mex[k].append((t["no"], st, o))
    for st, w in pw.items():
        if st not in gw[t["no"]]:
            dbl = any(tl.block_double.get((st, r[0])) or tl.block_double.get((r[0], st)) for r in rows)
            k = (w[2], "double" if dbl else "single", T.get(w[1], {}).get("type"))
            extra[k] += 1
            eex[k].append((t["no"], st, w[1], round(w[3] / 60, 1)))
print("GAPEKA waits the planner misses:")
for k, n in miss.most_common(8):
    print("  ", n, k, mex[k][:6])
print("planner waits GAPEKA does not have:")
for k, n in extra.most_common(8):
    print("  ", n, k, eex[k][:6])
