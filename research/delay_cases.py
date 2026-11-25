"""Delayed-train cases for the simulator (planner.plan_edit): a scheduled train starts N minutes late; prints whether
holding other trains was accepted, gain / weighted cost (min), how late it ends, how many trains were held and the
worst delays among them. Bima (7) was ~10 h late at +15 min before the Nov 2026 fixes.
Run: python research/delay_cases.py [train:minutes ...]"""
import gzip, json, sys, time
sys.path.insert(0, "/home/user/Gapeka-parser")
import planner

cases = [a.split(":") for a in sys.argv[1:]] or [["7", "5"], ["7", "15"], ["7", "30"], ["54", "15"], ["8", "20"],
                                                  ["121", "15"], ["2535", "10"], ["1008", "15"]]
pkg = json.load(gzip.open("/home/user/Gapeka-parser/dist/gapeka-2026/package.json.gz"))
tl = planner.Timeline(pkg)
P = planner.Planner(pkg, tl)
T = {t["no"]: t for t in pkg["trains"]}
for no, dl in cases:
    t, t0 = T[no], time.time()
    out, info = planner.plan_edit(pkg, tl, P, t, t["stops"][0][3] + int(dl) * 60, printed_from=t)
    late = sorted((round((r[-1][2] - T[n]["stops"][-1][2]) / 60), n) for n, (r, w) in out.items()
                  if n != no and r[-1][2] and T[n]["stops"][-1][2])
    print(f"{no} +{dl} min: holding accepted {info['accepted']}, gain {round(info['gain_s'] / 60)} min, weighted cost "
          f"{round(info['cost_s'] / 60)} min, ends {round((out[no][0][-1][2] - t['stops'][-1][2]) / 60)} min late, "
          f"held {len(info['displaced'])}, worst others {late[-3:]} ({time.time() - t0:.0f} s)")
