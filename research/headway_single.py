"""Same-direction following on a petak jalan: gap = follower leaves/passes A  -  leader arrives/passes B.
ITB/PTDI teaching rule (single track): follower may leave A only >= 4 min after the leader is at B."""
import sqlite3, numpy as np
from collections import defaultdict
db = sqlite3.connect("gapeka.db"); inf = sqlite3.connect("infra.db")
dt = {}
for a, b, d in inf.execute("select a,b,double_track from sections"):
    dt[(a, b)] = dt[(b, a)] = d
typ = dict(db.execute("select no,type from trains"))
rows = defaultdict(list)
for no, seq, st, kind, a, d in db.execute("select train_no,seq,station,kind,arr_s,dep_s from stops order by train_no,seq"):
    rows[no].append((st, kind, a, d))
legs = defaultdict(list)            # (A,B) -> [(tA, tB, train)] for consecutive TIMED stations (stations, not halts)
for no, R in rows.items():
    if typ.get(no) == "dinas": continue
    T = [(st, a, d) for st, k, a, d in R if (a is not None or d is not None) and k != "halt"]
    for (s1, a1, d1), (s2, a2, d2) in zip(T, T[1:]):
        tA = d1 if d1 is not None else a1; tB = a2 if a2 is not None else d2
        if (s1, s2) in dt:
            legs[(s1, s2)].append((tA % 86400, tB % 86400 if tB >= tA else (tB + 86400) % 86400, no))
res = defaultdict(list)
for (A, B), L in legs.items():
    L.sort()
    for (a1, b1, n1), (a2, b2, n2) in zip(L, L[1:]):
        if b1 < a1: b1 += 86400
        g = (a2 - b1) / 60
        if -30 < g < 30:                                    # only close followers
            res[dt[(A, B)]].append(g)
for d, v in sorted(res.items(), key=lambda x: str(x[0])):
    v = np.array(v); pos = v[v >= 0]
    print(f"double_track={d}: n={len(v)} leader still in section (gap<0) {np.mean(v<0):.1%}; of gap>=0: "
          f"min {pos.min():.1f}  p5 {np.percentile(pos,5):.1f}  p25 {np.percentile(pos,25):.1f}  median {np.median(pos):.1f}; "
          f"share <4 min {np.mean(pos<4):.1%}, <2 {np.mean(pos<2):.1%}")
    h = np.histogram(pos[pos<10], bins=np.arange(0,10.5,1))[0]; print("   0..10 min histogram:", list(h))
from collections import Counter
c = Counter(); ex = []
for (A, B), L in legs.items():
    if dt[(A, B)] != 0: continue
    for (a1, b1, n1), (a2, b2, n2) in zip(L, L[1:]):
        if b1 < a1: b1 += 86400
