import json
from collections import defaultdict
import numpy as np
exec(open("waits2.py").read().split("# 1) crossing where both stop")[0])
P = json.load(open("line_profile.json"))
LINES = [(7, 12, "Duri-Tangerang, Tanahabang-Merak, Bandara"), (13, 18, "Jakartakota-Jatinegara-Bekasi, Tanjungpriuk"),
         (19, 24, "Manggarai-Tanahabang-Duri, Bogor (OCR)"), (25, 27, "Cikampek-Bandung-Banjar, Garut"), (28, 28, "Sukabumi-Padalarang"),
         (29, 31, "Cikampek-Cirebon-Tegal, Prupuk"), (32, 34, "Tegal-Semarang-Cepu, Gundih"), (35, 37, "Prupuk-Purwokerto-Kutoarjo"),
         (38, 40, "Kutoarjo-Yogyakarta-Solo"), (41, 43, "Kertosono-Wlingi"), (44, 46, "Cepu-Surabaya, Mojokerto"),
         (47, 49, "Malang-Bangil-Sidoarjo"), (50, 51, "Bangil-Kalisat-Ketapang")]
st_page = {}
for k, p in P.items():
    for s in k.split("-"):
        st_page.setdefault(s, p.get("page", 0))
def line(s):
    pg = st_page.get(s, 0)
    return next((n for a, b, n in LINES if a <= pg <= b), "(no sheet)")
tab = defaultdict(lambda: defaultdict(list))
for (s, w), evs in groups.items():
    W = at(w, s)
    for kind, ov in evs:
        c = ref_times(kind, s, w, ov)
        if kind == "cross":
            r = c.get("other arrives/passes")
            if r is not None: tab[line(s)]["cross"].append(wrap(W[4] - r) / 60)
        else:
            rs, rn = c.get("overtaker passes station"), c.get("overtaker at next station")
            if rs is not None: tab[line(s)]["ot_vs_station"].append(wrap(W[4] - rs) / 60)
            if rn is not None: tab[line(s)]["ot_vs_next"].append(wrap(W[4] - rn) / 60)
print(f"{'line':45s} {'crossing: dep - other':>28s} | {'overtake: dep - overtaker at station':>36s} | {'dep - overtaker at next station':>32s}")
def cell(v):
    if len(v) < 4: return f"{'-':>30s}"
    v = np.array(v); md = np.median(v)
    return f"n={len(v):3d} med {md:+5.1f} ±0.5 {np.mean(np.abs(v - md) <= .5):4.0%}"
for ln in sorted(tab, key=lambda l: -sum(len(x) for x in tab[l].values())):
    t = tab[ln]
    print(f"{ln:45s} {cell(t['cross']):>28s} | {cell(t['ot_vs_station']):>36s} | {cell(t['ot_vs_next']):>32s}")
