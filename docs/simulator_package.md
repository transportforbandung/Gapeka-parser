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
| 3 | rule parameter tables (section 3), Python reference planner, re-planning test | DONE 25 Nov 2026 (all written rules in the planner; see "Phase 3 completion") |
| 4 | user-facing texts (id / en), origins on every value, disclaimer | DONE 25 Nov 2026 (texts are drafts the user customizes later; not blocking) |
| 5 | browser engine (separate repository?) | reproduces 2. and 3. exactly |

## 7. Open questions

- (decided 25 Nov 2026, user) The browser engine lives in THIS repository, in TypeScript (Web Worker).
- (decided 25 Nov 2026, user) Disclaimer: published by Transport for Bandung, full text in Indonesian and English
  (draft in phase 4, reviewed by the user).
- (decided 25 Nov 2026, user) v1 edits: departure time, stops and dwell, vmax, train type (KRL / locomotive-hauled /
  freight) and length (cars); not routes.
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
  Fixed at the source (user): Cikini - Manggarai has no signal printed on page 13 (Gambir - Gondangdia 1, Gondangdia -
  Cikini 3); manual/block_signals.csv adds 3 per track, evenly spaced (any number >= 1 makes GAPEKA consistent there;
  3 = the Gondangdia - Cikini spacing). GAPEKA pairs breaking the block rule 24 -> 3 of 23127.
  Pipeline guard (signal_infer.py, step 10): blocks where >= 3 GAPEKA pairs break the block rule get evenly spaced
  signals inferred on their longest ABS double-track section without printed signals (spacing of the neighbouring
  sections), stored in infra.db as kind 'inferred: ...' with a qa row; fewer pairs -> qa row only. Re-run without the
  manual Cikini - Manggarai row: it infers the same 3 signals per track. check.py: broken pairs <= 5.
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
- Priority with displacement (user): a lower-numbered train never waits at a station it does not serve; the
  higher-numbered scheduled train is held before the block instead (planner.plan_priority; rules/priority.csv cross).
  The planned train skips higher-numbered scheduled trains where it would wait at a pass station; they are re-planned
  afterwards in priority order from their printed departures and may displace higher numbers in turn (max 12 trains
  per edit). research/priority_displace.py on GAPEKA's 57 crossings where the lower number waits (all at its passenger
  stops, which the rule allows: user, more effective; the test treats them as not served to exercise the mechanism): it waits there in 39 with plain planning, 1 with priority (it waits at a station it serves); displaced
  trains (157) end median 11 min later than in GAPEKA, 45 % within 5 min, max 3.3 h (ripples on busy single track:
  52 at Kepanjen moves 11 trains).
- Cost limit (user: stop holding when the delay is much bigger than the gain): plan_priority plans the train plainly
  too; holding is accepted only if the held trains' extra end delay (divided by displace_cost_ratio = 3 for trains
  the planned train clearly outranks, in full for trains of equal priority) is at most the time the planned train
  gains. 57-case test: ratio 1 / 3 / 5 accepts 21 / 23 / 24; held trains then end median 0 min late, 97 / 87 / 85 %
  within 5 min (the unlimited version: median +11 min, max 3.3 h).
- Equal priority (user: odd = from the east, even = from the west; neither direction nor a 1-number gap may decide):
  research/cross_priority.py on 300 GAPEKA crossings where one train stands and the other runs through: the lower
  number runs in 81 % when the numbers differ by >= 100, 57-61 % below 100 (near chance); the lower number being odd
  or even: 74 / 79 % (no effect); by type pair: commuter-intercity 88 %, freight-intercity 85 %, intercity-intercity
  72 %, freight-freight 57 %, commuter-freight 43 % (n 23). Rule: clear priority = regular over KA Dinas, numbers
  >= priority_number_gap (50; user, from 100: gaps 50-99 follow the number 73 % (n 22), below 50 51 %) apart, or the same number regular over facultative; otherwise EQUAL priority and the
  crossing goes where the total delay of both trains is least.
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

## Phase 3 completion (25 Nov 2026)

User: finish phase 3 before phase 4. Every rule written in rules/priority.csv is now in the planner:
- Station tracks (can_wait / station_free): every moment another train is at the station, all trains present plus the
  planned one need a Roman track each, usable in their own direction (usable_towards; unknown = both ways) and, if they
  STAND there, long enough (greedy matching); a train running through needs a free track of any length; alone at the
  station a train need not fit (stop alone); 59 s tolerance (whole minutes). Train length: locomotive-hauled cars x 21 m
  + 16 m; multiple units (KRL, KRD, EMU-like) cars x 20 m (12-car KRL 240 m fits Bojonggede II-251); freight wagons.
  GAPEKA's printed calls break it 43 times of 36839 (misfit crossings: Wonogiri-type single-track loops, Rogojampi,
  Malang Kotalama, Telawa, ...): tolerated as printed (headways.station_exemptions, fitted per edition; user decision
  for existing schedules), new / edited trains stay strict.
