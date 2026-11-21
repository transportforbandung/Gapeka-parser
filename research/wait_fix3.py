import json
import numpy as np
src = open("waits11.py").read()
src = src.replace('res[("double" if dbl else "single", why)].append((wrap(W[4] - ref) / 60, wrap(W[4] - old) / 60 if old else None, line(s)))',
                  'res[("double" if dbl else "single", why)].append((wrap(W[4] - ref) / 60, wrap(W[4] - old) / 60 if old else None, line(s), s, w, ov, ref))')
exec(src.split('print("overtaking, waiting train departure')[0])
grp = lambda no: {"krl": "KRL", "freight": "freight"}.get(ttype(no), "passenger")
rowsout = []
for k, xs in res.items():
    if k[0] != "double": continue
    for gap, _, ln, s, w, ov, ref in xs:
        if grp(w) != "passenger": continue
        W, O = at(w, s), at(ov, s)
        tS = O[4] if O[4] is not None else O[3]
        run = wrap(ref - tS) / 60                     # overtaker time from s to the block point
        rowsout.append((gap, wrap(W[4] - tS) / 60, run, wrap(W[4] - W[3]) / 60, ln, s, w, ov, k[1], ttype(ov)))
a = np.array([r[:4] for r in rowsout])
print("passenger, double-track overtakes n=", len(a))
print("  dep - overtaker at block pt  pct10/25/50/75/90:", np.percentile(a[:, 0], [10, 25, 50, 75, 90]).round(1))
print("  dep - overtaker at station s pct:", np.percentile(a[:, 1], [10, 25, 50, 75, 90]).round(1))
print("  overtaker run s->block pt   pct:", np.percentile(a[:, 2], [10, 25, 50, 75, 90]).round(1))
print("  corr(gap at station, run to block):", np.corrcoef(a[:, 1], a[:, 2])[0, 1].round(2))
for h in (2, 3, 4, 5):
    e = a[:, 1] - h
    print(f"  dep = overtaker passes s + {h}:  exact {np.mean(np.abs(e) <= .5):.0%}  ±1 {np.mean(np.abs(e) <= 1):.0%}")
for r in sorted(rowsout, key=lambda r: r[4])[:200]:
    print(f"  {r[4][:28]:28s} {r[5]:5s} w={r[6]:6s} ov={r[7]:6s}({r[9][:5]}) gap@blk {r[0]:+5.1f} gap@s {r[1]:+5.1f} run {r[2]:4.1f} dwell {r[3]:5.1f} {r[8]}")
