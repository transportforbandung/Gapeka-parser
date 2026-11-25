"""Sample test for delayed priority trains: the 30 lowest-numbered intercity trains each start 15 min late (plan_edit).
   python research/delay_sample.py hold   (held trains re-timed from where they are held, current)
   python research/delay_sample.py origin (held trains re-planned from their origin, before)
Prints accepted holdings, how late the trains end (median / p90 / max), and the total delay of all other trains."""
import gzip,json,sys,re,statistics,time
sys.path.insert(0,'/home/user/Gapeka-parser')
src=open('/home/user/Gapeka-parser/planner.py').read()
if sys.argv[1]=='origin':      # old behaviour: held trains re-planned from their origin
    src=src.replace('''def plan_from(P, o, dep, pf, k, printed_times=True):''','''def plan_from(P, o, dep, pf, k, printed_times=True):
    k = 0''')
ns={}; exec(compile(src,'ps','exec'),ns)
pkg=json.load(gzip.open('/home/user/Gapeka-parser/dist/gapeka-2026/package.json.gz'))
tl=ns['Timeline'](pkg); P=ns['Planner'](pkg,tl)
T={t['no']:t for t in pkg['trains']}
ic=sorted([t for t in pkg['trains'] if t['type']=='intercity' and t['no'] in tl.trains and t['stops'][0][3] is not None],key=lambda t:int(re.match(r'\d+',t['no']).group()))[:30]
ends=[];acc=0;others=[];t0=time.time()
for t in ic:
    out,info=ns['plan_edit'](pkg,tl,P,t,t['stops'][0][3]+900,printed_from=t)
    ends.append((out[t['no']][0][-1][2]-t['stops'][-1][2])/60); acc+=info['accepted']
    others.append(sum(max(0,(r[-1][2]-T[n]['stops'][-1][2])/60) for n,(r,w) in out.items() if n!=t['no'] and r[-1][2] and T[n]['stops'][-1][2]))
q=sorted(ends)
print(sys.argv[1],'n',len(ends),'accepted',acc,'end late: median',round(statistics.median(q)),'p90',round(q[int(.9*(len(q)-1))]),'max',round(q[-1]),'| <=30 min',sum(x<=30 for x in q),'| others total delay median',round(statistics.median(others)),'max',round(max(others)),'|',round(time.time()-t0),'s')
