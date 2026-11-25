"""Block signals the graph sheet does not print, inferred from the timetable (pipeline step 10, before the package's
headways are fitted).

Why: the sheet can leave out automatic block signals that the line has (Cikini - Manggarai, page 13: none printed,
user: the line has several). The planner's double-track rule (a train enters block k only when the train ahead has
passed point k+1; block points = stations + block signals, halts excluded) then says GAPEKA's own timetable is
impossible there.

Rule (evidence first, never on a guess):
  1. For every double-track station block (two consecutive non-halt stations) count GAPEKA's consecutive
     same-direction train pairs that break the block rule with the printed signals (59 s tolerance, times at the halts
     inside the block, KA Dinas excluded).
  2. If pairs break it, look at the sheet sections on the block's route, in that direction: only sections with an
     automatic block system (closed / open ABS), on double track, with NO signal printed for that track, and without a
     manual entry (manual/block_signals.csv wins). Fewer than MIN_PAIRS (3) broken pairs: only a qa row (one odd pair
     is not enough evidence to add signals).
  3. Add evenly spaced signals to the longest such section, the number from the spacing next to it: the median block
     length of the printed signals on the neighbouring sections of the same sheet line (sharing a station with it),
     else that line's median, else the network median; n = max(1, round(km / spacing) - 1).
     Keep it only if it removes the broken pairs; otherwise nothing is added and the block is listed for review.
  4. Every inferred signal: kind 'inferred: ...', and a qa row; blocks that still break the rule are qa rows too.
"""
import statistics
from collections import defaultdict

TOL = 59.0
MIN_PAIRS = 3          # evidence: at least this many GAPEKA pairs must break the rule before a signal is inferred
KIND = "inferred: GAPEKA pairs need a block signal here; evenly spaced like the neighbouring sections"


def _violations(tl, ttype, key, knot_time):
    f = tl.bpts.get(key, (0.0, 1.0))
    occ = sorted(o for o in tl.block_base.get(key, []) if ttype.get(o[2]) != "dinas")
    bad = []
    for i, (e1, x1, n1) in enumerate(occ):
        k1 = tl.knots.get((key[0], key[1], n1))
        for e2, x2, n2 in occ[i + 1:]:
            if e2 > x1 + 600:
                break
            k2 = tl.knots.get((key[0], key[1], n2))
            A = lambda g, c: e1 + knot_time(k1, g, x1 - e1, c)
            B = lambda g, c: e2 + knot_time(k2, g, x2 - e2, c)
            n = len(f) - 1
            behind = all(B(f[k], False) >= A(f[k + 1], True) - TOL for k in range(n))
            ahead = all(A(f[k], False) >= B(f[k + 1], True) - TOL for k in range(n))
            if not (behind or ahead):
                bad.append((n1, n2))
    return bad


