"""Regression checks for the pipeline outputs.

    python check.py                 # check gapeka.db / infra.db / rules.db in this folder
    python check.py --run           # rebuild all three into a temporary folder first (8-10 min), then check those
    python check.py --run --pdf NEW.pdf   # the same with another GAPEKA PDF (a new edition)
    python check.py --update-baseline   # accept the current TfB exports as the new baseline (after a wanted change)

Checks (a failed check prints FAIL and the script exits with code 1):
  1. printed GAPEKA times are never replaced by estimates
  2. every halt has an estimated time; KA Dinas trips are timed
  3. every timetable station pair has infrastructure data (infra.db QA)
  4. running-time model: held-out legs > 1 min off stay at or below the limit
  5. halt times vs KAI's published times stay at or above the limits per region
  6. the TfB exports (posters, headway bar, train matrix) are identical to the baseline in tests/baseline/
"""
import filecmp
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
BASE = os.path.join(HERE, "tests", "baseline")
LIMITS = {"model_over_1min": 0.05,
          "halts": {"bandung": (0.75, 0.98), "jabodetabek (held-out trains)": (0.85, 0.98), "surabaya": (0.80, 0.98)},
          "package_bytes": 1_500_000,
          # re-planning test (25 Nov 2026, margin 5 min: intercity 90 / 96 / 69 %, commuter 89 / 100 / 50 %, krl 98 / 99 / 63 %)
          # + station tracks by direction / length, release rule, turnaround (25 Nov 2026): intercity 79 / 96 / 92 %,
          # commuter 73 / 100 / 93 %, KRL 71 / 98 / 98 %
          # block-point rule (25 Nov 2026): intercity 68 / 98 / 94 %, commuter 48 / 100 / 92 %, KRL 55 / 98 / 98 %
          # (same station / clean / end); KRL same station dropped from 63 % (old fixed gaps): GAPEKA keeps more
          # distance than the signals require at e.g. Tambun (research/block_clear.py)
          # + 2 min dispatcher gap on double track (user): intercity 74 / 98 / 94 %, commuter 56 / 100 / 94 %,
          # KRL 67 / 98 / 98 %
          "block_rule_broken": 5,         # GAPEKA pairs breaking the double-track block rule (3 single pairs, Nov 2026)
          "replan": {"intercity": dict(end_within_2min=0.89, no_wait_trains_clean=0.93, same_station=0.76),
                     "commuter": dict(end_within_2min=0.90, no_wait_trains_clean=0.97, same_station=0.70),
                     "krl": dict(end_within_2min=0.95, no_wait_trains_clean=0.95, same_station=0.68)},
          "station_exemptions": 60}       # printed calls breaking the station check (43, Nov 2026)
EXPORTS = [  # (file name, command)
    ("cl_bandung_even.svg", ["export_poster.py", "386-431", "--even"]),
    ("cl_bandung_odd.svg", ["export_poster.py", "386-431", "--odd"]),
    ("siliwangi_odd.svg", ["export_poster.py", "351-356", "--odd"]),
    ("siliwangi_even.svg", ["export_poster.py", "351-356", "--even"]),
    ("headway_bd.svg", ["export_headway.py", "BD", "--service", "Commuter Line Bandung Raya,Commuter Line Garut",
                        "--no-png"]),
    ("trains_bandung.csv", ["export_trains.py", "386-431"]),
]
failed = []


def result(ok, text):
    print(("  ok    " if ok else "  FAIL  ") + text)
    if not ok:
        failed.append(text)


def planner_origins(pkg):
    """every origin value used in the package"""
    o = {r[5] for t in pkg["trains"] for r in t["stops"] if r[5]}
    for s_ in pkg["sections"]:
        o |= {r[5] for r in s_.get("restrictions", [])} | {b[5] for b in s_.get("block_signals", [])}
    o |= {t[9] for L in pkg["layouts"] for t in L["tracks"]}
    o |= {t["consist"]["origin"] for t in pkg["trains"] if t.get("consist")}
    return o | {"fitted", "derived", "simulated"}


