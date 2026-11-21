"""Crossing and overtaking waits: how long after the other train's reference event does the waiting train leave?
crossing   : reference = other train's arrival at (or pass through) the crossing station
overtaking : reference = overtaking train's arrival at (or pass through) the NEXT station on the waiting train's route
gap = waiting train's departure - reference (seconds). Writes waits.csv."""
import csv, re, sqlite3
from collections import defaultdict
import numpy as np

db = sqlite3.connect("/home/user/Gapeka-parser/gapeka.db")
svc = dict(db.execute("select no,service from trains"))
rows = defaultdict(list)
for no, seq, st, kind, a, d in db.execute("select train_no,seq,station,kind,arr_s,dep_s from stops order by train_no,seq"):
    rows[no].append((seq, st, kind, a, d))
def at(no, st):
    for r in rows.get(no, []):
        if r[1] == st:
            return r
def t_at(r):                 # arrival if it stops, else its pass time
    if r is None: return None
    return r[3] if r[3] is not None else r[4]
def wrap(x):                 # signed difference modulo one day
    return ((x + 43200) % 86400) - 43200
KRL = ("Commuter Line Bogor", "Commuter Line Cikarang", "Commuter Line Rangkasbitung", "Commuter Line Tangerang",
       "Commuter Line Tanjungpriuk", "Commuter Line BST", "Commuter Line Yogyakarta")
def ttype(no):
    s = svc.get(no, "")
    if s in KRL: return "krl"
    if any(k in s.lower() for k in ("cargo", "tanker", "service", "trans", "kalmas", "ketandan")): return "freight"
    if s.startswith("Commuter") or s in ("Ka Feeder", "Bandara SMO"): return "commuter"
    return "intercity"
base = lambda n: (re.match(r"\d+F?", n.strip()) or [n])[0]

events = {}
for t, seq, s, sym, o, own in db.execute("select train_no,seq,station,symbol,other_train,own from notes"):
    if not o: continue
    o = o if o in rows else base(o)
    if o not in rows: continue
    A, B = at(t, s), at(o, s)
    if A is None or B is None: continue
    if sym in ("=", "//"):
        w, ov = (t, o) if sym == "=" else (o, t)
        events[("overtake", s, w, ov)] = None
    elif sym == "x":
        # waiter = the train that is at the station first and leaves after the other has arrived
        ta, tb = t_at(A), t_at(B)
        if ta is None or tb is None: continue
        w, ov = (t, o) if wrap(ta - tb) < 0 else (o, t)
        events[("cross", s, w, ov)] = None

out = []
for kind, s, w, ov in events:
    W, O = at(w, s), at(ov, s)
    if W is None or W[3] is None or W[4] is None:
        continue                                   # waiter must stop with arrival and departure
    if kind == "cross":
        ref_st, R = s, O
    else:
        seqs = [r for r in rows[w] if r[0] > W[0]]
        R, ref_st = None, None
        for r in seqs:                             # next station on the waiting train's route where the overtaker has a time
            q = at(ov, r[1])
            if q is not None and t_at(q) is not None:
                R, ref_st = q, r[1]
                break
        if R is None: continue
    ref = t_at(R)
    if ref is None: continue
    gap = wrap(W[4] - ref)
    dwell = wrap(W[4] - W[3])
    out.append(dict(kind=kind, station=s, waiter=w, waiter_type=ttype(w), other=ov, other_type=ttype(ov),
                    other_stops_at_ref=R[3] is not None, ref_station=ref_st, w_arr=W[3], w_dep=W[4], ref=ref,
                    gap_s=gap, dwell_s=dwell, other_at_station=t_at(O)))
with open("waits.csv", "w", newline="") as f:
    wr = csv.DictWriter(f, fieldnames=list(out[0])); wr.writeheader(); wr.writerows(out)

def summ(v):
    v = np.array(v)
    vals, cnt = np.unique(np.round(v / 60 * 2) / 2, return_counts=True)
    mode = vals[np.argmax(cnt)]
    return (f"n={len(v):4d}  median {np.median(v) / 60:+5.1f} min  IQR {np.percentile(v, 25) / 60:+4.1f}..{np.percentile(v, 75) / 60:+4.1f}  "
            f"mode {mode:+4.1f}  within 0.5 min of mode {np.mean(np.abs(v / 60 - mode) <= .5):4.0%}")
print(f"wait events with a stopping waiter: {len(out)}")
for kind in ("cross", "overtake"):
    sub = [r for r in out if r["kind"] == kind]
    print(f"\n{kind.upper()}: gap = waiting train's departure - reference ({'other at crossing station' if kind == 'cross' else 'overtaker at next station'})")
    print("  all                       ", summ([r["gap_s"] for r in sub]))
    for key, lab in (("other_stops_at_ref", "other train stops at ref station"), ("waiter_type", "waiting train type"), ("other_type", "other train type")):
        for val in sorted({r[key] for r in sub}, key=str):
            v = [r["gap_s"] for r in sub if r[key] == val]
            if len(v) >= 10:
                print(f"  {lab:30s} {str(val):9s} {summ(v)}")
    neg = [r for r in sub if r["gap_s"] < -30]
    print(f"  departures before the reference (< -0.5 min): {len(neg)} ({len(neg) / len(sub):.0%})")
