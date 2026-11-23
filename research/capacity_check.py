"""PROGRAM KA DAN KAPASITAS LINTAS (capacity.py) vs gapeka.db: trains per day over each section (both directions;
path reduced to the stations of the capacity diagrams), regular / facultative, against the printed P = a/b."""
import sqlite3, sys
from collections import Counter
import pymupdf
sys.path.insert(0, "/home/user/Gapeka-parser")
import capacity
R = "/home/user/Gapeka-parser"
blocks = capacity.read(pymupdf.open(f"{R}/GAPEKA-JAWA-2026.pdf"))
nodes = {c for b in blocks for c in (b["a"], b["b"])}
g = sqlite3.connect(f"{R}/gapeka.db")
fac = {n: f for n, f in g.execute("select no, facultative from trains")}
typ = dict(g.execute("select no, type from trains"))
cnt = Counter()
for no, in g.execute("select no from trains"):
    path = [s for s, in g.execute("select station from stops where train_no = ? order by seq", (no,)) if s in nodes]
    path = [s for i, s in enumerate(path) if i == 0 or s != path[i - 1]]
    for a, b in set(zip(path, path[1:])):
        cnt[(frozenset((a, b)), bool(fac[no]), typ[no] == "dinas")] += 1
ok = near = 0
rows = []
for b in blocks:
    if b["P"] is None:
        continue
    k = frozenset((b["a"], b["b"]))
    reg, fc = cnt[(k, False, False)] + cnt[(k, False, True)], cnt[(k, True, False)] + cnt[(k, True, True)]
    dreg = reg - b["P"][0]
    ok += dreg == 0 and fc == b["P"][1]
    near += abs(dreg) <= 2
    rows.append((abs(dreg), b["label"], b["page"], b["P"], (reg, fc), cnt[(k, False, True)] + cnt[(k, True, True)],
                 b["counts"].get("D")))
print(f"{len(rows)} sections: P regular and facultative exact {ok}, regular within 2 trains {near}")
for r in sorted(rows, reverse=True)[:40]:
    print(f"  {r[1]:9s} p{r[2]} printed P {r[3]}  gapeka.db {r[4]} (KA Dinas {r[5]}, printed D {r[6]})")