def infer(pkg):
    """adds inferred signals to pkg['sections'] in place; returns (added rows, qa rows). added row = (a, b, offset_km,
    track, kind); offsets from a in sheet order (hulu = a -> b, hilir measured from a too, as the sheet does)"""
    import planner
    from planner import knot_time
    ttype = {t["no"]: t["type"] for t in pkg["trains"]}
    secs = {(s["a"], s["b"]): s for s in pkg["sections"] if not s.get("variant")}
    # line spacing: median block length of the printed signals per sheet line (sections with >= 1 signal on a track)
    spacing = defaultdict(list)
    for s in secs.values():
        for tr in ("hu", "hi"):
            n = sum(1 for b in s.get("block_signals", []) if tr in (b[1] or "hu hi").split())
            if n:
                spacing[s.get("line")].append(s["km"] / (n + 1))
    own = {}                                  # section -> its printed block lengths
    for s in secs.values():
        v = []
        for tr in ("hu", "hi"):
            n = sum(1 for b in s.get("block_signals", []) if tr in (b[1] or "hu hi").split())
            if n:
                v.append(s["km"] / (n + 1))
        if v:
            own[(s["a"], s["b"])] = v

    def near_spacing(s):
        v = [x for (a, b), xs in own.items() if (a, b) != (s["a"], s["b"]) and {a, b} & {s["a"], s["b"]}
             and secs[(a, b)].get("line") == s.get("line") for x in xs]
        return statistics.median(v) if v else None
    all_sp = [x for v in spacing.values() for x in v]
    net = statistics.median(all_sp) if all_sp else 0.57
    line_sp = {k: statistics.median(v) for k, v in spacing.items()}

    tl = planner.Timeline(pkg)
    added, qa = [], []
    todo = sorted((k for k in tl.block_base if tl.block_double.get(k)), key=str)
    for key in todo:
        bad = _violations(tl, ttype, key, knot_time)
        if not bad:
            continue
        if len(bad) < MIN_PAIRS:
            qa.append(("block rule broken by GAPEKA (too few pairs to infer a signal; check)", f"{key[0]}-{key[1]}",
                       f"{len(bad)} pairs: {bad}"))
            continue
        route = tl.sheet_route(key[0], key[1], 60) or []
        cand = []
        for u, v in zip(route, route[1:]):
            s, fwd = (secs[(u, v)], True) if (u, v) in secs else (secs.get((v, u)), False)
            if not s or not s.get("double_track") or s.get("block_system") not in ("closed_abs", "open_abs"):
                continue
            tr = "hu" if fwd else "hi"
            if any(tr in (b[1] or "hu hi").split() for b in s.get("block_signals", [])):
                continue
            if any(str(b[4]).startswith("manual") for b in s.get("block_signals", [])):
                continue
            cand.append((s["km"], s, tr))
        if not cand:
            qa.append(("block rule broken by GAPEKA, no section to add a signal", f"{key[0]}-{key[1]}",
                       f"{len(bad)} pairs, e.g. {bad[:3]}"))
            continue
        km, s, tr = max(cand, key=lambda c: c[0])
        sp = near_spacing(s) or line_sp.get(s.get("line"), net)
        n = max(1, round(km / sp) - 1)
        rows = [[round(km * k / (n + 1), 3), tr, "", None, KIND, "inferred"] for k in range(1, n + 1)]
        s.setdefault("block_signals", []).extend(rows)
        tl2 = planner.Timeline(pkg)
        still = _violations(tl2, ttype, key, knot_time)
        if len(still) >= len(bad):
            for r in rows:
                s["block_signals"].remove(r)
            qa.append(("block rule broken by GAPEKA, an inferred signal does not help", f"{key[0]}-{key[1]}",
                       f"{len(bad)} pairs, e.g. {bad[:3]}"))
            continue
        tl = tl2
        for r in rows:
            added.append((s["a"], s["b"], r[0], tr, KIND))
        qa.append(("block signals inferred from GAPEKA (check on the sheet)", f"{s['a']}-{s['b']} {tr}",
                   f"{n} evenly spaced ({km:.3f} km, line spacing {sp:.2f} km); block {key[0]}-{key[1]}: "
                   f"{len(bad)} -> {len(still)} GAPEKA pairs break the rule"))
        if still:
            qa.append(("block rule still broken after inference", f"{key[0]}-{key[1]}", f"{len(still)} pairs"))
    return added, qa


def write_infra(infra_path, added, qa):
    """store the inferred signals and the qa rows in infra.db (replacing an earlier run's)"""
    import sqlite3
    db = sqlite3.connect(infra_path)
    db.execute("delete from block_signals where kind like 'inferred:%'")
    db.execute("delete from qa where check_name like 'block signals inferred%' or check_name like 'block rule%'")
    for a, b, off, tr, kind in added:
        page = db.execute("select page from sections where a = ? and b = ?", (a, b)).fetchone()
        db.execute("insert into block_signals values(?,?,?,?,?,?,?,?,?)",
                   (a, b, off, tr, None, None, kind, page[0] if page else None, ""))
    db.executemany("insert into qa values(?,?,?)", qa)
    db.commit()
    db.close()
