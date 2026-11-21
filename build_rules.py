"""Build the rules database (rules.db): hand-made rules from rules/*.csv plus values fitted from the GAPEKA.

    python build_rules.py [--rules rules] [--timetable gapeka.db] [--infra infra.db] [--out rules.db]

Hand-made rules (CSV in git, edited by hand, every row has a source):
  station_zones, section_buffers, station_buffers, station_attributes, wait_gaps, halt_rules, halt_offsets (KAI times
  for the TfB halts)
Fitted (recomputed from every GAPEKA edition, never edited by hand):
  fitted_calibration   per train type: margin, constants, halts, terminus, crossings, steep-section allowance
  fitted_corrections   per directed section / station end / halt station and train type (seconds)
  fitted_halt_offsets  per halt + direction for KRL, from KAI's published Jabodetabek times (reference/)
  fit_stats            held-out accuracy of the running-time model and of the halt rules against KAI
"""
import argparse
import csv
import json
import os
import re
import sqlite3
from collections import defaultdict

import numpy as np

import gapeka_model as gm

CSV_TABLES = ("station_zones", "section_buffers", "station_buffers", "station_attributes", "wait_gaps", "halt_rules")


def read_manual_halt_offsets(path):
    """rules/halt_offsets.csv: 'halt,from,minutes[,trains]' with # comments"""
    out = []
    for line in open(path, encoding="utf-8"):
        line = line.split("#")[0].strip()
        if not line:
            continue
        parts = [x.strip() for x in line.split(",")]
        if len(parts) >= 3:
            out.append((parts[0], parts[1], float(parts[2]), parts[3] if len(parts) > 3 else ""))
    return out


def kai_references(ttdb, ref_dir):
    """(region, train, station, HH:MM) of published KAI halt times"""
    stops = defaultdict(set)
    for no, st in ttdb.execute("select train_no, station from stops"):
        stops[no].add(st)
    out = []
    p = os.path.join(ref_dir, "kai_krl_jabodetabek_2026.csv")
    if os.path.exists(p):
        for r in csv.DictReader(open(p)):
            if r["time"] == "Ls":
                continue
            no = next((n for n in (r["train"], r["train2"]) if n and r["station"] in stops[n]), None)
            if no:
                out.append(("jabodetabek", no, r["station"], r["time"]))
    p = os.path.join(ref_dir, "kai_halts_bandung_surabaya_2026.csv")
    if os.path.exists(p):
        out += [(r["region"], r["train"], r["station"], r["time"]) for r in csv.DictReader(open(p))]
    return out


def score_halts(est, refs):
    """published halt time (arrival rounded to the minute) vs KAI, per region: (exact, within 1 min, n)"""
    E = {(no, st): a for no, seq, st, a, d, m in est}
    halts = {(no, st) for no, seq, st, a, d, m in est}
    res = defaultdict(list)
    for reg, no, st, t in refs:
        if (no, st) not in halts:
            continue                                      # KAI time at a timed station, not a halt
        h, m = map(int, t.split(":"))
        mins = int(np.floor((E[(no, st)] % 86400) / 60 + 0.5))
        e = (mins - (h * 60 + m) + 720) % 1440 - 720
        res[reg + (" (held-out trains)" if reg == "jabodetabek" and gm.held_out(no) else "")].append(e)
    return {k: (float(np.mean(np.array(v) == 0)), float(np.mean(np.abs(v) <= 1)), len(v)) for k, v in res.items()}


