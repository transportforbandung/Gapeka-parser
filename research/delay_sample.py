"""Sample test for delayed priority trains: the 30 lowest-numbered intercity trains each start 15 min late.
   python research/delay_sample.py dispatch   (planner.dispatch: the affected trains re-planned together, current)
   python research/delay_sample.py edit       (planner.plan_edit: one train + chain, accepted or not as a whole)
Prints how late the trains end (median / p90 / max), the total delay of all other trains and the run time."""
import gzip, json, re, statistics, sys, time
sys.path.insert(0, "/home/user/Gapeka-parser")
import planner

mode = sys.argv[1] if len(sys.argv) > 1 else "dispatch"
pkg = json.load(gzip.open("/home/user/Gapeka-parser/dist/gapeka-2026/package.json.gz"))
tl = planner.Timeline(pkg)
P = planner.Planner(pkg, tl)
T = {t["no"]: t for t in pkg["trains"]}
ic = sorted([t for t in pkg["trains"] if t["type"] == "intercity" and t["no"] in tl.trains and t["stops"][0][3] is not None],
            key=lambda t: int(re.match(r"\d+", t["no"]).group()))[:30]
fn = planner.dispatch if mode == "dispatch" else planner.plan_edit
ends, others, secs = [], [], []
for t in ic:
    t0 = time.time()
    out, info = fn(pkg, tl, P, t, t["stops"][0][3] + 900, printed_from=t)
    secs.append(time.time() - t0)
    ends.append((out[t["no"]][0][-1][2] - t["stops"][-1][2]) / 60)
    others.append(sum(max(0, (r[-1][2] - T[n]["stops"][-1][2]) / 60) for n, (r, w) in out.items()
                      if n != t["no"] and r[-1][2] and T[n]["stops"][-1][2]))
q = sorted(ends)
print(f"{mode}: n {len(q)}, train itself ends late median {statistics.median(q):.0f} min, p90 {q[int(.9 * (len(q) - 1))]:.0f}, "
      f"max {q[-1]:.0f}, within 30 min {sum(x <= 30 for x in q)}; other trains total delay median "
      f"{statistics.median(others):.0f} min, max {max(others):.0f}; time median {statistics.median(secs):.0f} s, max {max(secs):.0f} s")
