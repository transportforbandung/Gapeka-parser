"""Package physics (replay.compose: physics_ref over the whole leg) vs gapeka_model's whole-leg prediction, same legs.
Also held-out-only bands for a fair comparison with the model's fit_stats."""
import gzip, json, sqlite3, sys
import numpy as np
sys.path.insert(0, "/home/user/Gapeka-parser")
import gapeka_model as gm
import replay
R = "/home/user/Gapeka-parser"
pkg = json.load(gzip.open(f"{R}/dist/gapeka-2026/package.json.gz"))
eng = replay.Engine(pkg)
tt = sqlite3.connect(f"{R}/gapeka.db")
m = gm.Model(tt, gm.Infra(f"{R}/infra.db", tt), gm.load_rules(f"{R}/rules"))
r = sqlite3.connect(f"{R}/rules.db")
calib = {}
for t, f, v in r.execute("select train_type, feature, value from fitted_calibration"):
    calib.setdefault(t, {})[f] = v
m.calib = {t: np.array([calib[t][f] for f in m.FEATURE_NAMES]) for t in calib}
for kind, a, b, ek, t, sec in r.execute("select kind, a, b, end_kind, train_type, seconds from fitted_corrections"):
    key = {"section": ("sec", a, b, t), "halt": ("halt", a, t)}.get(kind, (kind, a, ek, t))
    m.eff[key] = (sec, 0)
legs = m.timetable_legs()
sim = m.run_sim(legs)
T = {t["no"]: t for t in pkg["trains"]}
diff, err_c, err_d, ho = [], [], [], []
for g, s in zip(legs, sim):
    direct = m.calibrated(g, s) + sum(m.eff.get(k, (0, 0))[0] for k in m.corr_keys(g))
    t = T[g["train"]]
    rows = t["stops"]
    st = [x[0] for x in rows]
    # locate the leg in the package rows: first occurrence of the leg's station run
    for p in range(len(st)):
        if st[p:p + len(g["stations"])] == g["stations"]:
            break
    else:
        continue
    c = eng.compose(t, rows, p, p + len(g["stations"]) - 1)
    if c is None:
        continue
    diff.append(c - direct)
    if not g["noted"]:
        err_c.append(c - g["T"]); err_d.append(direct - g["T"]); ho.append(g["test"])
d = np.array(diff)
print(f"legs {len(d)}: composed - whole-leg model: median {np.median(d):+.1f} s, mean |d| {np.mean(np.abs(d)):.1f} s, "
      f"p95 |d| {np.percentile(np.abs(d), 95):.1f} s, > 30 s {np.mean(np.abs(d) > 30):.1%}")
ho = np.array(ho); ec = np.array(err_c); ed = np.array(err_d)
for lab, e in (("whole-leg model", ed), ("composed (package)", ec)):
    a = np.abs(e[ho]) / 60
    print(f"held-out, no notes, {lab:20s}: <0.5 {np.mean(a < .5):.1%}  0.5-1 {np.mean((a >= .5) & (a <= 1)):.1%}  >1 {np.mean(a > 1):.1%}  (n {len(a)})")