def build(rules_dir="rules", tt_path="gapeka.db", infra_path="infra.db", out_path="rules.db", ref_dir="reference",
          log=print):
    ttdb = sqlite3.connect(tt_path)
    infra_codes = {c for (c,) in sqlite3.connect(infra_path).execute("select code from stations")}
    if os.path.exists(out_path):
        os.remove(out_path)
    rdb = sqlite3.connect(out_path)
    problems = []
    # ---- hand-made rules: CSV -> tables, station codes checked against infra.db
    for name in CSV_TABLES:
        p = os.path.join(rules_dir, name + ".csv")
        if not os.path.exists(p):
            continue
        rows = list(csv.DictReader(open(p)))
        cols = list(rows[0]) if rows else []
        rdb.execute(f"CREATE TABLE {name}({', '.join(c + ' TEXT' for c in cols)})")
        rdb.executemany(f"INSERT INTO {name} VALUES({','.join('?' * len(cols))})", [tuple(r[c] for c in cols) for r in rows])
        for i, r in enumerate(rows, 2):
            if "source" in r and not r["source"]:
                problems.append((name, i, "row without a source"))
            codes = [r.get("station"), r.get("towards")] + [x for s in (r.get("sections") or "").split() for x in s.split("-")]
            for c in codes:
                if c and c not in ("*",) and c not in infra_codes:
                    problems.append((name, i, f"unknown station code {c}"))
    rdb.execute("CREATE TABLE halt_offsets(halt TEXT, from_station TEXT, minutes REAL, trains TEXT, source TEXT)")
    p = os.path.join(rules_dir, "halt_offsets.csv")
    if os.path.exists(p):
        rdb.executemany("INSERT INTO halt_offsets VALUES(?,?,?,?,?)",
                        [r + ("KAI timetable (manual)",) for r in read_manual_halt_offsets(p)])
    # ---- fitted values
    infra = gm.Infra(infra_path, ttdb)
    model = gm.Model(ttdb, infra, gm.load_rules(rules_dir))
    stats = model.fit(log=log)
    model.save(rdb)
    refs = kai_references(ttdb, ref_dir)
    krl_refs = [(no, st, t) for reg, no, st, t in refs if reg == "jabodetabek"]
    offsets = gm.fit_halt_offsets(model, rules_dir, krl_refs) if krl_refs else {}
    rdb.execute("CREATE TABLE fitted_halt_offsets(halt TEXT, prev TEXT, next TEXT, seconds REAL, source TEXT)")
    rdb.executemany("INSERT INTO fitted_halt_offsets VALUES(?,?,?,?,?)",
                    [k + (round(v, 1), "fitted (KAI Jabodetabek)") for k, v in offsets.items()])
    est, counts = gm.estimate_halts(model, rules_dir, offsets)
    stats["halts_vs_kai"] = score_halts(est, refs)
    stats["halt_methods"] = counts
    stats["rule_problems"] = problems
    rdb.execute("CREATE TABLE fit_stats(key TEXT PRIMARY KEY, value TEXT)")
    rdb.executemany("INSERT INTO fit_stats VALUES(?,?)", [(k, json.dumps(v)) for k, v in stats.items()])
    rdb.commit()
    return model, offsets, est, stats


def report(stats, log=print):
    b = lambda x: f"<0.5 min {x[0]:4.0%}  0.5-1 {x[1]:4.0%}  >1 {x[2]:4.0%}  (n {x[3]})"
    log(f"    running-time model, held-out trains, legs without notes: physics+calibration {b(stats['physics_calibration'])}")
    log(f"                                                              full model          {b(stats['full'])}")
    for t, x in stats["by_type"].items():
        log(f"        {t:10s} {b(x)}")
    log(f"        legs with a crossing / overtaking note {b(stats['noted'])}")
    for k, (ex, w1, n) in sorted(stats["halts_vs_kai"].items()):
        log(f"    halt times vs KAI {k:32s} exact {ex:4.0%}  within 1 min {w1:4.0%}  (n {n})")
    for p in stats["rule_problems"]:
        log(f"    rules problem: {p}")


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--rules", default="rules")
    ap.add_argument("--timetable", default="gapeka.db")
    ap.add_argument("--infra", default="infra.db")
    ap.add_argument("--out", default="rules.db")
    ap.add_argument("--reference", default="reference")
    a = ap.parse_args()
    _, _, _, stats = build(a.rules, a.timetable, a.infra, a.out, a.reference)
    report(stats)


if __name__ == "__main__":
    main()
