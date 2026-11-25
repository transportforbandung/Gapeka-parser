"""Simulator data package (docs/simulator_package.md, phase 1): one compressed JSON file per GAPEKA edition built from
gapeka.db, infra.db and rules.db, plus a manifest.

    python export_package.py [--timetable gapeka.db] [--infra infra.db] [--rules rules.db] [--out dist]

Writes dist/<edition>/package.json.gz and dist/<edition>/manifest.json, validated against schema/package.schema.json.
Units: times in seconds from 00:00 of the train's first day (may pass 86400), km, km/h, m. Keys: station codes, train
numbers. Origin of times: printed (GAPEKA), model (running-time model), graph (read from the graph sheets), manual.
Compact rows are arrays whose field order is given in the schema (prefixItems) and in package['fields'].
"""
import argparse
import datetime
import gzip
import hashlib
import json
import os
import re
import sqlite3

import gapeka_model

SCHEMA_VERSION = "1.0.0"
HERE = os.path.dirname(os.path.abspath(__file__))

# field order of the compact rows (also documented in the schema)
FIELDS = {
    "stops": ["station", "kind", "arr_s", "dep_s", "vmax_kmh", "time_origin"],
    "restrictions": ["offset_from_km", "offset_to_km", "speed_kmh", "tracks", "note"],
    "block_signals": ["offset_km", "track", "signal_no", "km", "kind"],
    "line_stations": ["station", "km", "km_alt", "elevation_m"],
    "tracks": ["track", "kind", "length_m", "usable", "usable_towards", "enter", "through", "out_of_service", "arrows"],
    "corrections": ["kind", "a", "b", "end_kind", "train_type", "seconds", "support"],
    "profiles": ["a", "b", "variant", "context", "km", "umum_kmh", "ruling_gradient_permil", "derived", "restrictions"],
    "profile_restrictions": ["from_km", "to_km", "speed_kmh", "kind"],
}
BLOCK = {"closed automatic block": "closed_abs", "open automatic block": "open_abs",
         "electromechanical block": "electromechanical", "none": "none"}
HALT_METHOD_ORIGIN = {"manual": "manual", "dinas-graph": "graph"}     # everything else from halt_est is 'model'
STOP_KIND = {"origin_or_end": "terminal", "stop": "stop", "pass": "pass", "halt": "halt", "via": "via"}


def rows(db, sql, *args):
    cur = db.execute(sql, args)
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, r)) for r in cur.fetchall()]


def clean(d):
    """drop None values (smaller file; absent = unknown)"""
    return {k: v for k, v in d.items() if v is not None and v != ""}


def repair_segments(st):
    """Estimated times between two printed times must lie between them and increase (KA Dinas: graph and model passing
    times can disagree, or overshoot the printed end). A stretch that breaks this is spread evenly between the two
    printed times and marked 'model'. Returns the number of stops changed. Times are still the 0-86400 clock here, so a
    stretch across midnight is unwrapped first."""
    def t(row):
        return row[2] if row[2] is not None else row[3]
    anchors = [k for k, row in enumerate(st) if row[5] == "printed" and t(row) is not None]
    changed = 0
    for p, q in zip(anchors, anchors[1:]):
        if q - p < 2:
            continue
        lo = st[p][3] if st[p][3] is not None else st[p][2]
        hi = st[q][2] if st[q][2] is not None else st[q][3]
        if hi < lo:
            hi += 86400
        vals, ok, last = [], True, lo
        for row in st[p + 1:q]:
            for j in (2, 3):
                if row[j] is not None:
                    x = row[j] + (86400 if row[j] < lo - 43200 else 0)
                    ok &= last <= x <= hi
                    last = x
        if ok:
            continue
        n = q - p
        for k, row in enumerate(st[p + 1:q], 1):
            x = round(lo + (hi - lo) * k / n) % 86400
            row[2] = row[3] = x
            row[5] = "model"
            changed += 1
    return changed


