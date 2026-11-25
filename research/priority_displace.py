"""Priority by train number with displacement (user): a lower-numbered train never waits at a station it does not
serve; the higher-numbered scheduled train is held before the block instead and re-planned (planner.plan_priority).
Test on GAPEKA's crossings where the LOWER number waits (57): the waiter's stop there is treated as a pass (as if it
did not serve the station), everything else as printed; compared with plain planning (the planned train yields
to all). Prints: waiter still waits there, displaced trains and their end delay vs GAPEKA.
Run: python research/priority_displace.py"""
import gzip, json, re, statistics, sys
sys.path.insert(0, "/home/user/Gapeka-parser")
import planner

pkg = json.load(gzip.open("/home/user/Gapeka-parser/dist/gapeka-2026/package.json.gz"))
tl = planner.Timeline(pkg)
P = planner.Planner(pkg, tl)
T = {t["no"]: t for t in pkg["trains"]}
num = lambda n: int(re.match(r"\D*(\d+)", n).group(1))
cases = []
for w in pkg["waits"]:
    if w["kind"].startswith("cross") and w.get("waiter"):
        o = [n for n in w["trains"] if n and n != w["waiter"]]
        if o and o[0] in T and w["waiter"] in T and num(w["waiter"]) < num(o[0]) and T[w["waiter"]]["type"] != "dinas" \
                and w["waiter"] in tl.trains:
            cases.append((w["waiter"], o[0], w["station"]))
gw = {}
for w in pkg["waits"]:
    if w.get("waiter"):
        gw.setdefault(w["waiter"], set()).add(w["station"])
res = dict(cases=0, plain_wait=0, prio_wait=0, displaced=[], delays=[], end_waiter=[])
for no, other, st in cases:
    t = T[no]
    stops = [list(r) for r in t["stops"]]
    for r in stops:
        if r[0] == st:
            r[1] = "pass"
    tt = dict(t, stops=stops, notes={})
    res["cases"] += 1
    rows, waits = P.plan(tt, stops[0][3], printed_times=True, printed_from=t)
    res["plain_wait"] += any(w[0] == st and w[3] >= 30 for w in waits)
    out = planner.plan_priority(pkg, tl, P, tt, stops[0][3], printed_from=t)
    rows, waits = out[no]
    res["prio_wait"] += any(w[0] == st and w[3] >= 30 for w in waits)
    if rows[-1][2] is not None and t["stops"][-1][2] is not None:
        res["end_waiter"].append((rows[-1][2] - t["stops"][-1][2]) / 60)
    for d, (r2, w2) in out.items():
        if d == no:
            continue
        res["displaced"].append(d)
        g = T[d]["stops"][-1][2]
        if r2[-1][2] is not None and g is not None:
            res["delays"].append((r2[-1][2] - g) / 60)
    if len(res["displaced"]) and res["cases"] <= 6:
        print(f"  {no} at {st} (GAPEKA: waits for {other}): displaced {[d for d in out if d != no]}, "
              f"waits {[(w[0], w[1], round(w[3] / 60, 1)) for w in waits if w[3] >= 30]}")
print(f"cases {res['cases']}: lower number still waits there: plain planning {res['plain_wait']}, with priority "
      f"{res['prio_wait']}")
print(f"waiter end vs GAPEKA (min): median {statistics.median(res['end_waiter']):.1f}")
print(f"displaced trains {len(res['displaced'])}; their end vs GAPEKA (min): median "
      f"{statistics.median(res['delays']):.1f}, max {max(res['delays']):.1f}, within 5 min "
      f"{sum(x <= 5 for x in res['delays']) / len(res['delays']):.0%}")
