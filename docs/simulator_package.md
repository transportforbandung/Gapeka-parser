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
| 3 | rule parameter tables (section 3), Python reference planner, re-planning test | BASELINE 25 Nov 2026 (see phase 3 notes); tuning continues |
| 4 | user-facing texts (id / en), origins on every value, disclaimer | reviewed by the user |
| 5 | browser engine (separate repository?) | reproduces 2. and 3. exactly |

## 7. Open questions

- Where the browser engine lives (this repo or a new one) and its language (TypeScript or Rust / WASM).
- Licence / disclaimer text for public use of the GAPEKA data and of the estimated times.
- Which train attributes a public user may edit in v1 (times and stops only, or also type, speed and length).
- (decided 25 Nov 2026, user) Scheduled trains may be edited by the user (their GAPEKA times are the starting point
  and the planner re-plans them); they are never moved to make room for a train created outside the schedule.

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

## Phase 3 baseline (25 Nov 2026)

User decision: the user may edit any scheduled train (its GAPEKA times are the starting point; the planner re-plans
it); scheduled trains are never moved to make room for a train created outside the schedule. In both cases the
planner plans ONE train against all others, which keep their times (rules/engine_params.csv move_scheduled_trains).

- `rules/engine_params.csv` (rules.db + package rules.engine_params): dispatcher gap 2 min, min wait 1 min, release
  margin 1 min, dinas tutup reopening 10 min, move_scheduled_trains, single-track block, wait station, time tolerance
  59 s (GAPEKA plans in whole minutes and often keeps exactly the minimum gap).
