# Gapeka Parser

Tools used by **Transport for Bandung (TfB)** to turn KAI's official train timetable, the
**GAPEKA** (*Grafik Perjalanan Kereta Api*), into the rail timetables published on the
**Bandung Basin Metropolitan Area Transit Map** and on TfB's social media posts.

GAPEKA is released as one very large PDF: about 1,900 pages of timetables, train-graph sheets, rolling-stock
duties and a train index for all of Java. This repository reads that PDF into three SQLite databases and produces
ready-to-edit timetable graphics for the Bandung services:

- **Commuter Line Bandung Raya**: Padalarang – Bandung – Cicalengka, one trip to Purwakarta (trains 390–431)
- **Commuter Line Garut**: Purwakarta / Padalarang – Bandung – Cibatu / Garut (trains 386–389, 412, 423)
- **Siliwangi**: Sukabumi – Cipatat (trains 351–356)

The outputs are vector SVG files that open directly in Illustrator, with live text and named layers, plus CSV
files for checking and further editing.

## How it works

```
GAPEKA-JAWA-2026.pdf
   │   gapeka_pipeline.py  (one command, 8–10 minutes)
   ├──► gapeka.db   timetable: trains, stops, printed times, train types, KA Dinas, rolling-stock duties
   ├──► infra.db    infrastructure: stations, km, sections, speed limits, gradient, double track, signals
   └──► rules.db    rules (rules/*.csv) + the running-time model fitted on the timetable
                        │
                        ▼
              halt times and KA Dinas passing times (estimates, written into gapeka.db)

gapeka.db ──► export_poster.py   → timetable tables (SVG) for the map and posts
          ──► export_headway.py  → station time bar with headway colours (SVG)
          ──► export_trains.py   → station × train matrix (CSV)
          ──► sanity_check.py    → list of suspicious times (CSV)
```

**Printed times are never changed.** Every time printed in the GAPEKA goes into `gapeka.db` as it is. The GAPEKA
prints no time for halts (*Berh* / *Sb*) and only start and end times for KA Dinas trips (empty trainset and
locomotive moves); only those are estimated, by a running-time model:

- **Physics:** the speed a train may run on each 50 m of track is the lowest of its timetable speed limit, the
  line's general speed (*Umum*), local speed restrictions on its track (hulu / hilir) and station zones. The train
  accelerates and brakes like its rolling stock (KRL, locomotive / KRD, freight).
- **Calibration:** the model is fitted to the GAPEKA's own printed running times, per train type, so it plans
  like GAPEKA does (margins, start / stop time, extra time on steep sections, busy termini).
- **KAI's publication rule per region** places the halt time between the two printed times around it
  (`rules/halt_rules.csv`): Bandung shares the shortest time of that leg, Jabodetabek and Surabaya the train's own
  leg time. KAI's published times in `rules/halt_offsets.csv` always win for the TfB halts.

Accuracy (trains held out of the fitting): running times 83 % within 0.5 min, 3 % more than 1 min off; halt
times against KAI's published timetables: Bandung 79 %, Jabodetabek 90 %, Surabaya 84 % to the exact minute,
99–100 % within one minute.

## The three databases

### `gapeka.db`: timetable

