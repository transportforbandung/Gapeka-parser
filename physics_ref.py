"""Running-time physics for the simulator, pure Python (no numpy), written to be ported line by line to the browser
engine. It reproduces gapeka_model's physics (make_leg + apply_zones + simulate) for ANY train: any vmax, any stopping
pattern, any run class, so trains that are not in GAPEKA are timed the same way as those that are.

Inputs (all in the package):
  profiles      per directed station pair (and junction variant): km, general limit (Umum), ruling gradient, and the
                restrictions that apply to the track of this direction [from_km, to_km, kmh, kind]; kind 'line' applies
                to every train, 'platform-track' only to EMU-like trains (KRL, Ka Feeder). Pairs that are not one sheet
                section (joined / split / derived) are resolved in Python when the package is built.
  model.physics acceleration a0 (m/s2), power (a = min(a0, power / v)), braking (m/s2) per physics group
  model.station_zones  slow zones at stations (speed over a length on the departure / arrival side, by train type)
Algorithm (one leg between two points where the train is timed):
  1. speed cap per 50 m cell: per section min(train vmax, Umum), lowered by its restrictions; cells per section =
     max(1, round(km * 1000 / 50)); a restriction covers cells int(from * 20) .. max(int(to * 20), first + 1)
  2. station zones lower the first / last cells of the leg
  3. node speeds: node 0 = cap of cell 0, node j = min(cap j-1, cap j), last node = cap of the last cell; 0 where the
     train is at rest (leg start / end if it stops there, each halt)
  4. forward: v(j+1) = min(node(j+1), sqrt(max(v(j)^2 + 2 * 50 * min(a0, power / max(v(j), 1)), (8 / 3.6)^2)))
     backward: v(j) = min(v(j), sqrt(v(j+1)^2 + 2 * 50 * brake))
     (squares as v * v, never v ** 2: pow() differs from v * v in the last bit for ~0.1 % of values, and the browser
     engine must give the same doubles; numpy's v ** 2 in gapeka_model is v * v too)
  5. time = sum over cells of 2 * 50 / max(v(j) + v(j+1), 0.001)
"""
import math

DX = 50.0
V_MIN = 8 / 3.6


def phys_group(run_class):
    return {"krl": "krl", "commuter-emu": "krl", "freight": "freight"}.get(run_class, "loco/krd")


def train_type(run_class):
    return "commuter" if run_class == "commuter-emu" else run_class


# ------------------------------------------------------------------------------------------------ package side
class Profiles:
    def __init__(self, rows):
        """rows: package 'profiles' [a, b, variant, context, km, umum, ruling, derived, restrictions, double_track]"""
        self.by = {}
        for a, b, var, ctx, km, umum, ruling, derived, restr, dbl in rows:
            self.by.setdefault((a, b), []).append(dict(variant=var, ctx=set(ctx.split()), km=km, umum=umum, double=dbl,
                                                       ruling=ruling, restr=restr))

    def get(self, a, b, ctx):
        """the variant whose context holds one of the leg's stations, else the default"""
        vs = self.by.get((a, b))
        if not vs:
            return None
        for v in vs:
            if v["variant"] and v["ctx"] & ctx:
                return v
        for v in vs:
            if not v["variant"]:
                return v
        return vs[0]