def run_class(ttype, service):
    """train type for the running-time model; non-KRL commuters with EMU performance (Ka Feeder) run like KRL"""
    import gapeka_model as gm
    if ttype == "commuter" and service in gm.EMU_LIKE:
        return "commuter-emu"
    return ttype


def build(tt_path, infra_path, rules_path):
    g, i, r = sqlite3.connect(tt_path), sqlite3.connect(infra_path), sqlite3.connect(rules_path)
    meta_i = dict(i.execute("select key, value from meta"))
    edition = "GAPEKA " + (meta_i.get("decree", "").split("TAHUN")[-1].strip() or "unknown")

    # ---- stations: infra.db (every timetable station is there), CTC / joint dispatch / attributes
    attrs = {}
    for a in rows(r, "select station, attribute, value from station_attributes"):
        attrs.setdefault(a["station"], {})[a["attribute"]] = a["value"]
    jd = {}
    for a in rows(i, "select station, dispatcher from joint_dispatch"):
        jd[a["station"]] = a["dispatcher"]
    stations = {}
    for s in rows(i, "select code, name, kind, ctc, sheet_name from stations"):
        stations[s["code"]] = clean(dict(
            name=s["name"] or None, kind={"sheet only": "sheet_only"}.get(s["kind"], s["kind"]),
            ctc=bool(s["ctc"]) or None, dispatcher=jd.get(s["code"]), sheet_name=s["sheet_name"],
            attributes=attrs.get(s["code"])))

    for c, in i.execute("select a from sections union select b from sections union select code from line_stations"):
        stations.setdefault(c, dict(kind="sheet_only"))     # sheet rows without a stations row (e.g. ?stadion)

    # ---- lines (station order per graph-sheet group, for drawing the graph)
    lines = []
    for ln in rows(i, "select line_id, title, pages from lines"):
        st = [[x["code"], x["km"], x["km_alt"], x["elevation_m"]] for x in
              rows(i, "select code, km, km_alt, elevation_m from line_stations where line_id = ? order by seq",
                   ln["line_id"])]
        lines.append(dict(id=ln["line_id"], title=ln["title"], pages=ln["pages"], stations=st))

    # ---- sections with restrictions, block signals, block system
    sysm = {(s["a"], s["b"], s["variant"] or ""): s for s in rows(i, "select * from section_systems")}
    restr, blocks = {}, {}
    for x in rows(i, "select * from restrictions"):
        restr.setdefault((x["a"], x["b"], x["variant"] or ""), []).append(
            [x["offset_from_km"], x["offset_to_km"], x["speed_kmh"], x["tracks"] or "", x["note"] or ""])
    for x in rows(i, "select * from block_signals"):
        blocks.setdefault((x["a"], x["b"], x["variant"] or ""), []).append(
            [x["offset_km"], x["track"] or "", x["signal_no"] or "", x["km"], x["kind"]])
    sections = []
    for s in rows(i, "select * from sections order by a, b, variant"):
        k = (s["a"], s["b"], s["variant"] or "")
        sy = sysm.get(k, {})
        sections.append(clean(dict(
            a=s["a"], b=s["b"], variant=s["variant"] or None, context=s["context"] if s["variant"] else None,
            line=s["line_id"], km=s["km"], km_a=s["km_a"], km_b=s["km_b"], umum_kmh=s["umum_kmh"],
            ruling_gradient_permil=s["ruling_gradient_permil"], double_track=bool(s["double_track"]),
            block_system=BLOCK.get(sy.get("block_system")), electrified=bool(sy.get("electrified")) if sy else None,
            restrictions=restr.get(k), block_signals=blocks.get(k),
            origin="manual" if "manual" in (s["source"] or "") else "sheet", source=s["source"], page=s["page"])))

    # ---- station track layouts (one per station and line)
    layouts = []
    for L in rows(i, "select * from station_layouts order by station, line_id"):
        tr = [[t["track"], t["kind"], t["length_m"], t["usable"] or "", t["usable_towards"] or "", t["enter"] or "",
               bool(t["through"]), bool(t["out_of_service"]), t["arrows"] or ""]
              for t in rows(i, "select * from station_tracks where station = ? and line_id = ? and page = ? order by seq",
                            L["station"], L["line_id"], L["page"])]
        marks = [m["towards"] for m in rows(i, "select towards from station_signal_marks where station = ? and line_id = ?",
                                            L["station"], L["line_id"])]
        layouts.append(clean(dict(station=L["station"], line=L["line_id"], page=L["page"], ends=[L["left_ends"],
                                  L["right_ends"]], longest_roman_m=L["longest_roman_m"], ppka=bool(L["ppka"]),
                                  tracks=tr, entry_signal_grade_from=marks or None)))
    layout_refs = [clean(x) for x in rows(i, "select station, line_id as line, see_line, note from station_layout_refs")]

    # ---- dinas tutup and capacity
    hours = {}
    for h in rows(r, "select * from dinas_tutup_hours"):
        hours[h["station"]] = clean(dict(closed_from=h["closed_from"], closed_to=h["closed_to"], hours=h["hours"],
                                         note=h["note"]))
    dinas_tutup = [dict(ends=[d["end_a"], d["end_b"]], closable=(d["closed"] or "").split(), page=d["page"],
                        hours={c: hours[c] for c in (d["closed"] or "").split() if c in hours})
                   for d in rows(i, "select * from dinas_tutup")]
    capacity = [clean(dict(a=c["a"], b=c["b"], path=(c["path"] or "").split() or None, capacity=c["capacity"],
                           load_regular=c["timetable_regular"], load_facultative=c["timetable_facultative"],
                           printed_program=[c["program_regular"], c["program_facultative"]],
                           printed_adds_up=bool(c["counts_add_up"]), page=c["page"]))
                for c in rows(i, "select * from section_capacity")]

    # ---- trains and stops (printed times + estimates for halts / KA Dinas passing points)
    idx = {}
    for t in rows(g, "select train_no, name, identity, route, jenis from train_index"):
        idx.setdefault(t["train_no"], t)
    cons = {c["train_no"]: c for c in rows(g, "select * from consists")}
    pokok = {}
    for p in rows(g, "select train_no, wagon, locomotive, kind from rangkaian_pokok"):
        pokok.setdefault(p["train_no"], p)
    est = {(e["train_no"], e["seq"]): e for e in rows(g, "select * from halt_est")}
    import gapeka_model as gm
    import physics_ref
    infra_m = gm.Infra(infra_path, g)
    profiles = physics_ref.export_profiles(infra_m, g)
    syms = {}
    for no, st_, sym in g.execute("select train_no, station, symbol from notes"):
        syms.setdefault(no, {}).setdefault(st_, set()).add(sym)
    trains, repaired = [], 0
    for t in rows(g, "select * from trains order by cast(no as integer), no"):
        st = []
        for s in rows(g, "select * from stops where train_no = ? order by seq", t["no"]):
            a, d, origin = s["arr_s"], s["dep_s"], "printed"
            if s["kind"] in ("halt", "via") or (a is None and d is None):
                e = est.get((t["no"], s["seq"]))
                if e:
                    a, d = e["arr_s"], e["dep_s"]
                    origin = HALT_METHOD_ORIGIN.get(e["method"], "model")
                else:
                    origin = None
            if s["kind"] in ("pass", "halt", "via") and origin != "printed" and a is not None and d is not None \
                    and a > d and s["kind"] == "pass":
                a = d = round((a + d) / 2)                 # graph reading: a passing point read 1-35 s apart
            st.append([s["station"], STOP_KIND.get(s["kind"], s["kind"]), a, d, s["vmax"], origin])
        repaired += repair_segments(st)
        # past midnight: times continue above 86400 so they always increase along the train
        day, last = 0, None
        for row in st:
            for j in (2, 3):
                if row[j] is None:
                    continue
                x = row[j] + day
                if last is not None and x < last - 43200:
                    day += 86400
                    x += 86400
                row[j], last = x, x
        ix, c, pk = idx.get(t["no"], {}), cons.get(t["no"]), pokok.get(t["no"])
        consist = clean(dict(cars=c["cars"], sets=c["sets"], locomotive=c["locomotive"],
                             wagon=pk["wagon"] if pk else None)) if c else None
        trains.append(clean(dict(
            no=t["no"], name=ix.get("name") or t["service"], identity=ix.get("identity"), route=t["route"],
            type=t["type"], class_=t["kelas"], classification=t["klasifikasi"], facultative=bool(t["facultative"]),
            operator=t["operator"], km=t["index_km"], consist=consist,
            graph_only=(t["first_page"] or "").startswith("graph:") or None,
            run_class=run_class(t["type"], t["service"] or ""),
            notes={k: " ".join(sorted(v)) for k, v in syms.get(t["no"], {}).items()} or None, stops=st)))
    for t in trains:
        t["class"] = t.pop("class_", None)
        if t["class"] is None:
            del t["class"]

    # ---- waits from the timetable notes (who waits for whom), duties (turnarounds)
    known = {t["no"] for t in trains}

    def train_ref(n):
        """train numbers in the timetable notes: strip note words ('2539F JJ'); a facultative train may be written
        without its F ('2640' = 2640F) or the other way round"""
        if not n:
            return None
        m = re.match(r"[RL]?\d+F?(?:-\d+)?", n.strip().upper())
        n = m.group(0) if m else n.split()[0].upper()
        for c in (n, n + "F", n[:-1] if n.endswith("F") else None):
            if c and c in known:
                return c
        return n
    waits = []
    for c in rows(g, "select * from constraints order by id"):
        w = clean(dict(kind=c["kind"], station=c["station"], trains=[train_ref(c["train_a"]), train_ref(c["train_b"])],
                       waiter=train_ref(c["waiter"]), overtaker=train_ref(c["overtaker"])))
        unknown = [n for n in w["trains"] if n and n not in known]
        if unknown:
            w["unknown_trains"] = unknown            # referenced in a note but not in the timetable
        waits.append(w)
    duties = []
    for sid, kind in g.execute("select distinct set_id, kind from rangkaian_pokok order by set_id"):
        ch = rows(g, "select train_no, cars, locomotive, wagon, sets from rangkaian_pokok where set_id = ? order by seq", sid)
        duties.append(clean(dict(id=f"set{sid}", kind=kind, trains=[x["train_no"] for x in ch], cars=ch[0]["cars"],
                                 locomotive=ch[0]["locomotive"], wagon=ch[0]["wagon"], sets=ch[0]["sets"],
                                 source="rangkaian pokok")))
    for u in rows(g, "select * from roster_units where kind = 'loco' order by unit_id"):
        ch = [x["train_no"] for x in rows(g, "select train_no from roster_duties where unit_id = ? order by seq",
                                          u["unit_id"])]
        duties.append(dict(id=f"loco{u['unit_id']}", kind="loco", unit=u["unit"], trains=ch, source="dinasan O.18"))

    # ---- running-time model and rules (phase 3 turns the prose rules into parameters)
    model = dict(
        physics={k: dict(a0=v[0], power=v[1], brake=v[2]) for k, v in gapeka_model.PHYS.items()},
        cell_m=gapeka_model.DX, min_speed_kmh=8, algorithm="physics_ref.py (docstring)",
        emu_like_services=sorted(gapeka_model.EMU_LIKE),
        calibration=[clean(x) for x in rows(r, "select train_type, feature, value from fitted_calibration")],
        corrections=[[x["kind"], x["a"], x["b"], x["end_kind"], x["train_type"], round(x["seconds"], 2), x["support"]]
                     for x in rows(r, "select * from fitted_corrections")],
        station_zones=[clean(x) for x in rows(r, "select * from station_zones")],
        section_buffers=[clean(x) for x in rows(r, "select * from section_buffers")],
        station_buffers=[clean(x) for x in rows(r, "select * from station_buffers")],
        halt_rules=[clean(x) for x in rows(r, "select * from halt_rules")],
        halt_offsets=[clean(x) for x in rows(r, "select * from halt_offsets")],
        fitted_halt_offsets=[clean(x) for x in rows(r, "select * from fitted_halt_offsets")],
        fit_stats={k: json.loads(v) if v[:1] in "[{" else v for k, v in r.execute("select key, value from fit_stats")})
    rules = dict(
        priority=[dict(id=f"{x['event']}-{n}", **clean(x)) for n, x in
                  enumerate(rows(r, "select * from priority"), 1)],
        wait_gaps=[clean(x) for x in rows(r, "select * from wait_gaps")],
        mandatory_stops=[clean(x) for x in rows(r, "select * from mandatory_stops")],
        fit_exemptions=[clean(x) for x in rows(r, "select * from fit_exemptions")])

    print(f"stops re-spread between printed times (inconsistent estimates): {repaired}")
    meta = dict(
        edition=edition, decree=meta_i.get("decree"), valid_from=meta_i.get("valid_from"), pdf=meta_i.get("pdf"),
        pdf_sha256=meta_i.get("pdf_sha256"), schema_version=SCHEMA_VERSION,
        built=datetime.datetime.now().isoformat(timespec="seconds"),
        units=dict(time="seconds from 00:00 of the train's first day (may pass 86400)", distance="km",
                   speed="km/h", length="m"),
        time_origins=dict(printed="as printed in GAPEKA", model="running-time model estimate (not official)",
                          graph="read from the GAPEKA graph sheets (not printed in the timetable)",
                          manual="set by hand (user decision)"),
        disclaimer="Unofficial data package built from the GAPEKA PDF; only 'printed' times are official. "
                   "Licence and public disclaimer still to be decided (docs/simulator_package.md).")
    return dict(meta=meta, fields=FIELDS, profiles=profiles, stations=stations, lines=lines, sections=sections, layouts=layouts,
                layout_refs=layout_refs, dinas_tutup=dinas_tutup, capacity=capacity, trains=trains, waits=waits,
                duties=duties, model=model, rules=rules)


