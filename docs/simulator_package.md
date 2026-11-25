# Simulator data package: plan

Status: planning (Nov 2026). Decisions by the user, 25 Nov 2026:

| Decision | Choice |
|---|---|
| Audience | public from the start |
| Scope | all of Java |
| Engine | automatic re-planning (the engine applies the priority / wait / release rules and moves trains) |
| Format | one compressed JSON file per GAPEKA edition, with a JSON Schema and generated TypeScript types |

The pipeline (this repo) builds the package offline. A browser engine (TypeScript or WASM in a Web Worker) loads it.
A Python reference engine in this repo produces the expected results the browser engine is tested against.

## 1. Files

```
dist/gapeka-2026/
  package.json.gz        the data (target < 1.5 MB compressed; the three databases gzip to 2.4 MB today)
  manifest.json          edition, decree, valid-from, PDF hash, schema version, package hash, build date
  replay.json.gz         golden output of the Python engine (section 5)
schema/
  package.schema.json    JSON Schema; TypeScript types are generated from it
```

- Everything uses station codes and train numbers as keys.
- Times are in seconds from 00:00 and may pass 24 h for trains running past midnight. Distances are in km, speeds in km/h, lengths in m.
- Every value a user can see carries its origin: `printed` (GAPEKA as printed), `estimated` (model), `predicted`
  (inferred from the sheets, e.g. dinas tutup hours) or `manual` (user decision). A public site must show which
  times are official.

## 2. Contents

