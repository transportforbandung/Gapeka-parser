"""Relearned model + standard running-time lookup (identical leg pattern on other trains)."""
from collections import defaultdict
import numpy as np
import relearn as R

legs, y, pred, train, test, noted, typ = R.legs, R.y, R.pred, R.train, R.test, R.noted, R.typ
def key(g, by):
    return (g["service"] if by == "service" else g["type"], tuple(g["stations"]), g["start_rest"], g["end_rest"], len(g["halts"]))
look = {by: defaultdict(list) for by in ("service", "type")}
for i in np.where(train)[0]:
    for by in look: look[by][key(legs[i], by)].append(y[i])
out = {}
for variant in ("service", "service+type", "service, clip model"):
    p = pred.copy(); src = np.array(["model"] * len(legs), dtype=object)
    for i in np.where(test)[0]:
        g = legs[i]
        vs = look["service"].get(key(g, "service"))
        vt = look["type"].get(key(g, "type"))
        if variant == "service, clip model":
            if vs:
                p[i], src[i] = np.median(vs), "service"
            elif vt:                                   # keep the model, but within 1 min of the type's standard time
                p[i] = np.clip(pred[i], np.median(vt) - 60, np.median(vt) + 60); src[i] = "model clipped"
        elif vs:
            p[i], src[i] = np.median(vs), "service"
        elif vt and variant == "service+type":
            p[i], src[i] = np.median(vt), "type"
    m = test & ~noted
    print(f"{variant:22s} {R.bands((p - y)[m])}")
    for s in ("service", "type", "model clipped", "model"):
        mm = m & (src == s)
        if mm.sum():
            print(f"      from {s:14s} {R.bands((p - y)[mm])}")
