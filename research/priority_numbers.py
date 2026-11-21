"""Priority by train number (user): lower number has priority at crossings / overtakes; the higher number waits.
Share of waits where the waiting train has the higher number, by event and train types."""
import re, sqlite3
from collections import Counter, defaultdict
exec(open("waits.py").read().split("out = []")[0])        # events: (kind, station, waiter, other)
g = sqlite3.connect("/home/user/Gapeka-parser/gapeka.db")
typ = dict(g.execute("select no, type from trains"))
num = lambda n: int(re.match(r"\d+", n).group()) if re.match(r"\d+", n) else None
res = defaultdict(Counter); ex = defaultdict(list)
for kind, s, w, o in events:
    W, O = at(w, s), at(o, s)
    if W is None or O is None: continue
    nw, no_ = num(w), num(o)
    if nw is None or no_ is None or nw == no_: continue
    tw, to = typ.get(w), typ.get(o)
    pair = "same type" if tw == to else "different types"
    other_passes = O[3] is None                              # other train passes the station (no arrival time)
    sub = kind + (" (other passes)" if other_passes else " (both stop)")
    ok = nw > no_
    for k in (("all", sub), (pair, sub), (f"{tw} waits for {to}", sub)):
        res[k]["higher number waits" if ok else "LOWER number waits"] += 1
    if not ok and pair == "same type": ex[sub].append((s, w, o, tw))
for k in sorted(res, key=lambda k: (k[0] != "all", k[0] not in ("same type", "different types"), str(k))):
    c = res[k]; n = sum(c.values())
    if n >= 8:
        print(f"{k[0]:32s} {k[1]:28s} n={n:4d}  higher number waits {c['higher number waits']/n:5.0%}")
print("\nexceptions within the same type (sample):")
for sub, v in ex.items():
    print(" ", sub, len(v), v[:8])
