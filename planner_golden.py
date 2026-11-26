"""Golden output of the Python planner for the browser engine (phase 5, docs/simulator_package.md section 5.4), written
next to the package by the pipeline (step 10) and checked by check.py:

  replan.json.gz    the re-planning test (planner.replan_test, every train type): per train the planned rows and waits,
                    and the stats per type
  dispatch.json.gz  the dispatching re-plan (planner.dispatch) of the delay cases below: every re-planned train's rows
                    and waits, and the info

Full precision (the TypeScript engine must give the same doubles: engine/test/planner.test.ts).
    python planner_golden.py [dist/gapeka-2026] [--write]
"""
import gzip
import json
import os
import sys

import planner

REPLAN_TYPES = ("intercity", "commuter", "krl", "freight")
# (train, minutes late at its origin): research/delay_cases.py, a KRL, freight with the long-train rule, priority chains
DISPATCH_CASES = [("7", 15), ("7", 30), ("54", 15), ("8", 20), ("121", 15), ("29", 15), ("2535", 10), ("1008", 15)]


def replan(pkg):
    per = {}
    stats, _ = planner.replan_test(pkg, REPLAN_TYPES, on_train=lambda no, rows, waits: per.__setitem__(no, [rows, waits]))
    return dict(edition=pkg["meta"]["edition"], types=list(REPLAN_TYPES), stats=stats, trains=per)


def dispatch(pkg):
    tl = planner.Timeline(pkg)
    P = planner.Planner(pkg, tl)
    T = {t["no"]: t for t in pkg["trains"]}
    out = []
    for no, late in DISPATCH_CASES:
        t = T[no]
        plans, info = planner.dispatch(pkg, tl, P, t, t["stops"][0][3] + late * 60, printed_from=t)
        out.append(dict(train=no, late=late, plans={n: [r, w] for n, (r, w) in plans.items()}, info=info))
    return dict(edition=pkg["meta"]["edition"], cases=out)


def _norm(x):
    return json.loads(json.dumps(x))


def write(pkg, folder, data=None):
    data = data or dict(replan=replan(pkg), dispatch=dispatch(pkg))
    paths = []
    for name, d in data.items():
        path = os.path.join(folder, name + ".json.gz")
        with open(path, "wb") as fh:
            with gzip.GzipFile(fileobj=fh, mode="wb", mtime=0, compresslevel=9) as gz:
                gz.write(json.dumps(d, separators=(",", ":")).encode())
        paths.append(path)
    return paths


def matches(pkg, folder, replan_data=None):
    """{name: True / False} the golden files equal a fresh run of the Python planner"""
    out = {}
    for name, fn in (("replan", replan), ("dispatch", dispatch)):
        path = os.path.join(folder, name + ".json.gz")
        fresh = replan_data if (name == "replan" and replan_data is not None) else fn(pkg)
        out[name] = os.path.exists(path) and json.load(gzip.open(path)) == _norm(fresh)
    return out


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    folder = args[0] if args else "dist/gapeka-2026"
    pkg = json.load(gzip.open(os.path.join(folder, "package.json.gz")))
    if "--write" in sys.argv:
        print("wrote", write(pkg, folder))
    else:
        print(matches(pkg, folder))