def leg_cap(profiles, zones, stations, vmaxes, run_class, start_rest, end_rest, halt_idx=(), first=False, last=False):
    """speed cap per cell (km/h), sections [(km, ruling)], halt cells; None if a pair or a vmax is missing"""
    emu = phys_group(run_class) == "krl"
    typ = train_type(run_class)
    ctx = set(stations)
    cap, secs, halts = [], [], []
    for i in range(len(stations) - 1):
        pr = profiles.get(stations[i], stations[i + 1], ctx)
        v = vmaxes[i]
        if pr is None or not v:
            return None
        km = pr["km"]
        n = max(1, int(round(km * 1000 / DX)))
        c = [float(min(v, pr["umum"] or v))] * n
        for o0, o1, rv, kind in pr["restr"]:
            if kind == "platform-track" and not emu:
                continue
            i0 = int(o0 * 1000 / DX)
            i1 = max(int(o1 * 1000 / DX), i0 + 1)
            for j in range(i0, min(i1, n)):
                c[j] = min(c[j], rv)
        cap += c
        secs.append((km, pr["ruling"]))
        if i + 1 in halt_idx:
            halts.append(len(cap))
    # station zones (rules/station_zones.csv as in the package)
    for z in zones:
        if typ not in z["train_types"].split():
            continue
        for side, L in (("dep", int(z["dep_length_m"] or 0)), ("arr", int(z["arr_length_m"] or 0))):
            n = min(int(L / DX), len(cap))
            if not L or not n:
                continue
            at, nb, rest = (stations[0], stations[1], start_rest) if side == "dep" else \
                (stations[-1], stations[-2], end_rest)
            if at != z["station"] or (z["towards"] != "*" and nb != z["towards"]):
                continue
            terminal = first if side == "dep" else last
            w = z["applies_when"]
            if (w == "stop" and not rest) or (w == "pass" and rest) or (w == "midstop" and not (rest and not terminal)) \
                    or (w == "terminal" and not (rest and terminal)):
                continue
            sp = float(z["speed_kmh"])
            rng = range(n) if side == "dep" else range(len(cap) - n, len(cap))
            for j in rng:
                cap[j] = min(cap[j], sp)
    return cap, secs, halts


def simulate(cap_kmh, start_rest, end_rest, halts, a0, power, brake):
    """running time (s) over the cells"""
    n = len(cap_kmh)
    cap = [c / 3.6 for c in cap_kmh]
    node = [0.0] * (n + 1)
    node[0] = cap[0]
    for j in range(1, n):
        node[j] = min(cap[j - 1], cap[j])
    node[n] = cap[n - 1]
    if start_rest:
        node[0] = 0.0
    if end_rest:
        node[n] = 0.0
    for h in halts:
        node[min(max(h, 1), n - 1)] = 0.0
    v = [0.0] * (n + 1)
    v[0] = node[0]
    for j in range(n):
        acc = min(a0, power / max(v[j], 1.0))
        v[j + 1] = min(node[j + 1], math.sqrt(max(v[j] * v[j] + 2 * DX * acc, V_MIN * V_MIN)))
    for j in range(n - 1, -1, -1):
        v[j] = min(v[j], math.sqrt(v[j + 1] * v[j + 1] + 2 * DX * brake))
    return sum(2 * DX / max(v[j] + v[j + 1], 1e-3) for j in range(n))


# ------------------------------------------------------------------------------------------------ build side
def export_profiles(infra, ttdb, double=None):
    """package rows for every directed station pair a timetable train runs (halts included) and every sheet section
    in both directions; variants of a sheet section exported separately with their context. double: {frozenset(a, b):
    0 / 1} of the sheet sections; a pair that is not one sheet section is double track if every sheet section on its
    shortest sheet route is"""
    import gapeka_model as gm
    double = double or {}

    def is_double(a, b):
        k = frozenset((a, b))
        if k in double:
            return bool(double[k])
        pth = infra.path(a, b, max_km=60)
        if not pth or len(pth) < 2:
            return False
        return all(double.get(frozenset(xy), 0) for xy in zip(pth, pth[1:]))
    pairs = set()
    prev = {}
    for no, st in ttdb.execute("select train_no, station from stops order by train_no, seq"):
        if no in prev and prev[no] != st:
            pairs.add((prev[no], st))
        prev[no] = st
    for a, b in infra.sec:
        pairs.add((a, b))
        pairs.add((b, a))
    rows = []
    for a, b in sorted(pairs):
        vs = infra.sec.get((a, b)) or infra.sec.get((b, a)) or []
        variants = [v for v in vs if v["variant"]]
        todo = [(v["variant"], " ".join(sorted(v["ctx"])), frozenset(v["ctx"])) for v in variants] + [("", "", frozenset())]
        for var, ctxs, ctx in todo:
            p = infra.directed(a, b, ctx) if vs else infra.profile(a, b, ctx)
            if p is None:
                continue
            restr = []
            for o0, o1, v, tracks, note, down in p["restr"]:
                kind = gm.track_kind(note)
                if kind == "siding":
                    continue
                if (tracks == "hu" and not down) or (tracks == "hi" and down):
                    continue                     # the other track of a double track
                restr.append([o0, o1, int(v), kind])
            rows.append([a, b, var, ctxs, p["km"], p["umum"], p["ruling"], p.get("derived") or "", restr,
                         is_double(a, b)])
    return rows