def write(pkg, out_dir):
    folder = os.path.join(out_dir, pkg["meta"]["edition"].lower().replace(" ", "-"))
    os.makedirs(folder, exist_ok=True)
    raw = json.dumps(pkg, ensure_ascii=False, separators=(",", ":"), sort_keys=False).encode()
    path = os.path.join(folder, "package.json.gz")
    with open(path, "wb") as fh:                       # mtime 0: the same data gives the same file
        with gzip.GzipFile(fileobj=fh, mode="wb", mtime=0, compresslevel=9) as gz:
            gz.write(raw)
    data = open(path, "rb").read()
    manifest = dict({k: pkg["meta"][k] for k in ("edition", "decree", "valid_from", "pdf_sha256", "schema_version",
                                                  "built")},
                    file="package.json.gz", bytes=len(data), bytes_uncompressed=len(raw),
                    sha256=hashlib.sha256(data).hexdigest(),
                    counts={k: len(pkg[k]) for k in ("stations", "lines", "sections", "layouts", "trains", "waits",
                                                     "duties", "capacity", "dinas_tutup")})
    json.dump(manifest, open(os.path.join(folder, "manifest.json"), "w"), indent=1, ensure_ascii=False)
    return folder, manifest


def validate(pkg, schema_path=os.path.join(HERE, "schema", "package.schema.json")):
    import jsonschema
    jsonschema.validate(pkg, json.load(open(schema_path)))


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--timetable", default="gapeka.db")
    ap.add_argument("--infra", default="infra.db")
    ap.add_argument("--rules", default="rules.db")
    ap.add_argument("--out", default="dist")
    a = ap.parse_args()
    pkg = build(a.timetable, a.infra, a.rules)
    validate(pkg)
    folder, m = write(pkg, a.out)
    print(f"package {folder}: {m['bytes'] / 1e6:.2f} MB compressed ({m['bytes_uncompressed'] / 1e6:.1f} MB raw), "
          f"{m['counts']}")


if __name__ == "__main__":
    main()
