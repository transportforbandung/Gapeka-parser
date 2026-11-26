"""Who waits at a single-track crossing, by train-number gap and direction (user: odd = from the east, even = from the
west; a 1-number difference should not decide priority just by direction).
Crossings from the timetable notes (package waits, kind 'cross', waiter known); only crossings where the other train
does NOT stand at the station (a clear 'one waits, one runs' case) and both are regular passenger / freight trains.
Prints the share where the LOWER number runs, by number gap, by class band (hundreds), by direction of the lower
number (odd / even), and who reached the station first.
Run: python research/cross_priority.py"""
import collections, gzip, json, re, sys
sys.path.insert(0, "/home/user/Gapeka-parser")
import planner

pkg = json.load(gzip.open("/home/user/Gapeka-parser/dist/gapeka-2026/package.json.gz"))
tl = planner.Timeline(pkg)
T = {t["no"]: t for t in pkg["trains"]}
num = lambda n: int(re.match(r"\D*(\d+)", n).group(1))


def at(no, st, ref):
    best = None
    for r in tl.trains.get(no, []):
        if r[0] == st:
            for sh in (-86400, 0, 86400):
                a, d = r[2] + sh, r[3] + sh
                if best is None or abs(a - ref) < abs(best[0] - ref):
                    best = (a, d)
    return best


rows = []
for w in pkg["waits"]:
    if not w["kind"].startswith("cross"):
        continue
    pair = [n for n in w["trains"] if n in T and n in tl.trains]
    if len(pair) != 2 or "dinas" in (T[pair[0]]["type"], T[pair[1]]["type"]):
        continue
    a0 = next((r for r in tl.trains[pair[0]] if r[0] == w["station"]), None)
    if not a0:
        continue
    a = at(pair[0], w["station"], a0[2])
    b = at(pair[1], w["station"], a[0])
    if not b or abs(a[0] - b[0]) > 3 * 3600:
        continue
    stands = [n for n, x in zip(pair, (a, b)) if x[1] - x[0] >= 60]
    if len(stands) != 1:
        continue                                   # both stand (or none): not a clear 'one waits, one runs' case
    wt = stands[0]
    ot = pair[1] if wt == pair[0] else pair[0]
    wa, oa = (a, b) if wt == pair[0] else (b, a)
    lo, hi = sorted((wt, ot), key=num)
    rows.append(dict(lower_runs=(wt == hi), gap=abs(num(wt) - num(ot)), same_band=num(wt) // 100 == num(ot) // 100,
                     lower_odd=num(lo) % 2 == 1, waiter_first=wa[0] <= oa[0], types=(T[wt]["type"], T[ot]["type"]),
                     same_type=T[wt]["type"] == T[ot]["type"], pair=(wt, ot, w["station"]),
                     dwell=(wa[1] - wa[0]) / 60))
print(f"clear crossings (other train passes): {len(rows)}; lower number runs {sum(r['lower_runs'] for r in rows) / len(rows):.0%}")


def show(title, key):
    g = collections.defaultdict(list)
    for r in rows:
        g[key(r)].append(r)
    print(title)
    for k in sorted(g):
        v = g[k]
        print(f"   {str(k):26s} n {len(v):4d}  lower number runs {sum(r['lower_runs'] for r in v) / len(v):4.0%}  "
              f"waiter arrived first {sum(r['waiter_first'] for r in v) / len(v):4.0%}")


show("by number gap", lambda r: "1" if r["gap"] == 1 else "2-9" if r["gap"] < 10 else "10-99" if r["gap"] < 100
     else "100-999" if r["gap"] < 1000 else ">= 1000")
show("by class band (hundreds)", lambda r: "same hundred" if r["same_band"] else "different hundred")
show("by type pair", lambda r: "same type" if r["same_type"] else "different type")
show("lower number's direction", lambda r: "odd (from east)" if r["lower_odd"] else "even (from west)")
show("same type, gap < 10, by direction", lambda r: ("odd" if r["lower_odd"] else "even") if r["same_type"] and r["gap"] < 10 else "-")
show("waiter dwell (short = likely its passenger stop)", lambda r: "<= 2 min" if r["dwell"] <= 2 else "> 2 min")
show("same type, gap < 10: who waits", lambda r: ("first to arrive waits" if r["waiter_first"] else "later one waits")
     if r["same_type"] and r["gap"] < 10 else "-")
print("examples gap 1:", [r["pair"] for r in rows if r["gap"] == 1][:10])
