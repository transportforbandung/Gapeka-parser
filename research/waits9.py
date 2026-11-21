"""Overtaking on double track: waiting train departs after the overtaker reaches the next BLOCK signal (sheet data)."""
import json
from collections import defaultdict
import numpy as np
exec(open("waits3.py").read().split("tab = defaultdict")[0])
P = json.load(open("line_profile.json"))
def blocks_from(s, n):
    """block signals between s and n on the track used going s->n, offsets measured from s; plus length"""
    p, rev = P.get(f"{s}-{n}"), False
    if p is None:
        p, rev = P.get(f"{n}-{s}"), True
    if p is None: return None, None
    track = "hi" if rev else "hu"                    # pairs are stored top->bottom: moving down the sheet = hulu
    L = p["km"]
    offs = sorted((L - b["o"]) if rev else b["o"] for b in p.get("blocks", []) if b["tag"] == track)
    return [o for o in offs if 0.1 < o < L - 0.05], L
res = defaultdict(list)
for (s, w), evs in groups.items():
    W = at(w, s)
    for kind, ov in evs:
        if kind != "overtake": continue
        O = at(ov, s)
        tS = O[4] if O[4] is not None else O[3]
        iw = next(i for i, r in enumerate(rows[w]) if r[0] == W[0])
        if iw + 1 >= len(rows[w]) or tS is None: continue
        n = rows[w][iw + 1][1]
        q = at(ov, n)
        if q is None or t_at(q) is None: continue
        offs, L = blocks_from(s, n)
        if not offs: continue
        tN = t_at(q)
        def t_at_dist(d): return tS + wrap(tN - tS) * min(d / L, 1.0)
        res["next station"].append(wrap(W[4] - tN) / 60)
        res["1st block signal"].append(wrap(W[4] - t_at_dist(offs[0])) / 60)
        if len(offs) > 1:
            res["2nd block signal"].append(wrap(W[4] - t_at_dist(offs[1])) / 60)
        res["overtaker passes station"].append(wrap(W[4] - tS) / 60)
        res["_line"].append(line(s))
print(f"overtakes on station pairs with block-signal data: {len(res['next station'])}  lines: {dict((l, res['_line'].count(l)) for l in set(res['_line']))}")
for k in ("overtaker passes station", "1st block signal", "2nd block signal", "next station"):
    v = np.array(res[k])
    if len(v) < 4: continue
    md = np.median(v)
    print(f"  reference = {k:26s} n={len(v):3d}  typical gap {md:+5.1f} min   within ±0.5 {np.mean(np.abs(v - md) <= .5):4.0%}   within ±1 {np.mean(np.abs(v - md) <= 1):4.0%}")