- Release (event release): a train never arrives at a station without a free track for it; it leaves the previous
  station so it arrives when one frees (blocking train's departure - running time + dispatcher gap), by default 1 min
  before the train ahead leaves when it continues the same way (release_margin). Entry-signal marks need no code: the
  planner never holds a train at an entry signal.
- Rolling stock (duties): a train cannot leave before its unit's previous trip arrived + turnaround (fitted per duty
  kind and type, headways.turnaround_min: KRL 1, KRD 5, loco intercity 4, loco commuter 20 min, ...; a printed tighter
  turnaround is kept for that link). plan_edit (the simulator's entry point) re-plans the unit's next trips when an
  edit makes them late (e.g. KRL 1008 +40 min -> 1217, 1218, ... along the set's day). Printed trains held: 0.
- Window time: Planner.windows (a, b, start, end, optional direction 'X->Y'): no train in a block while a window closes
  a sheet section on its route.
- Capacity warning: capacity_warnings() lists sections whose load (timeline as planned) exceeds K; GAPEKA printed: none.
- Re-planning test (printed running times; block rule + 2 min gap before -> now): GAPEKA waits at the same station
  intercity 74 -> 79 %, commuter 56 -> 73 %, KRL 67 -> 71 %; end within 2 min 94 -> 92 / 94 -> 93 / 98 -> 98 %.
- check.py: station exemptions <= 60, no printed train held by its turnaround, no section over K.

## Phase 4 (25 Nov 2026; texts/ui.csv and texts/disclaimer.md are drafts the user will customize later)

- Origins on every value: one vocabulary (meta.origins, texts origin.*: printed / sheet / ocr / graph / manual / model /
  fitted / inferred / derived / simulated). Stops: time_origin (as before); restrictions and block signals: 6th field
  origin (sheet / manual / inferred); station tracks: 10th field origin (infra.db station_tracks.origin: sheet 1675,
  ocr 20, manual 13); consists printed; dinas tutup hours fitted; capacity {capacity printed, load / path derived};
  model and headways fitted. Schema 1.1.0.
- Texts: texts/ui.csv (key, id, en, note): 72 keys: train types, stop kinds, origins, block systems, track kinds, wait
  kinds, message templates (msg.* with {train} {other} {station} {minutes} {time}), warnings, the v1 edit fields, the
  rules in plain words, short disclaimer. Package: texts {key: {id, en}}. planner.explain(pkg, train, waits, lang, info)
  turns a plan into sentences (held trains only when the holding was accepted).
- Disclaimer: texts/disclaimer.md (## id / ## en; {edition} {decree} {valid_from} filled from the meta) -> meta.disclaimer
  {id, en}. Transport for Bandung, unofficial, not affiliated with PT KAI / KAI Commuter / DJKA / Kemenhub, only
  'printed' times from GAPEKA, rules are simplifications, not for journeys / operations / safety, no warranty.
- check.py section 7: every train type, stop kind, origin, wait kind, message and block system used in the package has
  an id and an en text; placeholders match between the languages; disclaimer present in both.
- Planner bugs found while testing the texts (fixed): (1) after 60 restarts _plan silently returned a half-filled plan
  (rows after the stop copied the printed times: 54 +15 min 'ended on time'); now the last attempt never restarts and
  waits where the conflict is ('(no place to wait)'). Re-planning test after the fix: same station 82 / 78 / 73 %, end
  within 2 min 91 / 92 / 97 % (half plans no longer count as on time). (2) plan_priority rejected every chain that hit
  its cap; now the rest of the chain is planned plainly and the cap is 30. Tested and left OFF (worse): chain trains
  displacing only trains they outrank (Planner.chain_rule), displacement at the train's own stops (stop_displace).
- Open (planner, before phase 5): plain planning (the planned train yields to all others) can make a delayed priority
  train wait for hours behind lower trains (7 +15 min -> ~10 h late; its holding chain is rejected); 54 +15 min -> +58
  min with 21 trains held. Needs a proper dispatching re-plan (several trains together, in priority order).

## Delayed priority trains (25 Nov 2026, user: "Bima 7 has a number below 10, how can it be 10 h late?")

research/delay_cases.py (plan_edit, train starts N min late). Causes found and fixed:
- hold_at_stop (hold a train at its last planned stop for an overtake) had no length limit: freight 2523 was held 6 h at
  Batang for 2761F, still hours behind. Now the hold may exceed the wait it replaces by at most hold_slack (10 min).
  Freight re-planning test: end mean |diff| 88 -> 15 min, same station 58 -> 68 %; passenger unchanged.
- hold_at_stop's overtaker day: chosen by its time at the conflict station vs the train's arrival there.
- Long-train rule in the planner (user: the long train goes first, the other stops and fits): a train that fits no
  track at a station holds the other train (crossing, or a faster one behind) when that one fits, whatever its
  number (Planner.forced). Kalingga Cargo 2535 (391 m) on Surabaya Pasarturi - Lamongan.
- A train's own set's next trip is never 'held' for it (it follows anyway: plan_edit turnaround).
- plan_priority chain past its cap: still applies the long-train rule (no priority holding), hard cap 3 x.
Results (end late): 7 +5 -> 21 min (accepted), 54 +15 -> 58, 8 +20 -> 40, 121 +15 -> 28, 2535 +10 -> 273; 7 +15 / +30
still rejected (gain 367 min vs weighted cost 369 / 414: the held freight trains, re-planned from their printed
departure, lose 2-4 h each) -> plain plan 6.7 h late.
- Held trains are now re-timed from where they are held (their printed run before that stays; plan_from /
  hold_index), not re-planned from their origin: 7 +15 / +30 -> 35 min late (accepted). Sample (research/
  delay_sample.py, the 30 lowest-numbered intercity trains each 15 min late): accepted 22 / 30 (origin: 20), end late
  median 36 min (43), p90 255 min (300), max 335 (402); 11 within 30 min. Still too late in the tail (realistic:
  ~15-25 min): next step = a dispatching re-plan (the affected trains in a time window re-planned together in priority
  order), instead of one train at a time against a frozen timetable.

## Dispatching re-plan (25 Nov 2026, user: "build the dispatching re-plan")

planner.dispatch (planner.plan_edit calls it; the old one-train chain is plan_edit_chain):
- the affected trains are re-planned TOGETHER, highest priority first, each against the fixed timetable plus the members
  already placed; rounds repeat until no train joins (max 80 trains, 8 rounds)
- holds decided per conflict (Planner.worth_holding, the user's cost rule locally): a train that would wait for a lower
  one holds it if the hold <= 3 x its avoided wait (clearly outranked) or <= 1 x (equal); at stations it passes, and at
  its own stops for trains it clearly outranks; the long-train rule; a unit's next trip joins when it would be late
- downstream check (the cost rule on the final result): a hold whose held train ends up later than the gain that
  justified it (weighted) is forbidden and the dispatch runs again (max 3 passes)
- held trains re-timed from where they are held; Timeline.drop / put update the index incrementally (3-4x faster)
- RECOVERY (Planner.recover, replay.Engine.minimum): a late train runs at max(technical minimum = raw physics without
  GAPEKA's supplement + 20 s per halt, printed run - lateness), never ahead of its printed times. Bima: printed running
  589 min, technical minimum 521 (68 min to recover). This was the biggest missing piece.
Sample (research/delay_sample.py, the 30 lowest-numbered intercity trains each +15 min), ends late median / p90 / max,
other trains' total delay median / max:
  dispatch (3 passes)  1 / 23 / 100 min, 25 / 1513 min, 1-16 s  <- default
  dispatch (1 pass)    1 /  1 /  25 min, 165 / 904 min
  one-train chain      1 / 37 / 104 min, 26 / 922 min
  (before recovery: dispatch 73 / 119 / 208; chain 36 / 255 / 335)
research/delay_cases.py: 7 +15 -> 1 min late (others 1 min); 7 +30 -> 0 (others 216 min); 54 +15 -> 1 (9); 8 +20 -> 1
(328); 121 +15 -> 0 (1); KRL 1008 +15 -> 26 (694: KRL have little slack, the set's day ripples).
Re-planning test with recovery: same station 85 / 78 / 73 %, end within 2 min 92 / 93 / 98 %; freight 80 % (mean 5.8 min).

## Python planner speed (25 Nov 2026, user: "optimize the python first")
Same plans, bit for bit (dispatch of 7 / 1008 / 29 / 2535 / 54 and the intercity re-planning test compared before /
after): Timeline.window() bisects the sorted block / station lists to the time window (maxdur per key) instead of
scanning every train of the day; block_conflict skips a double-track train early when bounds on both trains' times
prove 'ahead' or 'behind' (the full test would give the same), and computes the times at the block points once
(other train: cached per train + block in Timeline.ptimes, dropped with the train; T: once per block); replay.Engine
caches compose / minimum per (type, run class, end notes, rows p..q). KRL 1008 +15 min dispatch 19.5 -> 2.8 s,
54 9.8 -> 1.5 s; 30-intercity sample max 16 -> 3 s. The TypeScript port should keep these three ideas (time-window
lookup, early bound test, precomputed block-point times).
