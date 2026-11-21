import os
os.environ.setdefault("RULES", "rules.json")
import numpy as np
import relearn as R
legs, y, base = R.legs, R.y, R.base
def f(s): return "%d:%02d" % (s // 3600 % 24, s % 3600 // 60)
print("\nfreight legs touching Yogyakarta (physics only; err = model - GAPEKA, minus = GAPEKA slower)")
for i, g in enumerate(legs):
    st = g["stations"]
    if g["type"] == "freight" and "YK" in (st[0], st[-1]):
        print(f"  {g['train']:>6s} {g['service']:22s} {'-'.join(st):18s} {('stop' if g['start_rest'] else 'pass')}->{('stop' if g['end_rest'] else 'pass')}"
              f"  GAPEKA {y[i] / 60:4.1f} min  model {base[i] / 60:4.1f} min  err {(base[i] - y[i]):+4.0f} s  note {g['noted']}")
print("\nNambo branch KRL legs (physics only)")
for i, g in enumerate(legs):
    st = g["stations"]
    if g["type"] == "krl" and set(st) & {"CBN", "NMO"} and not g["noted"] and g["train"] in ("1500", "1501", "1502", "1503"):
        print(f"  {g['train']:>6s} {'-'.join(st):22s} GAPEKA {y[i] / 60:4.1f} min  model {base[i] / 60:4.1f} min  extra in GAPEKA {(y[i] - base[i]):+4.0f} s")
