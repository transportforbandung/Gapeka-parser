"""Un-noted trains: same-direction trains passing/leaving the waiting station while the train waits (not in its notes)."""
exec(open("wait_fix4.py").read().split("for variant in")[0])
by_st = defaultdict(list)
for no, rs in rows.items():
    for i, r in enumerate(rs):
        by_st[r[1]].append((no, i))
def nxt(no, s):
    rs = rows[no]
    for i, r in enumerate(rs):
        if r[1] == s and i + 1 < len(rs): return rs[i + 1][1]
cnt = defaultdict(lambda: [0, 0, 0]); ex = []
for (s, w), evs in groups.items():
    if grp(w) != "passenger": continue
    W = at(w, s); n1 = nxt(w, s)
    if n1 is None: continue
    noted = {ov for _, ov in evs}
    p = predict("line@block", s, w, evs)
    if p is None: continue
    e = wrap(p - W[4]) / 60
    extra = []
    for no, i in by_st[s]:
        if no == w or no in noted: continue
        r = rows[no][i]
        t = r[4] if r[4] is not None else r[3]
        if t is None: continue
        if 0 < wrap(t - W[3]) < wrap(W[4] - W[3]) and nxt(no, s) == n1:
            extra.append((no, wrap(W[4] - t) / 60, ttype(no)))
    kinds = {k for k, _ in evs}
    key = "overtake" if "overtake" in kinds else "crossing"
    band = "GAPEKA later >0.5" if e < -.5 else ("exact" if abs(e) <= .5 else "GAPEKA earlier")
    c = cnt[(key, band)]; c[0] += 1; c[1] += bool(extra)
    if extra and band == "GAPEKA later >0.5": ex.append((s, w, round(e, 1), extra[:3]))
for k in sorted(cnt):
    n, x, _ = cnt[k]
    print(f"{k[0]:9s} {k[1]:18s} n={n:4d}  with an un-noted same-direction train leaving during the wait: {x/n:4.0%}")
for r in ex[:25]: print("  ", r)
