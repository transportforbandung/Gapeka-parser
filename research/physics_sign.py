"""Is the physics model faster or slower than GAPEKA? Per train (from its first printed departure, printed dwell at
its stops kept): (1) running time alone, no other trains (replay chain); (2) the re-planning test with physics: end
difference split into running-time difference and the waits the planner adds. Sign: model - GAPEKA (minus = the
model is faster = GAPEKA allows more time)."""
import gzip, json, sys
from collections import defaultdict
import numpy as np
sys.path.insert(0, "/home/user/Gapeka-parser")
import planner, replay
pkg = json.load(gzip.open("/home/user/Gapeka-parser/dist/gapeka-2026/package.json.gz"))
T = {t["no"]: t for t in pkg["trains"]}
legs, chains = replay.replay(pkg, types=("krl", "commuter", "intercity"))
by = defaultdict(list)
for no, out in chains.items():
    last = out[-1]
    rows = T[no]["stops"]
    pr = rows[last[0]][2] if rows[last[0]][2] is not None else rows[last[0]][3]
    if last[1] is not None and pr is not None:
        dur = (pr - rows[out[0][0]][3]) / 3600
        by[T[no]["type"]].append(((last[1] - pr) / 60, dur))
print("(1) running time alone (no other trains), model - GAPEKA at the last timed point:")
for typ, v in by.items():
    d = np.array([x[0] for x in v]); h = np.array([x[1] for x in v])
    print(f"  {typ:9s} n {len(d)}: median {np.median(d):+.1f} min, mean {np.mean(d):+.1f} min, faster than GAPEKA in "
          f"{np.mean(d < -0.5):.0%}, slower in {np.mean(d > 0.5):.0%}; per hour of trip {np.sum(d) / np.sum(h):+.2f} min")
e = np.array([x["err"] for x in legs if not x["noted"]]) / 60
print(f"  per leg (no notes): median {np.median(e):+.2f} min, mean {np.mean(e):+.2f}; model faster in {np.mean(e < 0):.0%}")
tl = planner.Timeline(pkg); P = planner.Planner(pkg, tl)
gw = defaultdict(set)
for w in pkg["waits"]:
    if w.get("waiter"):
        gw[w["waiter"]].add(w["station"])
res = defaultdict(lambda: ([], [], []))
for t in pkg["trains"]:
    if t["type"] not in ("intercity", "commuter", "krl") or t["no"] not in tl.trains:
        continue
    stops = [list(r) for r in t["stops"]]
    for r in stops:
        if r[0] in gw[t["no"]] and r[1] == "stop" and r[2] is not None and r[3] is not None:
            r[3] = r[2] + 60
    rows, waits = P.plan(dict(t, stops=stops, notes={}), stops[0][3], printed_times=False, printed_from=t)
    if rows[-1][2] is None or t["stops"][-1][2] is None:
        continue
    end = (rows[-1][2] - t["stops"][-1][2]) / 60
    w = sum(x[3] for x in waits) / 60
    cut = sum((r[3] - r[2]) - 60 for r in t["stops"] if r[0] in gw[t["no"]] and r[1] == "stop"
              and r[2] is not None and r[3] is not None) / 60
    a, b, c = res[t["type"]]
    a.append(end); b.append(w); c.append(end - w + cut)
print("(2) re-planning test with physics: end difference = running time + planner waits - GAPEKA waits:")
for typ, (a, b, c) in res.items():
    a, b, c = map(np.array, (a, b, c))
    print(f"  {typ:9s} n {len(a)}: end median {np.median(a):+.1f} mean {np.mean(a):+.1f} min (late > 2 min {np.mean(a > 2):.0%}, "
          f"early < -2 min {np.mean(a < -2):.0%}); planner waits mean {np.mean(b):.1f} min; running time alone mean "
          f"{np.mean(c):+.1f} min")