| Table / view | Contents |
|---|---|
| `trains` | Every train: number, service, route; `type` (krl / commuter / intercity / freight / dinas), `klasifikasi` and `kelas` (class from the train index), `facultative` (1 = runs when KAI decides; still part of all calculations), `operator`, `index_km` |
| `stops` | Every timetable row: station, `kind` (stop / halt / pass / origin_or_end), `vmax` for the next section, printed arrival / departure (seconds after midnight; times after midnight continue past 86400), remark, PDF page |
| `schedule` (view) | `stops` with the estimated times filled in where nothing is printed (`estimated` = 1, `method`). **Use this one for exports.** |
| `schedule_hhmm` (view) | The same with times as HH:MM, the way KAI publishes them |
| `halt_est` | The estimates: `manual` (KAI times from `rules/halt_offsets.csv`), `model-split…` (running-time model with the region's rule), `dinas-model` (KA Dinas passing times) |
| `train_index` | The train-number index (Lampiran IV) as printed: number, regular / facultative, name, route, km, departure, arrival, class |
| `roster_units`, `roster_duties` | Rolling-stock duties (O.18 diagrams): one locomotive, KRD set or KRL trainset per day and the trains it works in order, including KA Dinas moves; `connects` = the train starts where the previous one ended |
| `consists` | Stamformasi: cars of one trainset and locomotive class per passenger train |
| `notes`, `constraints` | Crossing and overtaking remarks (`x ka 391`) linked to the other train |
| `stations`, `station_links` | Station codes and names; older station-to-station distances (a fallback behind `infra.db`) |
| `graph_*` | Train lines read from the drawings on the graph sheets |

### `infra.db`: infrastructure

Read from the margins of the graph sheets (PDF pages 7–51), plus hand corrections from `manual/*.csv`. Every row
says where it came from (`source`: sheet / sheet (joined) / manual) and the PDF page.

| Table | Contents |
|---|---|
| `stations` | Code, name, name as printed on the sheet, kind (station / halt / sheet only), CTC (dashed box) |
| `lines` | One per group of graph sheets (13), with the raw header text (dispatching stations) |
| `line_stations` | Stations in sheet order per line: km ("Letak"), second km at junctions, elevation |
| `sections` | Every pair of adjacent stations: length, km at both ends, *Umum*, ruling gradient, elevations, single / double track and why, variant (two tracks between the same stations, e.g. Kasugihan–Maos) |
| `restrictions` | Local speed restrictions: km from–to, speed, track (hu / hi / both), the note printed in the cell |
| `block_signals` | Block signals: position, track, number |
| `section_systems` | Per section, from the symbol column beside the graph: block system (closed automatic block, open automatic block, electromechanical block) and electrified (1 = zigzag symbol); `symbols` = number of symbols read, `detail` = mixed symbols |
| `station_layouts` | Station track diagrams (right of the graph sheets): one row per diagram and line (a station can be drawn differently on different lines) with the number of usable Roman (main / siding) and Arabic / Y (yard) tracks, out-of-service tracks, longest Roman track, PPKA / PAP posts, the neighbour stations at both ends and how the station was identified (`evidence`) |
| `station_tracks` | The tracks of each diagram: number, `roman` / `yard`, effective length (m; `length_m` = the longest figure printed for that track on any line, `length_on_sheet` = this diagram's), drawn arrows (`arrows`, `towards`), `through`, `out_of_service` (reached only by dashed / dotted lines, or drawn dotted itself), `usable` = directions (L / R on the sheet) in which a train can stand on it and continue, `usable_towards` = the same as neighbour stations, `enter` = directions from which a train can get onto it (termini, dead ends) |
| `station_signal_marks` | Entry signals on a gradient over 8 permil (small signal icon at a diagram end): trains arriving from that side are not held at the entry signal |
| `station_layout_refs` | "Emplasemen X periksa lintas Y" notes: X's diagram is drawn on line Y |
| `double_track_header` | The "JALUR GANDA" lists from the sheet headers |
| `qa` | Everything doubtful, for review (see "Reviewing the QA list") |
| `meta` | PDF fingerprint, decree number, valid-from date, build time |
| `section_lookup` (view) | Every section in both directions with the track used (hu / hi) |

Sections are stored top row first: trains running from the upper row to the lower one use the **hulu** track,
trains running back up use the **hilir** track.

### `rules.db`: rules and fitted values

| Table | Kind | Contents |
|---|---|---|
| `station_zones` | hand (`rules/`) | Station-area speed zones per train type, e.g. Kiaracondong towards Gedebage 30 km/h for 1,180 m |
| `section_buffers`, `station_buffers` | hand | Extra planning time GAPEKA adds: Nambo branch, Sukabumi line, busy termini (Gambir, Pasarsenen, Surabaya Kota) |
| `halt_rules` | hand | KAI's halt publication method per region |
| `halt_offsets` | hand | KAI's published halt times for the TfB halts |
| `wait_gaps` | hand | When a train waiting for a crossing / overtake leaves |
| `priority` | hand | Who yields at crossings / overtakes in the simulator (lower train number has priority) |
| `station_attributes` | hand | CTC stations |
| `mandatory_stops` | hand | Stations where every train stops (Cipeundeuy: brake check, 10 min) |
| `fitted_calibration` | fitted | Per train type: margin, fixed times, steep-section allowance |
| `fitted_corrections` | fitted | Small per-section / station / halt adjustments where GAPEKA is consistently faster or slower than physics |
| `fitted_halt_offsets` | fitted | Per-halt adjustments for KRL, learned from KAI's Jabodetabek timetable |
| `fit_stats` | fitted | Accuracy of this run (what `check.py` reads) |

Hand rules are edited in the CSV files; fitted values are recomputed on every run and never edited by hand.

## Tutorial

### 1. Install

Python 3.9 or newer, then:

```
pip install -r requirements.txt
```

This installs pymupdf (reads the PDF), numpy and scipy (the model) and rapidocr-onnxruntime (reads graph pages
whose text is drawn as outlines; it runs locally, no internet needed). The export scripts need only Python itself.

### 2. Build the databases

```
python gapeka_pipeline.py gapeka.db GAPEKA-JAWA-2026.pdf
```

About 8–10 minutes (the first run longer: OCR results are cached in `.cache/ocr`). The run prints each step:

```
[1] trains, stops, halts        timetable pages
[2] station links               distances on the graph sheets
[3] halt tables
[4] constraints                 crossing / overtaking remarks
[5] graph trains                drawn train lines
[6] train index                 types, classes, facultative, KA Dinas
[6b] rolling-stock duties
[7] infra.db                    graph-sheet margins + manual/*.csv
[8] rules.db                    rules/*.csv + model fitted, accuracy report
[9] halt times / KA Dinas       estimates written into gapeka.db
```

Read the report at step 8: it shows the running-time accuracy and the halt times against KAI. Options:
several PDFs or a folder can be given; `--dwell 30` sets the dwell time for the manual halt times; `--no-graph`
skips the graph sheets; `--no-model` skips steps 7–9 (halts then come from an older distance-based estimate).

`build_infra.py` and `build_rules.py` can also be run on their own (`python build_infra.py`, `python
build_rules.py`) after a change to `manual/` or `rules/`; run the whole pipeline afterwards so the estimates in
`gapeka.db` are updated too.

### 3. Check the result

```
python check.py            # checks the databases in this folder
python check.py --run      # rebuilds everything into a temporary folder first, then checks
python check.py --run --pdf NEW-GAPEKA.pdf   # the same with another PDF (a new edition)
```

It checks that printed GAPEKA times are never replaced, every halt and KA Dinas stop has a time, every timetable
station pair has infrastructure data, the model and the halt times stay as accurate as now, and that the TfB
exports are identical to the copies in `tests/baseline/`. Anything wrong prints `FAIL` and the script exits with
an error. When a change to the exports is wanted (for example a new GAPEKA), accept it with:

```
python check.py --update-baseline
```

### 4. Export timetable graphics

```
# Cicalengka-bound and Padalarang-bound tables for Commuter Line Bandung Raya + Garut
python export_poster.py 386-431 --even -o cl_bandung_even.svg
python export_poster.py 386-431 --odd  -o cl_bandung_odd.svg

# Siliwangi
python export_poster.py 351-356 --odd  -o siliwangi_odd.svg
```

Colours, cell sizes and fonts follow the TfB timetable design. Run the script without arguments to see every
option: `--exclude`, `--stations`, `--bold`, `--highlight`, `--sort`, `--scale` and `--fit-width`.

Station headway bar (a time axis with one line per train, gaps coloured from green = short to red = long):

```
python export_headway.py BD --service "Commuter Line Bandung Raya,Commuter Line Garut"
python export_headway.py BD,KAC,CCL 386-431 --even -o headway_even.svg
```

CSV matrix and a list of suspicious times:

```
python export_trains.py 386-431 --odd -o trains_odd.csv
python sanity_check.py gapeka.db
```

In the outputs, halt times are shown as one time, the arrival, in whole minutes, the same way the KAI ticket
shows them. In the CSV they are marked with `*`.

### 5. Look things up

Any SQLite tool works (DB Browser for SQLite, `sqlite3`, Python). A few examples:

```sql
-- the timetable of train 390, as KAI publishes it
SELECT seq, station, kind, arr, dep, estimated FROM schedule_hhmm WHERE train_no = '390' ORDER BY seq;

-- all trains of one type
SELECT no, service, route FROM trains WHERE type = 'dinas' LIMIT 20;

-- the track between Andir and Bandung (infra.db)
SELECT a, b, km, umum_kmh, ruling_gradient_permil, double_track FROM sections WHERE a = 'AND' AND b = 'BD';
SELECT km_from, km_to, speed_kmh, tracks, note FROM restrictions WHERE a = 'AND' AND b = 'BD';

-- what a KRL trainset does all day
SELECT d.seq, d.train_no FROM roster_duties d JOIN roster_units u USING (unit_id)
WHERE u.kind = 'krl' AND u.unit_id = (SELECT MIN(unit_id) FROM roster_units WHERE kind = 'krl') ORDER BY d.seq;
```

### 6. Correct the infrastructure (`manual/*.csv`)

When the sheet is read wrongly or says too little, add a line to one of these files and rebuild. Every line has a
`source` (who said so) and a `note` (why), so corrections survive every rebuild.

| File | Use | Example |
|---|---|---|
| `manual/station_aliases.csv` | A station printed under another name | `Lumajang,KK,...` (Klakah is called Lumajang on the sheet) |
| `manual/pair_overrides.csv` | A section length; `delete` removes a false pair | `AK,KPB,4.102,...`; `KDO,PWS,delete,...` |
| `manual/section_overrides.csv` | Umum, gradient, length or double track of a section | `KDO,SLO,umum_kmh,120,...`; `CCL,NG,double_track,0,...` |
| `manual/layout_station_fixes.csv` | Which station a track diagram belongs to, when the reader cannot tell; station `-` ignores the diagram | `44,2229,SB,...` (page, y of the top track, station); `50,2632,-,...` (Kalisat copy in the inactive Panarukan table) |
| `manual/layout_label_fixes.csv` | A misprinted end label on a diagram | `25,GDB,GDB,KAC,...` (Gedebage's left arrow is Kiaracondong) |
| `manual/layout_tracks.csv` | Tracks whose labels cannot be read | `35,KRT,IV,335,...` (Kretek IV-335) |
| `manual/layout_track_directions.csv` | Directions the drawing cannot show; `*` = every Roman track | `13,CKR,VI,R,1,...` (Cikarang VI eastbound main); `44,SDT,*,LR,0,...` |
| `manual/restriction_overrides.csv` | Change a restriction's track or speed, or delete it (`action` = set / delete) | `CIR,BD,0.712,1.346,90,,delete,...` |

### 7. Change the rules (`rules/*.csv`)

| File | Use |
|---|---|
| `rules/halt_offsets.csv` | KAI's published halt times for the TfB halts: `halt,from,minutes[,trains]`, e.g. `GK,PDL,5` = Gadobangkong at Padalarang's departure + 5 min; a 4th column lists trains that use a different value. Update it when KAI publishes a new schedule. If a TfB halt has no rule, its time comes from the model. |
| `rules/halt_rules.csv` | KAI's halt publication method per region (split / split-min / front, dwell) |
| `rules/station_zones.csv` | Station-area speed zones (length, speed, train types, stopping / passing) |
| `rules/section_buffers.csv`, `rules/station_buffers.csv` | Extra planning time on sections or at stations |
| `rules/wait_gaps.csv` | Departure rules for trains waiting for a crossing / overtake |
| `rules/priority.csv` | Who yields in the simulator: the higher train number waits (overtakes 98 %, crossings 74 % in GAPEKA); real GAPEKA trains keep their printed waits |
| `rules/station_attributes.csv` | CTC stations |
| `rules/mandatory_stops.csv` | Stations where every train stops, with the minimum dwell |

The KAI timetables in `reference/` are used to check the halt rules (step 8 report and `check.py`). When KAI
publishes new timetables, replace those files to keep the check meaningful.

### 8. Reviewing the QA list

`infra.db`'s `qa` table lists everything the extraction was unsure about:

```sql
SELECT check_name, COUNT(*) FROM qa GROUP BY 1;
SELECT item, detail FROM qa WHERE check_name = 'timetable pair without a sheet section';
```

| Check | Meaning | Usual action |
|---|---|---|
| timetable pair without a sheet section | A pair of stations in the timetable with no track data | Must be 0 (`check.py` fails otherwise): add an alias or a pair override |
| timetable pair covered by a chain of sections | A train skips stations (express); covered through several sections | None |
| section without Umum | No general speed printed | Add `umum_kmh` in `manual/section_overrides.csv` if known |
| station name from timetable neighbours | The name could not be read (e.g. Ijo) and was taken from the timetable | Check once |
| sheet station not in the timetable | Sidings, junctions, inactive lines, stops no train serves | None |
| bypass section dropped / section deleted (manual) | Wrong pairs removed | None |
| station diagram assigned by position only (check) | The diagram's station came only from its position next to a row | Look at the sheet; fix in `manual/layout_station_fixes.csv` if wrong |
| track label on no track line (check the diagram) | A track label (text or OCR) the reader could not attach to a drawn track | Add the track in `manual/layout_tracks.csv` |

### 9. A new GAPEKA edition

1. Put the new PDF in the folder and run `python check.py --run --pdf NEW-GAPEKA.pdf`. It builds everything into
   a temporary folder and checks it, without touching the databases in this folder.
2. Read the step 8 report and the `qa` table. If the layout is unchanged, coverage stays at 100 % and the
   accuracy stays about the same.
3. Expect the TfB export check to fail (the times changed): look at the new SVGs, then accept them with
   `python check.py --update-baseline`.
4. Update `rules/halt_offsets.csv` from KAI's new published timetable, and the files in `reference/` if KAI
   publishes new station timetables.
5. If KAI changed the layout of the PDF (columns, fonts, symbols), the readers need adjusting: `gapeka_pipeline.py`
   (timetable pages, column positions `COLS`), `build_infra.py` (graph-sheet margins), `train_index.py`,
   `rosters.py`. `CLAUDE.md` records why every rule exists.

## Files

| File | Purpose |
|---|---|
| `gapeka_pipeline.py` | The one command: GAPEKA PDF → `gapeka.db`, `infra.db`, `rules.db` |
| `build_infra.py` | Graph-sheet margins → `infra.db` |
| `build_rules.py` | `rules/*.csv` + fitted model → `rules.db` |
| `gapeka_model.py` | Running-time model, halt and KA Dinas times |
| `train_index.py` | Reads the train-number index (Lampiran IV) |
| `rosters.py` | Reads the rolling-stock duty diagrams (locomotive, KRD, KRL) and the consists (stamformasi) |
| `station_layouts.py` | Reads the station track diagrams (tracks, lengths, directions, signal marks; OCR for outlined labels) |
| `export_poster.py` | Timetable table SVG for the transit map and posts |
| `export_headway.py` | Station time bar with headway colours (SVG) |
| `export_trains.py` | Station × train matrix (CSV) |
| `sanity_check.py` | Flags times going backwards, odd running times, long dwells → `sanity_flags.csv` |
| `check.py` | Regression checks of a pipeline run |
| `manual/*.csv` | Hand corrections for `infra.db` |
| `rules/*.csv` | Hand rules for `rules.db`, including KAI halt times (`halt_offsets.csv`) |
| `reference/*.csv` | KAI published timetables used to check the halt rules |
| `tests/baseline/` | TfB exports that `check.py` compares against |
| `requirements.txt` | Python packages |
| `manual_links.csv` | *(optional)* station distances to add or override in `gapeka.db`, `code,code,km` |
| `gapeka.db`, `infra.db`, `rules.db` | The databases |
| `*.svg` | Latest exported timetable graphics |
| `research/` | The experiments behind the model (see `research/README.md`) |
| `CLAUDE.md` | Decisions and railway knowledge behind every rule (notes for the AI assistant, readable by anyone) |

## Notes

- The GAPEKA is published by PT Kereta Api Indonesia. Times in the database are the planned schedule
  and may change with service announcements. Always check against KAI's current schedule before
  publishing.
- Halt and KA Dinas times are estimates; printed times are exactly as in the GAPEKA.
- The readers are tuned to the layout of the GAPEKA 2026 PDF (see "A new GAPEKA edition").