| Part | Contents | From |
|---|---|---|
| `meta` | edition, decree, valid from, sources, schema version, licence / disclaimer text | all |
| `stations` | code, official name, display name, kind (station / halt), CTC, joint dispatcher, km per line | infra.db |
| `lines` | station order per line as on the graph sheets (to draw the GAPEKA-style graph), km, elevation | infra.db |
| `sections` | a, b, variant + context, km, Umum, ruling gradient, double track, block system, electrified, block signals, restrictions (offsets, speed, track hu/hi) | infra.db |
| `tracks` | per station and line: track, Roman / yard, length (longest), usable directions, enter, out of service, entry-signal marks | infra.db |
| `dinas_tutup` | pieces (ends, closable stations), predicted closing hours per station | infra.db, rules.db |
| `capacity` | per capacity section: station path, K, timetable load (regular / facultative); printed P as reference only | infra.db |
| `trains` | number, name, type, class, facultative, operator, origin / destination, length and consist where known | gapeka.db |
| `stops` | per train in order: station, kind (stop / pass / halt / via), arrival / departure s, vmax of the following section, origin of each time | gapeka.db |
| `waits` | from the timetable notes: waiting train, station, other train(s), crossing / overtake | gapeka.db constraints |
| `duties` | set and locomotive chains (rangkaian pokok, rosters): which train turns into which, minimum turnaround | gapeka.db |
| `profiles` | physics inputs per directed station pair (km, Umum, gradient, restrictions of this direction's track); the engine runs the physics (physics_ref.py) | new |
| `model` | physics parameters per group, calibration, per-section corrections, station zones, buffers, gradient allowance, halt rules | rules.db |
| `rules` | engine parameters (section 3), fit exemptions, mandatory stops | rules/ (new tables) |
| `text` | user-facing messages per rule and warning (Indonesian + English), category names (KOM, EKO, ...) | new |

Left out: research and graph-reading tables (graph_crossings, graph_lines, sheet_pairs, dinas_tutup_marks, qa,
station_distances, train_index raw rows, roster time bars).

## 3. Rules as engine parameters (main new work)

`rules/priority.csv` holds most rules as prose. Automatic re-planning needs them as parameters plus a defined order of
evaluation. New CSV tables, compiled into rules.db and the package:

- `engine_params`: global values: dispatcher gap 2 min, release margin 1 min (larger after long blocks), opening of
  dinas tutup >= 10 min before the next open train, Cipeundeuy minimum stop, and so on.
- `wait_gaps` (exists): per line and track, crossing / overtake gaps.
- `headways`: minimum following gap per section, learned from GAPEKA (single track: next station + 2 min, >= 4 min
  in 97.5 %; double track: per block / section). Still to compute per section.
- `priority`: decision order (lower number first; first-to-arrive exception; regular before facultative), as codes, not
  prose.
- `fit`: train-length formula per type (car / wagon / locomotive lengths), blocked-station rule, stop-alone rule,
  exemptions.
- Conflict actions in order of preference: geser (shift the departure), ulur (lengthen a stop), with limits (maximum shift
  / extra dwell); batalkan (cancel) is never automatic.

The rule texts stay in priority.csv as the explanation. The new tables carry the numbers and IDs the engine uses, so the
website can say which rule moved a train.

## 4. Running times

- Unchanged trains: printed GAPEKA times, never recomputed.
- Changed or new trains: the engine runs the physics (physics_ref.py, inputs in `profiles` and `model`) for any vmax
  and stopping pattern, then the calibration and corrections, plus halts by the region's KAI halt rule.
- A new run class (new rolling stock with other acceleration / braking) needs its own physics parameters and a
  calibration type; v1 offers only the existing run classes.

## 5. Validation

1. Package check (`check.py`): schema valid, every train's stations / sections exist, the totals match the databases,
   size limit.
2. Golden replay (`replay.json.gz`): the Python reference engine re-times every train from its departure using
   the physics + waits. Report the error against the printed times, using the same measures as check.py (< 0.5 / 0.5-1 /
   > 1 min).
3. Re-planning test: remove one train, re-insert it at its printed departure and let the planner place its crossings /
   overtakes. Score: same waiting stations and same waiting train as GAPEKA. Run it over all trains, by type.
   This is the main measure of how well the rules reproduce KAI's planning.
4. The browser engine must reproduce 2. and 3. exactly from the same package.

## 6. Phases

| Phase | Output | Done when |
|---|---|---|
| 1 | `export_package.py` (pipeline step 10), schema, manifest, static parts of section 2 | DONE 25 Nov 2026: 0.44 MB, check.py section 7 |
| 2 | physics in the engine (`profiles`, physics_ref.py) + golden replay | DONE 25 Nov 2026: = gapeka_model on every leg, held-out 83 / 14 / 3 % |
| 3 | rule parameter tables (section 3), Python reference planner, re-planning test | baseline score recorded, rules tuned |
| 4 | user-facing texts (id / en), origins on every value, disclaimer | reviewed by the user |
| 5 | browser engine (separate repository?) | reproduces 2. and 3. exactly |

## 7. Open questions

- Where the browser engine lives (this repo or a new one) and its language (TypeScript or Rust / WASM).
- Licence / disclaimer text for public use of the GAPEKA data and of the estimated times.
- Which train attributes a public user may edit in v1 (times and stops only, or also type, speed and length).
- Limits for automatic geser / ulur (how far may a scheduled train be moved for a user's train: never? only
  facultative trains?).

## Phase 1 result (25 Nov 2026)

- `export_package.py` (pipeline step 10) -> `dist/gapeka-2026/package.json.gz` (0.44 MB; 3.4 MB raw) + `manifest.json`;
  `schema/package.schema.json` (JSON Schema 2020-12, compact rows described with prefixItems); `package_check.py`
  (references, time order) used by `check.py` section 7 (schema, problems, size <= 1.5 MB, counts = databases, same PDF).
- Contents: 528 stations (sheet-only rows included as kind sheet_only), 13 lines, 582 sections, 419 layouts, 2669
  trains, 1648 waits, 346 duties (148 sets from rangkaian pokok: 112 KRL / KRD + 36 freight; 198 locomotive units), 141 capacity sections, 26 dinas tutup
  pieces, model and rules as in rules.db (rules still prose + ids: phase 3).
- Stops: [station, kind, arr_s, dep_s, vmax_kmh, time_origin]; kinds terminal / stop / pass / halt / via; origins:
  printed 36695, model (halts, KA Dinas) 9714, graph (KA Dinas) 1268, manual (Bandung KAI halts) 178. Times continue
  above 86400 after midnight. 3444 passes have no printed time ('Ls'): left empty (phase 2 leg times can fill them).
- Repairs made only in the package: estimated times between two printed times that are out of order or outside them
  (KA Dinas: graph vs model times, or past the printed end) are spread evenly and marked model (90 stops); a passing
  point read with arrival > departure gets one time. Train numbers in the notes are normalised ('2539F JJ' -> 2539F,
  '2640' -> 2640F); none left unresolved.
- Not yet in the package: running-time inputs (phase 2), rule parameters (phase 3), user texts id / en (phase 4).

## Phase 2 result (25 Nov 2026, revised: physics in the engine)

User decision: trains that are not in GAPEKA (any vmax, any stopping pattern, later new rolling stock) are timed by
the physics model itself, not by a table of precomputed times. A first version shipped leg times per (pair, run
class, vmax) and could not time a vmax GAPEKA never uses (e.g. 100 km/h where only 90 / 120 exist); interpolating
between speeds was tested: pass-pass mean 0.5 s, stop-stop mean 3 s, worst 35 s. Replaced by:

- Package `profiles` (physics_ref.export_profiles): every directed station pair a train runs plus every sheet section
  both ways, with junction variants: km, Umum, ruling gradient, derived ('' = one sheet section, else joined / split /
  chain, resolved in Python), and the restrictions that apply to this direction's track [from_km, to_km, kmh, kind]
  (kind line, or platform-track = EMU-like trains only; siding notes dropped). `model.physics` (a0, power, brake per
  group), `cell_m` 50, `min_speed_kmh` 8, `station_zones`.
- `physics_ref.py`: pure-Python physics (no numpy), written for a line-by-line port: leg_cap() (50 m cells, Umum,
  restrictions, zones), simulate() (forward acceleration pass, backward braking pass). Its docstring is the spec.
- `replay.py` compose(): physics over the whole leg + calibration . features + buffers + corrections (the model's
  formula). Equal to gapeka_model on all 29203 timed legs (mean |difference| 0.0 s; research/compose_check.py);
  held-out legs without notes 83.1 / 13.6 / 3.3 %, all passenger legs without notes 85 / 12 / 2.5 %, freight (borrows
  intercity's calibration) 54 / 23 / 23 %.
- Train replay (chained from the printed first departure, printed dwell kept): drift at the last timed point, median
  / p10 / p90 min: KRL 0.0 / -1.0 / +0.9, commuter 0.0 / -2.5 / +2.1, intercity -0.3 / -7.9 / +6.6, freight -4.3 /
  -37 / +3.2 (GAPEKA's slack and crossing slow-downs add up over long trips).
- Example (any vmax works): Argo Wilis (10) Bandung - Banjar as one run, 27 sections: own GAPEKA vmax 163.3 min, all
  sections at 90 / 100 / 110 / 120 km/h: 165.1 / 162.5 / 161.4 / 160.6 min (Umum and restrictions still cap).
- Golden output `dist/gapeka-2026/replay.json.gz` (per train [stop index, arr, dep, leg s], 0.1 s), written by
  pipeline step 10, compared by check.py section 7. The browser engine must give the same numbers from the package.
- Package 0.46 MB. Not done: times for the 3444 untimed passes; a freight calibration; new run classes (new
  acceleration groups) need their own physics parameters and a calibration type.