- Package `rules.headways` (planner.fit_headways, recomputed from GAPEKA every build, 0.1th percentile, >= 2 min):
  single track: follower enters 2 min after the leader cleared the block, opposing train 2 min after the other cleared
  it (0 overlaps in GAPEKA); double track: KRL after KRL 3, KRL after other 2, other after KRL 3, other after other 3
  min. (1st percentiles 3 / 3 / 4 / 5 gave false conflicts: by definition 1 % of GAPEKA's own pairs break them.)
  Profiles carry a double-track flag (a derived pair: every sheet section on its route).
- `planner.py`: Timeline (every scheduled train at every station, untimed passes by distance; blocks = consecutive
  non-halt stations; copies a day earlier / later for midnight) and Planner.plan(train, departure): block by block the
  earliest departure that keeps the gaps (single: one train per block, crossing / following gaps; double: following
  gaps; an overtaken train that stood at the station leaves >= 3 min after the overtaker passed and after it cleared
  the block; dinas tutup: closed stations are no block points). The train waits only where it can (a station, not
  closed, with a Roman track it fits, room beside the trains standing there), else at an earlier station. Double-track
  overtakes: held at its last planned stop until the overtaker reached the next station + 2.5 min (data: GAPEKA lets
  the faster train pass where the slower one stops anyway; +16 points on intercity). A wait at a station the train
  passes = a stop there (printed time + physics difference stop vs pass). Waits name the train and rule (cross /
  overtake / follow).
- Re-planning test (planner.replan_test, research/replan_test.py, check.py section 8): each train out, re-planned
  from its printed departure against all others, its GAPEKA waits cut to a 1-min stop, printed running times kept:

  | type | trains | GAPEKA waits at the same station | + same other train | departure there +-1 min | planner waits not in GAPEKA | trains without waits kept clean | end within 2 min (mean) |
  |---|---|---|---|---|---|---|---|
  | intercity | 345 | 62 % | 61 % | 33 % | 53 % | 97 % | 88 % (2.5 min) |
  | commuter | 280 | 43 % | 38 % | 23 % | 37 % | 100 % | 88 % (1.6 min) |
  | KRL | 1342 | 63 % | 63 % | 29 % | 55 % | 99 % | 98 % (0.2 min) |
  | freight | 126 | 52 % | 34 % | 10 % | 52 % | 98 % | 63 % (37 min) |

  With the package physics instead of printed running times: intercity end within 2 min 50 %, KRL 78 % (running
  times off by +-1 min against GAPEKA's minute-tight spacing cascade): for trains that exist in GAPEKA the engine should
  keep their printed running times and use physics only for new trains / changed stopping patterns.
- Open (tuning): where GAPEKA chooses the overtake / crossing station (misses: double-track overtakes on Cikampek
  lines, commuter crossings on the Merak line), per-line overtake gaps (wait_gaps line names do not match the package
  line titles yet), station capacity per direction
  (usable_towards), the release rule (arrive when a track frees) and entry-signal marks, freight.

## Phase 3 tuning (25 Nov 2026)

- Superseded (user: fixed 3-5 min margins are wrong, overtakes depend on the line: block lengths and signals; "trains
  usually only need the block ahead to clear"): the overtake_margin (5 min, not for KRL) and the fixed double-track
  following minima were replaced by the block-point rule below. History: research/overtake_margin.py.
- Block-point rule (double track; engine_params double_track_block, approach_blocks; research/block_clear.py):
  block points = stations (not halts) + the block signals of the track in the travel direction (package
  sections.block_signals: hulu = sheet a -> b, offsets from a; hilir = km - offset; a station pair over several sheet
  sections collects the signals of each). A train may run into block k only when the train ahead has passed point
  k+1. Times inside a block by distance between each train's times at the halts in it (Timeline.block_knots: a KRL
  stopping at Gondangdia / Cikini is not at an even speed). GAPEKA 2026 keeps it: 24 of 23127 consecutive pairs
  break it, no margin beyond block clear (0.0 min at whole minutes) -> only the 59 s time tolerance.
  User's wait-station rule ("stop the slower train where continuing would hold up or slow the faster one"):
  approach_blocks 1 = a FASTER train behind must find each block clear one block point earlier (where it sees the
  signal aspect), so it is never slowed. 0 (only the block ahead): KRL same-station 55 -> 35 %, commuter 48 -> 40 %.
  Incomplete signal data: Gambir - Manggarai shows one signal (GMR-GDD) but GAPEKA runs KRL 3 min behind intercity
  trains, so the sheet misses signals there; without help intercity trains waited hours at Gambir. Fallback per
  directed block: a pair is also allowed when it is spaced at every block point at least like the tightest GAPEKA pair
  on that block (headways.double_block_spacing_s, 462 blocks, >= 60 s, fitted on GAPEKA when the package is built,
  never refitted on a user scenario). A line condition per block, not a global margin.
  The waiting train (overtaken) leaves when the overtaker has passed the next block point + the dispatcher gap
  (wait_gaps overtake double, 2.5 min).
  Re-planning test (printed running times; old fixed margins in brackets): intercity same station 68 % (69),
  end within 2 min 94 % (90), mean |end diff| 1.8 min (3.2), clean 98 % (96), waits not in GAPEKA 41 % (48);
  commuter 48 % (50), 92 % (89); KRL 55 % (63), 98 % (98). KRL misses: GAPEKA keeps more distance than the signals
  require at Tambun (intercity 78F 0.9-3 min clear behind KRL 5016 at every block point to Cikarang, GAPEKA still
  overtakes at Tambun) and on Yogyakarta - Solo (Srowot / Brambanan: the planner overtakes one station later, ending
  earlier). Both are valid plans under the rule. Physics running times: intercity end within 2 min 53 % (51).
- Dispatcher gap on double track (user: the follower needs the block ahead clear PLUS 1-2 min for the dispatcher, as
  on single track): engine_params double_follow_gap = 2 min, added to both the "behind" and the "ahead" check (the
  per-block GAPEKA spacing fallback already contains GAPEKA's gaps). Re-planning test, gap 0 / 1 / 2 min: same
  station intercity 68 / 71 / 74 %, commuter 48 / 53 / 56 %, KRL 55 / 61 / 67 %; end within 2 min 94 / 94 / 94,
  92 / 93 / 94, 98 / 98 / 98 %. GAPEKA keeps less than 2 min (after the 59 s tolerance) in 2.8 % of consecutive
  double-track pairs. Fix found on the way: hold_at_stop compared the overtaker's raw times (346 at 29:49 = 05:49 next
  day) with T's: now the overtaker's copy on T's day (98 was held 24 h at Gambringan).
  Remaining intercity outliers (end 1.5-4.5 h late, 6 of 345): a faster train that loses its GAPEKA overtakes falls
  behind slower FIXED trains and waits at several stations ('follow'); the planner cannot slow a train to follow
  (only waits at stations) and fixed trains never yield. Open item.
- Fixes: a station that already is a planned stop is never charged the stop / start penalty again (only stops the
  planner adds are); the planner's printed running times keep every row (no silent physics fallback).
- Physics vs GAPEKA (research/physics_sign.py, model - GAPEKA): running time alone over whole trips median -0.3
  (intercity) / 0.0 / 0.0 min, -0.1 min per hour of trip, per leg faster in 48 %: no systematic bias. GAPEKA's planning
  supplement is real (the calibration multiplies pure physics by 1.06-1.08 and adds fixed times) and is inside the
  model. The weak physics re-planning (intercity 51 % end within 2 min) is symmetric (late > 2 min 26 %, early 23 %,
  median 0) and comes from waits (27 min per intercity train on average): +-1 min per leg breaks GAPEKA's minute-tight
  spacing and cascades. GAPEKA trains keep their printed running times; physics for new trains / changed patterns.
