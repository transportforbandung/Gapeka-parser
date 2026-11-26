"""User rule (Nov 2026): the slower train is stopped where letting it continue would hold up / slow the faster
train behind. Measure the margin GAPEKA protects on double track:
 A. GAPEKA overtakes: if the waiting train had left its wait station after a 1-min stop (printed running time to the
    next station), how far ahead of the overtaker would it have been at that station and at the next one?
 B. faster train following a slower one with NO overtake: the gap kept between them at the same two points.
gap = overtaker's time - slower train's time (minutes; small = the overtaker is held up / slowed)."""
import gzip, json, sys
import numpy as np
sys.path.insert(0, "/home/user/Gapeka-parser")
import planner
pkg = json.load(gzip.open("/home/user/Gapeka-parser/dist/gapeka-2026/package.json.gz"))
tl = planner.Timeline(pkg)
typ = {t["no"]: t["type"] for t in pkg["trains"]}
pos = {no: {r[0]: i for i, r in enumerate(rows)} for no, rows in tl.trains.items()}


def nxt_station(rows, i):
    for j in range(i + 1, len(rows)):
        if tl.kind.get(rows[j][0]) != "halt":
            return j
    return None


A = []
for w in pkg["waits"]:
    W = w.get("waiter")
    if w["kind"] != "overtaken_wait" or not W:
        continue
    O = [n for n in w["trains"] if n and n != W]
    if not O or W not in tl.trains or O[0] not in tl.trains:
        continue
    O = O[0]
    rw, ro = tl.trains[W], tl.trains[O]
    st = w["station"]
    if st not in pos[W] or st not in pos[O]:
        continue
    i = pos[W][st]
    j = nxt_station(rw, i)
    if j is None or rw[j][0] not in pos[O] or not tl.block_double.get((st, rw[j][0])):
        continue
    run = rw[j][2] - rw[i][3]
    dep_wb = rw[i][2] + 60
    gA = (ro[pos[O][st]][3] - dep_wb) / 60
    gB = (ro[pos[O][rw[j][0]]][2] - (dep_wb + run)) / 60
    A.append((min(gA, gB), gA, gB, typ.get(W), typ.get(O)))
# B: consecutive same-direction trains in a double-track block, follower faster in that block, no overtake there
B = []
for (a, b), occ in tl.block_base.items():
    if not tl.block_double[(a, b)]:
        continue
    occ = sorted(occ)
    for (e1, x1, n1), (e2, x2, n2) in zip(occ, occ[1:]):
        if typ.get(n1) == "dinas" or typ.get(n2) == "dinas":
            continue
        if (x2 - e2) < (x1 - e1) - 30:                 # the follower is faster in this block
            B.append((min(e2 - e1, x2 - x1) / 60, typ.get(n1), typ.get(n2)))
a = np.array([x[0] for x in A]); b = np.array([x[0] for x in B])
print(f"A. GAPEKA overtakes on double track (n {len(a)}): would-be gap if the slower train had left after 1 min:")
print("   ", {f"<{t}": f"{np.mean(a < t):.0%}" for t in (0, 3, 5, 8, 10, 15, 20)}, f"median {np.median(a):.1f}")
print(f"B. faster train behind a slower one, no overtake (n {len(b)}): gap kept:")
print("   ", {f"<{t}": f"{np.mean(b < t):.0%}" for t in (3, 5, 8, 10, 15, 20)}, f"median {np.median(b):.1f}")
for thr in (3, 5, 6, 8, 10, 12, 15):
    print(f"   threshold {thr:2d} min: overtakes explained {np.mean(a < thr):.0%}, followers wrongly held {np.mean(b < thr):.0%}")
