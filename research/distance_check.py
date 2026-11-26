"""Independent check of infra.db section lengths against the 'JARAK ANTAR STASIUN' tables (PDF pages 1875-1887,
distances.py). Each consecutive pair of a table (halts skipped by the table) is compared with the shortest route over
infra.db sections. Output: differences > 0.01 km, and table pairs without an infra.db route."""
import heapq, sqlite3, sys
from collections import defaultdict
import pymupdf
sys.path.insert(0, "/home/user/Gapeka-parser")
import distances

R = "/home/user/Gapeka-parser"
i = sqlite3.connect(f"{R}/infra.db")
G = defaultdict(dict)
for a, b, km in i.execute("select a, b, km from sections where km is not null"):
    for x, y in ((a, b), (b, a)):
        G[x][y] = min(G[x].get(y, 1e9), km)


def route(a, b, limit):
    dist, pq = {a: 0}, [(0, a, [a])]
    while pq:
        d, x, path = heapq.heappop(pq)
        if x == b:
            return d, path
        if d > dist.get(x, 1e9) or d > limit:
            continue
        for y, w in G[x].items():
            if d + w < dist.get(y, 1e9):
                dist[y] = d + w
                heapq.heappush(pq, (d + w, y, path + [y]))
    return None, None


name = dict(i.execute("select code, name from stations"))
rows = distances.read(pymupdf.open(f"{R}/GAPEKA-JAWA-2026.pdf"))
seen, ok, diff, miss = set(), 0, [], []
for r in rows:
    if not r["adjacent"] or (r["a"], r["b"]) in seen:
        continue
    seen.add((r["a"], r["b"]))
    d, path = route(r["a"], r["b"], r["km"] * 1.5 + 2)
    if d is None:
        miss.append(r)
    elif abs(d - r["km"]) <= 0.01:
        ok += 1
    else:
        diff.append((round(d - r["km"], 3), r, path))
print(f"consecutive table pairs {len(seen)}: infra.db route within 10 m {ok}, different {len(diff)}, no route {len(miss)}")
print("differences (infra.db - table, km):")
for dd, r, path in sorted(diff, key=lambda x: -abs(x[0])):
    print(f"  {dd:+8.3f}  {r['a']}-{r['b']} ({name.get(r['a'], '?')} - {name.get(r['b'], '?')}) table {r['km']:.3f} "
          f"p{r['page']}  via {' '.join(path)}")
print("no infra.db route:")
for r in miss:
    print(f"  {r['a']}-{r['b']} ({name.get(r['a'], '?')} - {name.get(r['b'], '?')}) {r['km']:.3f} p{r['page']}")
