"""Build one row per KAI-timed halt with its GAPEKA context -> halts.csv"""
import csv
import sqlite3
import sys

sys.path.insert(0, "/home/user/Gapeka-parser")
import gapeka_pipeline as gp  # noqa: E402

db = sqlite3.connect("/home/user/Gapeka-parser/gapeka.db")
dist = gp.make_distance(db)
M = list(csv.DictReader(open("matched.csv")))
kai = {(r["train"], int(r["seq"])): r for r in M}

out = []
for r in M:
    if r["kind"] != "halt" or not r["kai_s"]:
        continue
    no, seq = r["train"], int(r["seq"])
    rows = db.execute("select seq,station,kind,arr_s,dep_s,vmax from stops where train_no=? order by seq", (no,)).fetchall()
    i = next(k for k, x in enumerate(rows) if x[0] == seq)

    def timed(x):
        return x[2] in ("stop", "origin_or_end") or (x[2] == "pass" and x[4] is not None)
    p = next(k for k in range(i - 1, -1, -1) if timed(rows[k]))
    n = next(k for k in range(i + 1, len(rows)) if timed(rows[k]))
    A, B = rows[p], rows[n]
    t0 = A[4] if A[4] is not None else A[3]
    t1 = B[3] if B[3] is not None else B[4]
    chain = [x[1] for x in rows[p:n + 1]]
    legs = [dist(chain[k], chain[k + 1]) for k in range(len(chain) - 1)]
    nh_before = sum(1 for x in rows[p + 1:i] if x[2] == "halt")
    nh = sum(1 for x in rows[p + 1:n] if x[2] == "halt")
    km_to = sum(legs[: i - p]) if all(legs) else None
    km_all = sum(legs) if all(legs) else None
    # KAI times of other halts in the same leg
    out.append(dict(
        label=r["label"], train=no, service=r["service"], halt=r["station"], prev=A[1], next=B[1],
        prev_kind=A[2], next_kind=B[2], prev_dwell=(A[4] - A[3]) if A[3] is not None and A[4] is not None else "",
        dep_prev=t0, arr_next=t1, T=(t1 - t0) / 60, kai_off=(int(r["kai_s"]) - t0) / 60,
        to_next=(t1 - int(r["kai_s"])) / 60, km_to=km_to, km_all=km_all,
        f_km=(km_to / km_all) if km_all else "", f_t=(int(r["kai_s"]) - t0) / (t1 - t0),
        n_halts_leg=nh, halt_idx=nh_before, n_mids=n - p - 1, vmax=r["vmax"],
        model_off=((int(r["model_s"]) - t0) / 60) if r["model_s"] else "", method=r["method"],
        kai=r["kai"], chain="-".join(chain)))
w = csv.DictWriter(open("halts.csv", "w", newline=""), fieldnames=list(out[0]))
w.writeheader()
w.writerows(out)
print(len(out))
