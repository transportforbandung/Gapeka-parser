"""Align KAI published times with GAPEKA stops; write matched.csv"""
import csv
import json
import sqlite3
import sys

db = sqlite3.connect(sys.argv[1])
rows = json.load(open(sys.argv[2]))


def hm(s):
    h, m = s.split(".")
    return int(h) * 3600 + int(m) * 60


by = {}
for src, page, lab, order, code, sub, val in rows:
    by.setdefault((src, lab), []).append((order, code, sub, val))

out = []
unmatched = []
for (src, lab), ents in by.items():
    ents.sort()
    comps = lab.split("-")
    stops = []
    for c in comps:
        svc = db.execute("select service from trains where no=?", (c,)).fetchone()
        for r in db.execute("""select s.seq,s.station,s.kind,s.arr_s,s.dep_s,s.vmax,h.arr_s,h.method from stops s
               left join halt_est h on h.train_no=s.train_no and h.seq=s.seq where s.train_no=? order by s.seq""", (c,)):
            stops.append((c, svc[0] if svc else "?") + r)
    i = 0
    for order, code, sub, val in ents:
        j = i
        while j < len(stops) and stops[j][3] != code:
            j += 1
        if j == len(stops):
            unmatched.append((lab, code, val))
            continue
        if j + 1 < len(stops) and stops[j + 1][3] == code and stops[j + 1][0] != stops[j][0] and sub != "Datang":
            j += 1          # end of one train and start of the next at the same station: KAI shows the departure
        # SBI Datang then Berangkat: both refer to same row; keep index on Datang
        no, svc, seq, st, kind, a, d, v, est, meth = stops[j]
        out.append(dict(src=src, label=lab, train=no, service=svc, seq=seq, station=st, kind=kind, sub=sub,
                        kai=val, kai_s=hm(val) if val[0].isdigit() else "", arr_s=a if a is not None else "",
                        dep_s=d if d is not None else "", vmax=v, model_s=est if est is not None else "", method=meth or ""))
        i = j if sub == "Datang" else j + 1
w = csv.DictWriter(open("matched.csv", "w", newline=""), fieldnames=list(out[0]))
w.writeheader()
w.writerows(out)
print("matched", len(out), "unmatched", len(unmatched))
for u in unmatched[:40]:
    print("  ", u)
