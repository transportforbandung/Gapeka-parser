"""Precomputed physics times per directed section for the simulator package (docs/simulator_package.md, phase 2).

Entry key: (a, b, variant, run_class, vmax). a -> b = consecutive stations of a train (halts and passes included),
variant = the infra.db section variant used ('' = the only one; Kasugihan-Maos has two, chosen by the timed leg's
stations), run_class = train type, 'commuter-emu' for non-KRL commuters with EMU performance (Ka Feeder), vmax = the
train's timetable speed for this section (km/h).
Entry value: km, ruling gradient (permil, may be None), and 9 physics times (s, gapeka_model.simulate on this one
section, station zones applied) for start x end kind, each of pass / stop / terminal, in this order:
  pp ps pt  sp ss st  tp ts tt      (first letter = start at a, second = end at b; t = the train's origin / terminus)

The running time of a leg between two timed points is composed from these (replay.py, compose()): the physics time is
the sum of the sections' times, with each section's ends taken as the train runs (stop at a halt or stop, terminal at
the train's ends); the calibration, buffers and corrections of the package are then applied once per leg, exactly as
gapeka_model.Model does for the whole leg. The only approximation is the physics sum: a train still accelerating
(or already braking) across a section boundary is cut at that boundary.
"""
from collections import defaultdict

import gapeka_model as gm

KINDS = ("pass", "stop", "terminal")
PATTERNS = [(s, e) for s in KINDS for e in KINDS]


def run_class(ttype, service):
    if ttype == "commuter" and service in gm.EMU_LIKE:
        return "commuter-emu"
    return ttype


def class_type(rc):
    """train type (for zones, buffers, calibration) and physics group of a run class"""
    if rc == "commuter-emu":
        return "commuter", "krl"
    return rc, gm.phys_group(rc)


def variant_of(infra, a, b, ctx):
    p = infra._pick(a, b, frozenset(ctx))
    return p[0]["variant"] if p else ""


def collect(model):
    """every (a, b, variant, run_class, vmax) used by a timed leg of a krl / commuter / intercity / freight train,
    with the leg's stations as context"""
    keys = {}
    for no, typ in model.ttype.items():
        if typ not in ("krl", "commuter", "intercity", "freight"):
            continue
        rc = run_class(typ, model.service.get(no, ""))
        rows = model.db.execute("select station, kind, arr_s, dep_s, vmax from stops where train_no = ? order by seq",
                                (no,)).fetchall()
        timed = [i for i, r in enumerate(rows) if r[1] in ("stop", "origin_or_end") or
                 (r[1] == "pass" and r[3] is not None)]
        for p, q in zip(timed, timed[1:]):
            ctx = frozenset(r[0] for r in rows[p:q + 1])
            for i in range(p, q):
                a, b, v = rows[i][0], rows[i + 1][0], rows[i][4]
                if not v:
                    continue
                k = (a, b, variant_of(model.infra, a, b, ctx), rc, v)
                keys.setdefault(k, ctx)
    return keys


def build(model):
    """list of [a, b, variant, run_class, vmax, km, ruling, [9 times]] (times rounded to 0.1 s)"""
    keys = collect(model)
    legs, owner = [], []
    for k, ctx in keys.items():
        a, b, var, rc, v = k
        typ, grp = class_type(rc)
        for s, e in PATTERNS:
            g = model.make_leg("__lt", [a, b], [v, v], s != "pass", e != "pass", (), s == "terminal",
                               e == "terminal", ttype=typ, ctx=ctx)
            if g is None:
                break
            g["service"] = "Ka Feeder" if rc == "commuter-emu" else ""
            legs.append(g)
            owner.append(k)
    sims = model.run_sim(legs) if legs else []
    by = defaultdict(dict)
    meta = {}
    for g, k, t in zip(legs, owner, sims):
        pat = (g["first"] and "t" or (g["start_rest"] and "s" or "p")) + (g["last"] and "t" or (g["end_rest"] and "s" or "p"))
        by[k][pat] = round(float(t), 1)
        meta[k] = (round(g["sec_grade"][0][0], 3), g["sec_grade"][0][1])
    out = []
    order = [a[0] + b[0] for a in KINDS for b in KINDS]
    for k, t in by.items():
        if len(t) != 9:
            continue
        km, ruling = meta[k]
        out.append(list(k) + [km, None if ruling is None else round(ruling, 2), [t[p] for p in order]])
    out.sort(key=lambda r: (r[0], r[1], r[2], r[3], r[4]))
    return out


if __name__ == "__main__":
    import sqlite3
    import time
    t0 = time.time()
    tt = sqlite3.connect("gapeka.db")
    m = gm.Model(tt, gm.Infra("infra.db", tt), gm.load_rules("rules"))
    rows = build(m)
    from collections import Counter
    print(len(rows), "entries", Counter(r[3] for r in rows), f"{time.time() - t0:.0f} s")
    print(rows[:3])
