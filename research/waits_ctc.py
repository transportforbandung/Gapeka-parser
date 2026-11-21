import json
import numpy as np
from collections import defaultdict
src = open("waits11.py").read()
src = src.replace('res[("double" if dbl else "single", why)].append((wrap(W[4] - ref) / 60, wrap(W[4] - old) / 60 if old else None, line(s)))',
                  'res[("double" if dbl else "single", why)].append((wrap(W[4] - ref) / 60, wrap(W[4] - old) / 60 if old else None, line(s), s))')
exec(src.split('print("overtaking, waiting train departure')[0])
CTC = set(json.load(open("ctc_stations.json")))
print(f"CTC stations known: {len(CTC)}")
# overtakes (block rule reference), by CTC
ov = defaultdict(list)
for k, xs in res.items():
    for x in xs:
        ov[(k[0], x[3] in CTC)].append(x[0])
print("\novertaking: waiting train departure - overtaker at the next block point (minutes)")
for k, v in sorted(ov.items()):
    v = np.array(v); md = np.median(v)
    print(f"   {k[0]:6s} track  {'CTC' if k[1] else 'not CTC':8s} n={len(v):3d}  median gap {md:+4.1f}  P10 {np.percentile(v, 10):+4.1f}  within ±0.5 of median {np.mean(np.abs(v - md) <= .5):4.0%}")
# crossings
cr = defaultdict(list)
for (s, w), evs in groups.items():
    W = at(w, s)
    for kind, ov_ in evs:
        if kind != "cross": continue
        r = ref_times(kind, s, w, ov_).get("other arrives/passes")
        if r is not None:
            cr[s in CTC].append(wrap(W[4] - r) / 60)
print("\ncrossing: waiting train departure - other train arrives/passes (minutes)")
for k, v in sorted(cr.items()):
    v = np.array(v); md = np.median(v)
    print(f"   {'CTC' if k else 'not CTC':8s} n={len(v):3d}  median {md:+4.1f}  P10 {np.percentile(v, 10):+4.1f}  within ±0.5 of median {np.mean(np.abs(v - md) <= .5):4.0%}")
