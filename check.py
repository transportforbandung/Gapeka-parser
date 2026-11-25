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
          "package_bytes": 1_500_000}
EXPORTS = [  # (file name, command)
    ("cl_bandung_even.svg", ["export_poster.py", "386-431", "--even"]),
    ("cl_bandung_odd.svg", ["export_poster.py", "386-431", "--odd"]),
    ("siliwangi_odd.svg", ["export_poster.py", "351-356", "--odd"]),
    ("siliwangi_even.svg", ["export_poster.py", "351-356", "--even"]),
    ("headway_bd.svg", ["export_headway.py", "BD", "--service", "Commuter Line Bandung Raya,Commuter Line Garut"]),
    ("trains_bandung.csv", ["export_trains.py", "386-431"]),
]
failed = []


def result(ok, text):
    print(("  ok    " if ok else "  FAIL  ") + text)
    if not ok:
        failed.append(text)


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
    print("\nall checks passed" if not failed else f"\n{len(failed)} check(s) failed")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
