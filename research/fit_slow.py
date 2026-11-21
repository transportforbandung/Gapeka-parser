"""Fit slow-zone rules from the residuals of the current model (training legs, physics + calibration, no corrections)."""
import os, json
os.environ.setdefault("RULES", "rules.json")
import numpy as np
import relearn as R
import physics_model as pm
legs, y, base, train = R.legs, R.y, R.base, R.train
res = y - base                                          # + = GAPEKA slower than the model
rules = json.load(open("rules.json"))
out = []
# terminus time: station buffers for terminating / originating trains
for st, types in (("GMR", ["intercity"]), ("PSE", ["intercity"]), ("SB", ["commuter"]), ("SGU", ["commuter"])):
    for side in ("arr", "dep"):
        ii = [i for i, g in enumerate(legs) if train[i] and g["type"] in types and
              ((side == "arr" and g["stations"][-1] == st and g["last"] and g["end_rest"]) or (side == "dep" and g["stations"][0] == st and g["first"] and g["start_rest"]))]
        if len(ii) < 5: continue
        s = float(np.median(res[ii]))
        print(f"terminus {st} {side} {types[0]:9s} n={len(ii):3d}  median residual {s:+5.0f} s")
        if abs(s) >= 20:
            out.append(dict(kind="station_buffer", station=st, sides=[side], when="terminal", seconds=round(s), types=types,
                            source="fitted", note=f"terminus time: trains held at the home signal of a busy terminus (user); {side}"))
# Ciroyom commuter stops (operational waits at Bandung / Cimindi home signals)
for side in ("arr", "dep"):
    ii = [i for i, g in enumerate(legs) if train[i] and g["type"] == "commuter" and
          ((side == "arr" and g["stations"][-1] == "CIR" and g["end_rest"]) or (side == "dep" and g["stations"][0] == "CIR" and g["start_rest"]))]
    s = float(np.median(res[ii])) if ii else 0
    print(f"Ciroyom {side} commuter n={len(ii):3d}  median residual {s:+5.0f} s")
    if len(ii) >= 5 and abs(s) >= 20:
        out.append(dict(kind="station_buffer", station="CIR", sides=[side], when="stop", seconds=round(s), types=["commuter"],
                        source="fitted", note="commuters held at Bandung's home signal / waiting for main-line trains at Cimindi (user)"))
# per-km buffers on slow lines
def line_pairs(page_list, stations=None):
    return [k.split("-") for k, p in pm.PROF.items() if p.get("page") in page_list and (stations is None or set(k.split("-")) <= stations)]
for name, pairs in (("Sukabumi line", line_pairs([28])),
                    ("Prupuk-Purwokerto", line_pairs([35, 36, 37], {"PPK", "LG", "BMA", "KRT", "PAT", "KRR", "KGD", "PWT"}))):
    want = {frozenset(p) for p in pairs}
    for t in ("intercity", "commuter"):
        num = den = 0.0; n = 0; vals = []
        for i, g in enumerate(legs):
            if not train[i] or g["type"] != t: continue
            km = sum(L for (a, b), (L, _) in zip(zip(g["stations"], g["stations"][1:]), g.get("sec_grade", [])) if frozenset((a, b)) in want)
            if km > 0.5 * sum(L for L, _ in g.get("sec_grade", [])):
                vals.append(res[i] / km); n += 1
        if n >= 8:
            v = float(np.median(vals))
            print(f"{name:18s} {t:9s} legs {n:4d}  median residual {v:+5.1f} s/km")
            if abs(v) >= 1.5:
                out.append(dict(kind="buffer_km", sections=pairs, seconds_per_km=round(v, 1), types=[t], source="fitted",
                                note=f"{name}: GAPEKA plans slower than the limits and gradient allowance"))
json.dump(rules + out, open("rules_slow.json", "w"), indent=1)
print(f"\nnew rules: {len(out)}")
for o in out:
    print("  ", {k: v for k, v in o.items() if k != "sections"})
# Kotok check: legs Kalisat-Kotok-Arjasa after the gradient allowance
for a, b in (("KLT", "KTK"), ("KTK", "KLT"), ("AJ", "KTK"), ("KTK", "AJ")):
    ii = [i for i, g in enumerate(legs) if g["stations"] == [a, b] and not g["noted"]]
    if ii:
        print(f"Kotok check {a}-{b}: n={len(ii)}  GAPEKA {np.median(y[ii]) / 60:.1f} min  model {np.median(base[ii]) / 60:.1f} min  residual {np.median(res[ii]):+.0f} s")
