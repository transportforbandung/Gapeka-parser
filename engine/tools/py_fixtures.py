"""Reference outputs of the Python engine at FULL precision for the TypeScript tests (engine/build/fixtures, not in
git): python3 engine/tools/py_fixtures.py [what ...]   what: legs (the planner goldens are dist/<edition>/replan.json.gz and dispatch.json.gz,
written by planner_golden.py)
legs: replay.Engine.compose and .minimum for every leg between consecutive timed points, and for every leg from each
timed point to the last row with the stops in between turned into passes (other stopping patterns)"""
import gzip
import json
import os
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, ROOT)
import replay  # noqa: E402

OUT = os.path.join(ROOT, "engine", "build", "fixtures")


def legs(pkg):
    eng = replay.Engine(pkg)
    out = []
    for t in pkg["trains"]:
        rows = t["stops"]
        tp = replay.timed_points(rows)
        for p, q in zip(tp, tp[1:]):
            out.append([t["no"], p, q, None, eng.compose(t, rows, p, q), eng.minimum(t, rows, p, q)])
        if len(tp) >= 2:
            alt = [list(r) for r in rows]
            for r in alt[1:-1]:
                if r[1] == "stop":
                    r[1] = "pass"
            out.append([t["no"], tp[0], len(rows) - 1, "passes", eng.compose(t, alt, tp[0], len(rows) - 1),
                        eng.minimum(t, alt, tp[0], len(rows) - 1)])
    return out


if __name__ == "__main__":
    pkg = json.load(gzip.open(os.path.join(ROOT, "dist/gapeka-2026/package.json.gz")))
    os.makedirs(OUT, exist_ok=True)
    for what in sys.argv[1:] or ["legs"]:
        data = globals()[what](pkg)
        with open(os.path.join(OUT, what + ".json"), "w") as fh:
            json.dump(data, fh)
        print(what, len(data))