def exports(db, folder):
    os.makedirs(folder, exist_ok=True)
    for name, cmd in EXPORTS:
        subprocess.run([sys.executable, os.path.join(HERE, cmd[0])] + cmd[1:] + ["--db", db, "-o", os.path.join(folder, name)],
                       check=True, cwd=HERE, capture_output=True)


def main():
    args = sys.argv[1:]
    folder = HERE
    if "--run" in args:
        folder = tempfile.mkdtemp()
        print(f"building into {folder} ...")
        pdf = args[args.index("--pdf") + 1] if "--pdf" in args else os.path.join(HERE, "GAPEKA-JAWA-2026.pdf")
        subprocess.run([sys.executable, os.path.join(HERE, "gapeka_pipeline.py"), os.path.join(folder, "gapeka.db"),
                        os.path.abspath(pdf)], check=True, cwd=HERE)
    g = sqlite3.connect(os.path.join(folder, "gapeka.db"))
    infra = sqlite3.connect(os.path.join(folder, "infra.db"))
    rules = sqlite3.connect(os.path.join(folder, "rules.db"))

    if "--update-baseline" in args:
        exports(os.path.join(folder, "gapeka.db"), BASE)
        print(f"baseline exports written to {BASE}")
        return

    print("1. printed times")
    bad = g.execute("""select count(*) from stops s join schedule sc on sc.train_no=s.train_no and sc.seq=s.seq
                       where (s.arr_s is not null and sc.arr_s is not s.arr_s) or (s.dep_s is not null and sc.dep_s is not s.dep_s)""").fetchone()[0]
    result(bad == 0, f"printed times replaced by estimates: {bad}")
    print("2. estimates")
    n_h = g.execute("select count(*) from stops where kind='halt'").fetchone()[0]
    n_e = g.execute("select count(*) from stops s join halt_est h on h.train_no=s.train_no and h.seq=s.seq where s.kind='halt'").fetchone()[0]
    result(n_h == n_e, f"halts with a time: {n_e} of {n_h}")
    n_d = g.execute("select count(*) from trains where type='dinas'").fetchone()[0]
    n_un = g.execute("""select count(distinct sc.train_no) from schedule sc join trains t on t.no=sc.train_no
                         where t.type='dinas' and sc.arr_s is null and sc.dep_s is null""").fetchone()[0]
    result(n_un == 0, f"KA Dinas trips with an untimed stop: {n_un} of {n_d}")
    print("3. infrastructure coverage")
    unc = infra.execute("select count(*) from qa where check_name='timetable pair without a sheet section'").fetchone()[0]
    result(unc == 0, f"timetable station pairs without infrastructure data: {unc}")
    print("4. running-time model (held-out legs without notes)")
    st = dict(rules.execute("select key, value from fit_stats"))
    full = json.loads(st["full"])
    result(full[2] <= LIMITS["model_over_1min"], f"> 1 min off: {full[2]:.1%} (limit {LIMITS['model_over_1min']:.0%}); "
                                                 f"< 0.5 min {full[0]:.1%}")
    print("5. halt times vs KAI")
    hv = json.loads(st["halts_vs_kai"])
    for region, (ex_min, w1_min) in LIMITS["halts"].items():
        ex, w1, n = hv.get(region, (0, 0, 0))
        result(ex >= ex_min and w1 >= w1_min, f"{region}: exact {ex:.0%} (limit {ex_min:.0%}), within 1 min {w1:.0%} "
                                              f"(limit {w1_min:.0%}), n {n}")
    print("6. TfB exports vs tests/baseline/")
    if not os.path.isdir(BASE):
        result(False, "no baseline: run  python check.py --update-baseline")
    else:
        tmp = tempfile.mkdtemp()
        exports(os.path.join(folder, "gapeka.db"), tmp)
        for name, _ in EXPORTS:
            same = filecmp.cmp(os.path.join(tmp, name), os.path.join(BASE, name), shallow=False)
            result(same, f"{name} {'identical' if same else 'DIFFERS (compare with tests/baseline/' + name + ')'}")
        shutil.rmtree(tmp)
    print("7. simulator data package (dist/)")
    import glob
    import package_check
    found = sorted(glob.glob(os.path.join(folder, "dist", "*", "manifest.json")))
    if not found:
        result(False, "no package: run  python export_package.py")
    for mpath in found:
        pfolder = os.path.dirname(mpath)
        pkg, man = package_check.load(pfolder)
        try:
            import export_package
            export_package.validate(pkg)
            result(True, f"{os.path.basename(pfolder)}: schema valid")
        except Exception as e:                         # noqa: BLE001 (report any schema error as a failed check)
            result(False, f"{os.path.basename(pfolder)}: schema: {str(e).splitlines()[0]}")
        probs, info = package_check.problems(pkg)
        result(not probs, f"references / time order: {len(probs)} problems {probs[:3]}")
        result(man["bytes"] <= LIMITS["package_bytes"], f"size {man['bytes'] / 1e6:.2f} MB (limit "
                                                        f"{LIMITS['package_bytes'] / 1e6:.1f} MB)")
        n_t = g.execute("select count(*) from trains").fetchone()[0]
        n_s = infra.execute("select count(*) from sections").fetchone()[0]
        result(man["counts"]["trains"] == n_t and man["counts"]["sections"] == n_s,
               f"trains {man['counts']['trains']} / {n_t}, sections {man['counts']['sections']} / {n_s} (package / databases)")
        sha = infra.execute("select value from meta where key = 'pdf_sha256'").fetchone()[0]
        result(man["pdf_sha256"] == sha, "package built from the same PDF as infra.db")
        import gzip
        import replay
        legs, trains_out = replay.replay(pkg)
        b = replay.bands([x["err"] for x in legs if not x["noted"] and x["type"] != "freight"])
        result(b[2] <= LIMITS["model_over_1min"], f"replay from the package: passenger legs without notes > 1 min off "
                                                  f"{b[2]:.1%} (limit {LIMITS['model_over_1min']:.0%}), < 0.5 min {b[0]:.1%}")
        # double-track block rule vs GAPEKA (signal_infer.py): broken pairs left after the printed, manual and inferred
        # signals; a new edition with signals missing on the sheet shows up here (and in infra.db qa)
        import planner
        import signal_infer
        tl = planner.Timeline(pkg)
        tt = {t["no"]: t["type"] for t in pkg["trains"]}
        broken = {k: len(v) for k in tl.block_base if tl.block_double.get(k)
                  for v in [signal_infer._violations(tl, tt, k, planner.knot_time)] if v}
        n_inf = infra.execute("select count(*) from block_signals where kind like 'inferred:%'").fetchone()[0]
        result(sum(broken.values()) <= LIMITS["block_rule_broken"],
               f"double-track block rule: GAPEKA pairs breaking it {sum(broken.values())} (limit "
               f"{LIMITS['block_rule_broken']}) {sorted(broken.items())[:5]}; inferred signals in infra.db {n_inf}")
        P_ = planner.Planner(pkg, tl)
        n_ex = len(pkg["rules"]["headways"].get("station_exemptions", []))
        pushed = sum(1 for t in pkg["trains"] if t["no"] in tl.trains and t["stops"][0][3] is not None
                     and P_.ready_time(t["no"], t["stops"][0][3]) > t["stops"][0][3] + 1)
        warn = planner.capacity_warnings(pkg, tl)
        result(n_ex <= LIMITS["station_exemptions"] and pushed == 0 and not warn,
               f"station check: printed calls tolerated {n_ex} (limit {LIMITS['station_exemptions']}); printed trains "
               f"held by their unit's turnaround {pushed}; sections over capacity K {len(warn)}")
        # phase 4: every value the app shows has an Indonesian and an English text; templates use the same placeholders
        import re as _re
        tx = pkg.get("texts", {})
        need = [f"train.type.{t}" for t in {t["type"] for t in pkg["trains"]}] + \
               [f"stop.kind.{k}" for k in {r[1] for t in pkg["trains"] for r in t["stops"]}] + \
               [f"origin.{o}" for o in planner_origins(pkg)] + [f"wait.{k}" for k in planner.WAIT_KINDS] + \
               [f"msg.{k}" for k in planner.WAIT_KINDS] + \
               [f"block.{s_['block_system']}" for s_ in pkg["sections"] if s_.get("block_system")]
        missing = sorted({k for k in need if k not in tx or not tx[k].get("id") or not tx[k].get("en")})
        bad = [k for k, v in tx.items() if set(_re.findall(r"{(\w+)}", v["id"])) != set(_re.findall(r"{(\w+)}", v["en"]))]
        disc = pkg["meta"].get("disclaimer", {})
        result(not missing and not bad and all(disc.get(l) and "{" not in disc[l] for l in ("id", "en")),
               f"texts id / en: {len(tx)} keys, missing {missing[:5]}, placeholder mismatch {bad[:5]}, disclaimer "
               f"{'id + en' if disc.get('id') and disc.get('en') else 'MISSING'}")
        gpath = os.path.join(pfolder, "replay.json.gz")
        same = os.path.exists(gpath) and json.load(gzip.open(gpath))["trains"] == json.loads(json.dumps(trains_out))
        result(same, "golden replay.json.gz matches a fresh replay of the package")
    print("8. re-planning test (planner.py: each train re-planned from its printed departure, printed running times)")
    import planner
    for mpath in found[:1]:
        pkg, _ = package_check.load(os.path.dirname(mpath))
        res, _ = planner.replan_test(pkg, ("intercity", "commuter", "krl"))
        for typ, lim in LIMITS["replan"].items():
            r = res.get(typ, {})
            ok = r.get("end_within_2min", 0) >= lim["end_within_2min"] and \
                r.get("no_wait_trains_clean", 0) >= lim["no_wait_trains_clean"] and \
                r.get("same_station", 0) >= lim["same_station"]
            result(ok, f"{typ}: end within 2 min {r.get('end_within_2min', 0):.0%} (limit {lim['end_within_2min']:.0%}), "
                       f"trains without waits kept clean {r.get('no_wait_trains_clean', 0):.0%} "
                       f"(limit {lim['no_wait_trains_clean']:.0%}), GAPEKA waits at the same station "
                       f"{r.get('same_station', 0):.0%} (limit {lim['same_station']:.0%})")
        import planner_golden
        for name, ok in planner_golden.matches(pkg, os.path.dirname(mpath)).items():
            result(ok, f"golden {name}.json.gz matches a fresh run of the Python planner")
    print("9. browser engine (engine/, TypeScript): reproduces the golden replay and planner files exactly")
    eng_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "engine")
    if not os.path.isdir(os.path.join(eng_dir, "node_modules")):
        print("  skip  engine/node_modules missing (run: npm ci --prefix engine)")
    else:
        import subprocess
        r = subprocess.run(["npm", "test", "--silent"], cwd=eng_dir, capture_output=True, text=True)
        lines = [ln[2:] for ln in r.stdout.splitlines() if ln.startswith("# ") and not ln.startswith("# Subtest")
                 and ln[2:].split(" ")[0] not in ("tests", "suites", "pass", "fail", "cancelled", "skipped", "todo",
                                                    "duration_ms")]
        for ln in lines:
            print("        " + ln)
        result(r.returncode == 0, "npm test in engine/ (golden replay, leg times, re-planning test, dispatch)"
               + ("" if r.returncode == 0 else ": " + (r.stderr or r.stdout)[-400:]))
        has_pw = subprocess.run(["node", "-e", "require('playwright')"], cwd=eng_dir, capture_output=True).returncode == 0
        if not has_pw:
            print("  skip  browser smoke test (playwright not installed)")
        else:
            r = subprocess.run(["npm", "run", "smoke", "--silent"], cwd=eng_dir, capture_output=True, text=True)
            tail = [ln for ln in r.stdout.splitlines() if ln.strip()][-1:] or [r.stderr[-300:]]
            result(r.returncode == 0, f"browser smoke test (bundled worker in Chromium vs dispatch golden): {tail[0]}")
    print("\nall checks passed" if not failed else f"\n{len(failed)} check(s) failed")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
