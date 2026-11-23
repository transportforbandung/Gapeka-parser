"""Alternative priority orders (type, class, facultative, then number) vs number alone."""
import re, sqlite3
from collections import Counter, defaultdict
exec(open("waits.py").read().split("out = []")[0])
g = sqlite3.connect("/home/user/Gapeka-parser/gapeka.db")
info = {n: (t, k or "", f) for n, t, k, f in g.execute("select no, type, kelas, facultative from trains")}
num = lambda n: int(re.match(r"\d+", n).group())
print(Counter(info[n][1] for n in info if info[n][0] == "intercity").most_common())
RANK = {"ARGO": 0, "EKSEKUTIF": 1, "BANDARA YIA": 1, "EKSEKUTIF CAMPURAN": 2, "EKONOMI JARAK JAUH": 3}
def tr(n):
    t, k, f = info[n]
    return {"intercity": 0, "commuter": 2, "krl": 2, "freight": 3, "dinas": 4}.get(t, 5)
rules = {
  "lower number": lambda w, o: num(w) > num(o),
  "type, then number": lambda w, o: (tr(w), num(w)) > (tr(o), num(o)),
  "type, class, then number": lambda w, o: (tr(w), RANK.get(info[w][1], 9), num(w)) > (tr(o), RANK.get(info[o][1], 9), num(o)),
  "type, regular before facultative, number": lambda w, o: (tr(w), info[w][2], num(w)) > (tr(o), info[o][2], num(o)),
  "type, facultative, class, number": lambda w, o: (tr(w), info[w][2], RANK.get(info[w][1], 9), num(w)) > (tr(o), info[o][2], RANK.get(info[o][1], 9), num(o)),
}
res = defaultdict(Counter)
for kind, s, w, o in events:
    W, O = at(w, s), at(o, s)
    if W is None or O is None or w not in info or o not in info: continue
    if not re.match(r"\d", w) or not re.match(r"\d", o): continue
    sub = kind + (" (other passes)" if O[3] is None else " (both stop)")
    same = "same type" if info[w][0] == info[o][0] else "diff types"
    for name, f in rules.items():
        for k in ((sub, "all"), (sub, same)):
            res[(name,) + k][f(w, o)] += 1
for sub in ("overtake (other passes)", "cross (other passes)", "cross (both stop)"):
    print("\n" + sub)
    for name in rules:
        a = res[(name, sub, "all")]; s_ = res[(name, sub, "same type")]
        print(f"  {name:42s} all {a[True]/sum(a.values()):4.0%} (n {sum(a.values())})   same type {s_[True]/max(1,sum(s_.values())):4.0%}")
