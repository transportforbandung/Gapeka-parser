"""Reference engine, phase 2 (docs/simulator_package.md): re-times the official GAPEKA from the simulator package ALONE
(no databases, no numpy), so the browser engine can be checked against it.

    python replay.py [dist/gapeka-2026] [--write]

compose(): running time of one leg between two timed points of a train:
  sim   = sum of leg_times physics s of its sections, each with its own end kinds (pass / stop at a halt or stop /
          terminal at the train's origin / terminus)
  T     = calibration[type] . features + buffers + corrections, as gapeka_model.Model (features: sim, 1, halts in the
          leg, terminus arrival, origin departure, start from a stop, stop at the end, crossing / overtaking roles from
          the train's note symbols at both ends, km on 5-10 and >= 10 permil sections); freight has no calibration of
          its own and uses intercity's (as gapeka_model does)
Two replays:
  legs  : every timed leg on its own, predicted vs printed duration
  trains: each train from its printed first departure; arrival = previous departure + T; at a stop the printed
          dwell is kept; drift = predicted - printed time at each timed point (waits are part of the printed dwell)
--write saves replay.json.gz (golden output for the browser engine: per train the predicted leg durations and the
chained times, 0.1 s) next to the package.
Error sign: predicted - printed; minus = GAPEKA allows more time than the model.
"""
import gzip
import json
import os
import sys
from collections import defaultdict

FEATURES = ["sim", "const", "per_halt", "terminus_arrival", "origin_departure", "start_from_stop", "stop_at_end",
            "start_wait_cross", "start_wait_overtaken", "start_pass_cross", "end_wait_cross", "end_wait_overtaken",
            "end_pass_cross", "km_5_10_permil", "km_10plus_permil"]
PAT = {a + b: i for i, (a, b) in enumerate((a, b) for a in "pst" for b in "pst")}


class Engine:
    def __init__(self, pkg):
        self.pkg = pkg
        m = pkg["model"]
        self.calib = defaultdict(dict)
        for c in m["calibration"]:
            self.calib[c["train_type"]][c["feature"]] = c["value"]
        self.corr = {}
        for kind, a, b, end_kind, t, sec, _ in m["corrections"]:
            key = {"section": ("sec", a, b, t), "halt": ("halt", a, t)}.get(kind, (kind, a, end_kind, t))
            self.corr[key] = sec
        self.lt = {}
        for a, b, var, rc, v, km, ruling, times in pkg["leg_times"]:
            self.lt[(a, b, var, rc, v)] = (km, ruling, times)
        self.variants = defaultdict(list)
        for s in pkg["sections"]:
            if s.get("variant"):
                self.variants[frozenset((s["a"], s["b"]))].append((s["variant"], set(s.get("context", "").split())))
        self.buffers = []
        for b in m["section_buffers"]:
            val = float(str(b["seconds"]).split()[0])
            secs = [tuple(x.split("-")) for x in b["sections"].split()]
            self.buffers.append(dict(kind=b["kind"], sections=secs, value=val, types=b["train_types"].split()))
        for b in m["station_buffers"]:
            self.buffers.append(dict(kind="station_buffer", station=b["station"], side=b["side"], when=b["applies_when"],
                                     value=float(b["seconds"]), types=b["train_types"].split()))

    def variant(self, a, b, ctx):
        """the section variant whose context holds one of the leg's stations, else the default ('')"""
        for var, c in self.variants.get(frozenset((a, b)), []):
            if c & ctx:
                return var
        return ""

    def buffer_s(self, typ, st, secs, start_rest, end_rest, first, last):
        pairs = set(zip(st, st[1:]))
        both = pairs | {(b, a) for a, b in pairs}
        tot = 0.0
        for b in self.buffers:
            if typ not in b["types"]:
                continue
            if b["kind"] == "buffer" and both & set(b["sections"]):
                tot += b["value"]
            elif b["kind"] == "buffer_km":
                want = {frozenset(p) for p in b["sections"]}
                tot += b["value"] * sum(km for (x, y), (km, _) in zip(zip(st, st[1:]), secs) if frozenset((x, y)) in want)
            elif b["kind"] == "station_buffer":
                at, rest, term = (st[0], start_rest, first) if b["side"] == "dep" else (st[-1], end_rest, last)
                if at == b["station"]:
                    w = b["when"]
                    if (w == "terminal" and rest and term) or (w == "stop" and rest) or w == "any":
                        tot += b["value"]
        return tot

    def compose(self, train, rows, p, q):
        """predicted seconds from timed point p to timed point q of train (rows = its stops); None if a section has
        no leg time"""
        typ, rc = train["type"], train.get("run_class", train["type"])
        st = [r[0] for r in rows[p:q + 1]]
        ctx = set(st)
        first, last = p == 0, q == len(rows) - 1
        start_rest, end_rest = rows[p][1] != "pass", rows[q][1] != "pass"
        sim, secs = 0.0, []
        for i in range(p, q):
            a, b, v = rows[i][0], rows[i + 1][0], rows[i][4]
            e = self.lt.get((a, b, self.variant(a, b, ctx), rc, v))
            if e is None:
                return None
            sk = "t" if i == 0 else ("s" if rows[i][1] != "pass" else "p")
            ek = "t" if i + 1 == len(rows) - 1 else ("s" if rows[i + 1][1] != "pass" else "p")
            sim += e[2][PAT[sk + ek]]
            secs.append((e[0], e[1]))
        halts = [r[0] for r in rows[p + 1:q] if r[1] == "halt"]
        notes = train.get("notes", {})

        def role(code, rest):
            s = set(notes.get(code, "").split())
            return [float(rest and "x" in s), float(rest and "=" in s), float((not rest) and bool(s & {"x", "//"}))]
        km5 = sum(km for km, g in secs if g is not None and 5 <= g < 10)
        km10 = sum(km for km, g in secs if g is not None and 10 <= g < 99)
        x = [sim, 1.0, len(halts), float(last), float(first), float(start_rest), float(end_rest)] + \
            role(st[0], start_rest) + role(st[-1], end_rest) + [km5, km10]
        c = self.calib.get(typ) or self.calib["intercity"]
        T = sum(c.get(f, 0.0) * v for f, v in zip(FEATURES, x))
        T += self.buffer_s(typ, st, secs, start_rest, end_rest, first, last)
        T += sum(self.corr.get(("sec", a, b, typ), 0.0) for a, b in zip(st, st[1:]))
        T += sum(self.corr.get(("halt", h, typ), 0.0) for h in halts)
        T += self.corr.get(("dep", st[0], "stop" if start_rest else "pass", typ), 0.0)
        T += self.corr.get(("arr", st[-1], "stop" if end_rest else "pass", typ), 0.0)
        return T


