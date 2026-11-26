"""Integrity checks of a simulator data package (used by check.py; also: python package_check.py [dist/gapeka-2026])."""
import gzip
import json
import os
import sys


def load(folder):
    return json.load(gzip.open(os.path.join(folder, "package.json.gz"))), json.load(open(os.path.join(folder, "manifest.json")))


def problems(pkg):
    """list of (check, detail); empty = all good"""
    out = []
    st = pkg["stations"]
    trains = {t["no"]: t for t in pkg["trains"]}
    if len(trains) != len(pkg["trains"]):
        out.append(("duplicate train numbers", len(pkg["trains"]) - len(trains)))
    for s in pkg["sections"]:
        for c in (s["a"], s["b"]):
            if c not in st:
                out.append(("section station unknown", f"{s['a']}-{s['b']}: {c}"))
    secs = {frozenset((s["a"], s["b"])) for s in pkg["sections"]}
    untimed, backwards = 0, []
    for t in pkg["trains"]:
        last = None
        for row in t["stops"]:
            code, kind, a, d = row[:4]
            if code not in st:
                out.append(("stop station unknown", f"{t['no']}: {code}"))
            if a is None and d is None:
                untimed += 1
            for x in (a, d):
                if x is not None:
                    if last is not None and x < last:
                        backwards.append(f"{t['no']} at {code}")
                    last = x
    if backwards:
        out.append(("times run backwards", f"{len(backwards)}: {backwards[:5]}"))
    for w in pkg["waits"]:
        if w["station"] not in st:
            out.append(("wait station unknown", w["station"]))
        for n in w["trains"] + [w.get("waiter"), w.get("overtaker")]:
            if n and n not in trains and n not in w.get("unknown_trains", []):
                out.append(("wait train unknown", n))
    for dty in pkg["duties"]:
        miss = [n for n in dty["trains"] if n not in trains]
        if miss and dty["kind"] != "loco":
            out.append(("duty train unknown", f"{dty['id']}: {miss}"))
    for c in pkg["capacity"]:
        for code in c.get("path", []):
            if code not in st:
                out.append(("capacity path station unknown", code))
    unk = sorted({n for w in pkg["waits"] for n in w.get("unknown_trains", [])})
    return out, dict(untimed_stops=untimed, note_trains_not_in_timetable=unk)


if __name__ == "__main__":
    pkg, man = load(sys.argv[1] if len(sys.argv) > 1 else "dist/gapeka-2026")
    p, info = problems(pkg)
    print(man["bytes"], "bytes;", info)
    for x in p[:40]:
        print(x)
    print(len(p), "problems")
