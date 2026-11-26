"""Re-planning test (planner.replan_test): prints the scores per train type.
    python research/replan_test.py [--types intercity,commuter,krl,freight] [--physics]"""
import gzip, json, sys, time
sys.path.insert(0, "/home/user/Gapeka-parser")
import planner

args = sys.argv[1:]
types = tuple((args[args.index("--types") + 1] if "--types" in args else "intercity,commuter,krl").split(","))
physics = "--physics" in args
pkg = json.load(gzip.open("/home/user/Gapeka-parser/dist/gapeka-2026/package.json.gz"))
t0 = time.time()
res, ex = planner.replan_test(pkg, types, physics, examples=15)
print(f"re-planning test ({'physics' if physics else 'printed running times'}), {time.time() - t0:.0f} s")
for typ, s in res.items():
    print(f"  {typ:9s} trains {s['trains']}: GAPEKA waits {s['gapeka_waits']}, planner waits {s['planner_waits']}; "
          f"same station {s['same_station']:.0%}, same station + train {s['same_station_and_train']:.0%}, departure "
          f"there within 1 min {s['departure_within_1min']:.0%}; planner waits not in GAPEKA {s['extra_waits']:.0%}; "
          f"trains without waits kept clean {s['no_wait_trains_clean']:.0%}; end within 2 min {s['end_within_2min']:.0%} "
          f"(mean |diff| {s['end_mean_abs_min']:.1f} min)")
for e in ex:
    print("   ", e[0], e[1], "GAPEKA", e[2], "planner", e[3])
