exec(open("wait_fix5.py").read().split("cnt = defaultdict")[0])
def prv(no, s):
    rs = rows[no]
    for i, r in enumerate(rs):
        if r[1] == s and i > 0: return rs[i - 1][1]
cnt = defaultdict(lambda: [0, 0]); gaps = []
for (s, w), evs in groups.items():
    if grp(w) != "passenger": continue
    W = at(w, s); n1 = nxt(w, s)
    if n1 is None: continue
    noted = {ov for _, ov in evs}
    p = predict("line@block", s, w, evs)
    if p is None: continue
    e = wrap(p - W[4]) / 60
    opp = []
    for no, i in by_st[s]:
        if no == w or no in noted: continue
        r = rows[no][i]; t = t_at(r)
        if t is None: continue
        if prv(no, s) == n1 and 0 < wrap(W[4] - t) < 600:     # opposite train arriving from the waiter's next station within 10 min before departure
            opp.append(wrap(W[4] - t) / 60)
    trk = double_track(s, n1)
    band = "GAPEKA later >0.5" if e < -.5 else ("exact" if abs(e) <= .5 else "GAPEKA earlier")
    c = cnt[("double" if trk else "single", band)]; c[0] += 1; c[1] += bool(opp)
    if opp and not trk and band != "exact": gaps.append(min(opp))
for k in sorted(cnt):
    print(f"{k[0]:7s} {k[1]:18s} n={cnt[k][0]:4d}  un-noted opposite train arrives <10 min before departure: {cnt[k][1]/cnt[k][0]:4.0%}")
print("single-track misses, dep - that train's arrival (min):", sorted(gaps))