def timed_points(rows):
    """indices of the stops with a printed time that a leg can start / end at (as gapeka_model.timetable_legs)"""
    return [i for i, r in enumerate(rows) if r[5] == "printed" and
            (r[1] in ("stop", "terminal") or (r[1] == "pass" and r[3] is not None))]


def replay(pkg, types=("krl", "commuter", "intercity", "freight")):
    eng = Engine(pkg)
    legs, trains_out = [], {}
    for t in pkg["trains"]:
        if t["type"] not in types:
            continue
        rows = t["stops"]
        tp = timed_points(rows)
        if len(tp) < 2:
            continue
        noted = set(t.get("notes", {}))
        out, clock = [], rows[tp[0]][3]
        out.append([tp[0], None, clock])
        for p, q in zip(tp, tp[1:]):
            t0 = rows[p][3]
            t1 = rows[q][2] if rows[q][1] in ("stop", "terminal") and rows[q][2] is not None else rows[q][3]
            T = eng.compose(t, rows, p, q)
            if t0 is not None and t1 is not None and T is not None and 0 < t1 - t0 < 4 * 3600:
                legs.append(dict(train=t["no"], type=t["type"], err=T - (t1 - t0),
                                 noted=rows[p][0] in noted or rows[q][0] in noted))
            if T is None or clock is None:
                clock = None
                out.append([q, None, None])
                continue
            arr = clock + T
            a_pr, d_pr = rows[q][2], rows[q][3]
            dep = arr + (d_pr - a_pr) if rows[q][1] == "stop" and a_pr is not None and d_pr is not None else \
                (arr if rows[q][1] != "terminal" else None)
            out.append([q, round(arr, 1), None if dep is None else round(dep, 1), round(T, 1)])
            clock = dep
        trains_out[t["no"]] = out
    return legs, trains_out


def bands(errs):
    a = [abs(e) / 60 for e in errs]
    n = len(a) or 1
    return (sum(x < .5 for x in a) / n, sum(.5 <= x <= 1 for x in a) / n, sum(x > 1 for x in a) / n, len(a))


def report(pkg, legs, trains_out):
    print("leg replay (predicted - printed duration; minus = GAPEKA allows more time):")
    for typ in ("krl", "commuter", "intercity", "freight"):
        for lab, sel in (("no notes", False), ("noted", True)):
            e = [x["err"] for x in legs if x["type"] == typ and x["noted"] == sel]
            if e:
                b = bands(e)
                print(f"  {typ:9s} {lab:8s} <0.5 {b[0]:4.0%}  0.5-1 {b[1]:4.0%}  >1 {b[2]:4.0%}  (n {b[3]})")
    allb = bands([x["err"] for x in legs if not x["noted"] and x["type"] != "freight"])
    print(f"  passenger, no notes: <0.5 {allb[0]:.0%} 0.5-1 {allb[1]:.0%} >1 {allb[2]:.0%} (n {allb[3]})")
    T = {t["no"]: t for t in pkg["trains"]}
    drift = defaultdict(list)
    for no, out in trains_out.items():
        rows = T[no]["stops"]
        last = out[-1]
        if last[1] is not None:
            pr = rows[last[0]][2] if rows[last[0]][2] is not None else rows[last[0]][3]
            drift[T[no]["type"]].append((last[1] - pr) / 60)
    print("train replay, drift at the last timed point (min, predicted - printed):")
    for typ, d in drift.items():
        d = sorted(d)
        print(f"  {typ:9s} median {d[len(d) // 2]:+.1f}  p10 {d[len(d) // 10]:+.1f}  p90 {d[9 * len(d) // 10]:+.1f}"
              f"  |drift| > 5 min {sum(abs(x) > 5 for x in d) / len(d):.0%}  (n {len(d)})")
    return allb


def write_golden(pkg, trains_out, folder):
    raw = json.dumps(dict(edition=pkg["meta"]["edition"],
                          fields=["stop_index", "arr_s", "dep_s", "leg_s"], trains=trains_out),
                     separators=(",", ":")).encode()
    path = os.path.join(folder, "replay.json.gz")
    with open(path, "wb") as fh:
        with gzip.GzipFile(fileobj=fh, mode="wb", mtime=0, compresslevel=9) as gz:
            gz.write(raw)
    return path


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    folder = args[0] if args else "dist/gapeka-2026"
    pkg = json.load(gzip.open(os.path.join(folder, "package.json.gz")))
    legs, trains_out = replay(pkg)
    report(pkg, legs, trains_out)
    if "--write" in sys.argv:
        print("wrote", write_golden(pkg, trains_out, folder))
