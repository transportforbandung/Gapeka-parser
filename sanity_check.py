"""Sanity check of a timetable: py sanity_check.py gapeka.db ["Service name" ...]
Flags time going backwards, dep before arr, zero/negative legs, running times that differ a lot from the same
station pair on other trains, too fast / too slow legs, and long dwells. Writes sanity_flags.csv."""
import sqlite3,sys,statistics as st
from collections import defaultdict
import os
sys.path.insert(0,os.path.dirname(os.path.abspath(__file__)))
import gapeka_pipeline as gp      # keep this file next to gapeka_pipeline.py
db=sqlite3.connect(sys.argv[1] if len(sys.argv)>1 else "gapeka.db")
nm=dict(db.execute("select code,name from stations"))
dist=gp.make_distance(db)
def hm(s): return f"{int(s)//3600%24:02d}:{int(s)%3600//60:02d}"
SERV=sys.argv[2:] or ["Commuter Line Garut","Commuter Line Bandung Raya","Siliwangi"]
flags=[]; summary={}
for svc in SERV:
    trains=db.execute("select no,route from trains where service=? order by cast(no as integer)",(svc,)).fetchall()
    legs=defaultdict(list); data={}
    for no,route in trains:
        rows=db.execute("select seq,station,kind,vmax,arr_s,dep_s,estimated from schedule where train_no=? order by seq",(no,)).fetchall()
        data[no]=(route,rows)
        # displayed times as on the poster
        disp=[]
        for seq,s,k,v,a,d,e in rows:
            if k=="pass": continue
            t=a if k=="halt" else (d if d is not None else a)
            if t is not None: disp.append((s,t,seq,v,a,d,k))
        for x,y in zip(disp,disp[1:]):
            a2=y[4] if y[4] is not None else y[5]; d1=x[5] if x[5] is not None else x[4]
            if a2 is not None and d1 is not None: legs[(svc,x[0],y[0])].append(a2-d1)
        data[no]=(route,rows,disp)
    med={k:st.median(v) for k,v in legs.items()}
    n=0
    for no,(route,rows,disp) in data.items():
        # 1. time order over every row that has a time (incl. LS passing times)
        timed=[(seq,s,k,a,d) for seq,s,k,v,a,d,e in rows if a is not None or d is not None]
        prev=None
        for seq,s,k,a,d in timed:
            for t,lab in ((a,"arr"),(d,"dep")):
                if t is None: continue
                if prev is not None and t<prev[0]:
                    flags.append((svc,no,route,"time goes back",f"{nm[prev[1]]} {hm(prev[0])} -> {nm[s]} {lab} {hm(t)}")); 
                prev=(t,s)
        # 2. dwell too long / arr>dep
        for seq,s,k,v,a,d,e in rows:
            if a is not None and d is not None:
                if d<a: flags.append((svc,no,route,"dep before arr",f"{nm[s]} {hm(a)}/{hm(d)}"))
                elif (d-a)>900 and k=="stop": flags.append((svc,no,route,"long dwell",f"{nm[s]} {hm(a)}-{hm(d)} ({(d-a)//60} min)"))
        # 3. leg duration vs same pair on other trains, and speed
        for x,y in zip(disp,disp[1:]):
            a2=y[4] if y[4] is not None else y[5]; d1=x[5] if x[5] is not None else x[4]
            if a2 is None or d1 is None: continue
            dur=(a2-d1)/60; m=med[(svc,x[0],y[0])]/60
            km=dist(x[0],y[0]) if x[0]!=y[0] else None
            lab=f"{nm[x[0]]}->{nm[y[0]]}"
            if dur<=0: flags.append((svc,no,route,"zero/negative leg",f"{lab} {hm(x[1])}->{hm(y[1])}"))
            elif abs(dur-m)>=3 and abs(dur-m)/m>=0.4 and len(legs[(svc,x[0],y[0])])>=4:
                flags.append((svc,no,route,"running time differs from other trains",f"{lab} runs {dur:.0f} min vs median {m:.0f} (dep {hm(d1)} -> arr {hm(a2)})"))
            # speed on real run time: next arrival (or its time) minus previous departure
            a2=y[4] if y[4] is not None else y[5]; d1=x[5] if x[5] is not None else x[4]
            if km and a2 is not None and d1 is not None and a2>d1 and km>=1.0:
                sp=km/(a2-d1)*3600; vm=max(x[3] or 0,y[3] or 0) or 90
                if sp>vm*1.1: flags.append((svc,no,route,"too fast",f"{lab} {km:.1f} km in {(a2-d1)/60:.1f} min = {sp:.0f} km/h (limit {vm})"))
                elif sp<12: flags.append((svc,no,route,"too slow",f"{lab} {km:.1f} km in {(a2-d1)/60:.1f} min = {sp:.0f} km/h"))
        n+=1
    summary[svc]=(n,sum(len(v) for v in legs.values()))
print(summary)
from collections import Counter
print(Counter((f[0],f[3]) for f in flags))
import csv
with open("sanity_flags.csv","w",newline="",encoding="utf-8-sig") as f:
    w=csv.writer(f); w.writerow(["service","train","route","issue","detail"]); w.writerows(flags)
for f in flags[:60]: print(f)