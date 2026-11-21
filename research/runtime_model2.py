"""Refined linear running-time model + noise floor + application to KAI halts."""
import csv
import pickle
import sqlite3
import sys
from collections import defaultdict

import numpy as np

from runtime_model import build_legs, lad, metrics

sys.path.insert(0, "/home/user/Gapeka-parser")
import gapeka_pipeline as gp  # noqa: E402

NAMES = ["cruise (sum km/limit)", "start at rest", "start at rest x speed", "end at rest", "end at rest x speed",
         "halts", "halts x speed", "constant"]


def feats(secs, start_rest, end_rest, halt_speeds):
    cruise = sum(d / v for d, v in secs) * 3600
    v0, v1 = secs[0][1], secs[-1][1]
    return [cruise, float(start_rest), start_rest * v0, float(end_rest), end_rest * v1,
            float(len(halt_speeds)), float(sum(halt_speeds)), 1.0]


def leg_feats(g):
    hs = []
    cum = 0.0
    for d, v in g["secs"]:
        cum += d
        if any(abs(cum - h) < 1e-6 for h in g["halts"]):
            hs.append(v)
    return feats(g["secs"], g["start_rest"], g["end_rest"], hs)


def group_key(g):
    return (g["service"] if g["cls"] == "emu" else g["cls"])


def main():
    db = sqlite3.connect("/home/user/Gapeka-parser/gapeka.db")
    legs = build_legs(db)
    models = {}
    print(f"{'group':34s} {'n test':>6s} | {'M1 km':>14s} | {'M2b linear':>14s} | {'same-leg median':>16s}   (MAE min / exact-minute)")
    rows_out = []
    for cls in ("emu", "passenger", "freight"):
        G = [g for g in legs if g["cls"] == cls]
        tr = [g for g in G if not g["test"] and not g["noted"]]
        te = [g for g in G if g["test"]]
        Xtr = np.array([leg_feats(g) for g in tr])
        ytr = np.array([g["T"] for g in tr], float)
        # drop columns with no variation (e.g. no pass legs) to keep the fit identifiable
        keep = [j for j in range(Xtr.shape[1]) if np.ptp(Xtr[:, j]) > 0 or NAMES[j] == "constant"]
        c = lad(Xtr[:, keep], ytr)
        coef = np.zeros(len(NAMES))
        coef[keep] = c
        models[cls] = coef
        # same-leg lookup (noise floor): median T of identical legs (same stations, same ends, same halts) of other trains
        look = defaultdict(list)
        for g in tr:
            look[(group_key(g), tuple(g["stations"]), g["start_rest"], g["end_rest"], len(g["halts"]))].append(g["T"])
        yte = np.array([g["T"] for g in te], float)
        p2 = np.array([leg_feats(g) for g in te]) @ coef
        c1 = lad(np.array([[g["km"], 1.0] for g in tr]), ytr)
        p1 = np.array([[g["km"], 1.0] for g in te]) @ c1
        lk = np.array([np.median(look[k]) if (k := (group_key(g), tuple(g["stations"]), g["start_rest"], g["end_rest"], len(g["halts"]))) in look else np.nan for g in te])
        m = ~np.isnan(lk)
        a, b, o = metrics(p1, yte), metrics(p2, yte), metrics(lk[m], yte[m])
        print(f"{cls:34s} {len(te):6d} | {a['mae_min']:.2f} / {a['exact']:3.0%}   | {b['mae_min']:.2f} / {b['exact']:3.0%}   | "
              f"{o['mae_min']:.2f} / {o['exact']:3.0%} (n {o['n']})   ±1 min: M2b {b['within1']:.0%}")
        print("     " + ";  ".join(f"{n} {v:+.3f}" if n.startswith("cruise") else f"{n} {v:+.1f}" for n, v in zip(NAMES, coef)))
        rows_out.append((cls, coef))
    # what each input contributes: drop one feature group, refit, score (emu + passenger + freight pooled)
    print("\nablation (refit without the effect; MAE minutes on held-out trains, all classes):")
    groups = {"full model": [], "no section speed limits (cruise=km only)": ["limits"], "no stop/pass distinction": [1, 2, 3, 4],
              "stop penalty not speed-dependent": [2, 4, 6], "halts ignored": [5, 6]}
    for lab, drop in groups.items():
        errs = []
        for cls in ("emu", "passenger", "freight"):
            G = [g for g in legs if g["cls"] == cls]
            tr = [g for g in G if not g["test"] and not g["noted"]]
            te = [g for g in G if g["test"]]

            def F(g):
                f = leg_feats(g)
                if drop == ["limits"]:
                    f[0] = g["km"]
                    return f
                return [x for j, x in enumerate(f) if j not in drop]
            Xtr = np.array([F(g) for g in tr])
            keep = [j for j in range(Xtr.shape[1]) if np.ptp(Xtr[:, j]) > 0 or j == Xtr.shape[1] - 1]
            c = lad(Xtr[:, keep], np.array([g["T"] for g in tr], float))
            p = np.array([F(g) for g in te])[:, keep] @ c
            errs.extend(np.abs(p - np.array([g["T"] for g in te])) / 60)
        print(f"  {lab:44s} MAE {np.mean(errs):.2f}")
    pickle.dump(models, open("runtime_linear.pkl", "wb"))

    # ---------------------------------------------------------------- KAI halts
    dist = gp.make_distance(db)
    coef = models["emu"]
    H = list(csv.DictReader(open("detail.csv")))
    G = {(r["train"], r["halt"]): r for r in csv.DictReader(open("graph_halts.csv"))}
    res = defaultdict(list)
    for h in H:
        g = G[(h["train"], h["halt"])]
        chain = g["chain"].split("-")
        i = chain.index(h["halt"])
        vm = dict(db.execute("select station,vmax from stops where train_no=?", (h["train"],)).fetchall())
        secs = [(dist(chain[k], chain[k + 1]), vm[chain[k]]) for k in range(len(chain) - 1)]
        if not all(d for d, _ in secs):
            continue
        kai = float(h["kai"])
        # a) arrival at halt: from rest, cruise to halt, braking to rest, no halts in between
        f_arr = feats(secs[:i], True, True, [])
        arr = np.dot(coef, f_arr) / 60
        # b) share of the minimum leg time, weighted by the two sub-legs as predicted by the model
        f2 = feats(secs[i:], True, True, [])
        share = float(h["T_min"]) * np.dot(coef, f_arr) / (np.dot(coef, f_arr) + np.dot(coef, f2))
        region = "bandung" if h["service"].endswith(("Bandung Raya", "Garut")) else "surabaya"
        res[(region, "model run time prev->halt (rest to rest)")].append((arr, kai))
        res[(region, "share of min leg time by model sub-leg times")].append((share, kai))
    print("\nKAI halt times predicted with the EMU coefficients (offset after previous departure):")
    for (region, lab), v in sorted(res.items()):
        p = np.array([x for x, _ in v])
        k = np.array([y for _, y in v])
        for sh, nm in ((0, "arrival"), (1, "+1 min (departure)")):
            e = np.round(p) + sh - k
            print(f"  {region:9s} {lab:46s} {nm:19s} exact {np.mean(e == 0):4.0%}  ±1 {np.mean(np.abs(e) <= 1):4.0%}  "
                  f"mean error {np.mean(p + sh - k):+.2f} min  n {len(v)}")


if __name__ == "__main__":
    main()
