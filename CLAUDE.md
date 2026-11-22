# Notes for Claude: Gapeka-parser

Read this first. It records decisions and findings from earlier sessions so they are not re-derived.

## Decided: three outputs, built in stages (user, this round)

1. **Infrastructure** `infra.db` (`build_infra.py`, DONE): from the graph sheets. Lines (one per sheet group),
   stations (code, name, kind, CTC, sheet name), km + elevation per line, sections (sheet order a = upper row,
   b = lower row; HULU = trains a -> b), Umum, ruling gradient, restrictions (offsets from a, km, tracks, cell note),
   block signals, double track (header list "JALUR GANDA" OR hu/hi tags; header lists are not complete, e.g.
   Jatinegara-Bekasi), raw header (dispatching stations "Ks X untuk lintas ..."), QA table. Manual infra fixes are
   CSV in `manual/` (station_aliases, pair_overrides, restriction_overrides), stored with source 'manual: ...'.
   OCR (rapidocr-onnxruntime) is required for outlined pages; results cached in .cache/ocr. Runs in ~25 s (cached).
2. **Timetable** `gapeka.db` (`gapeka_pipeline.py`, DONE; step [6]: train index (train_index.py, PDF
   pages 1825-1864, Lampiran IV, 2353 entries, every gapeka.db train is in it) -> trains.type / klasifikasi / kelas /
   facultative (209) / operator / index_km, table train_index, 550 KA Dinas trips added as trains (type dinas, stops
   kind 'via' between start and end, timed in step 9), view schedule_hhmm. Step [6] runs last so the distance / halt /
   note steps see exactly the old trains; exporter output verified byte-identical. Train 90 (Gaya Baru Selatan) was lost
   because its header words differ by 0.1 pt in height: text lines are now grouped by centres within 1.5 pt (fixed,
   orphan rows 123 -> 0). trains, stops, vmax, notes. Add: train type
   (krl / commuter / intercity / freight / dinas), facultative flag (F trains: run when KAI wants, still in all
   calculations), KA Dinas (R... trainset, L... locomotive movements; only drawn on the graph sheets and listed in the
   train-number / roster tables near the end of the PDF, e.g. "R49 L49-2" on the locomotive roster from page 1680,
   "Dinas Rangkaian" on the KRL loop rosters from page 1796). Halt times from the physics model, rounded per KAI region.
   Calculation in seconds; published columns HH:MM like KAI. Keep `stations`, `trains` and the `schedule` view
   unchanged for the SVG / CSV exporters (do not change SVG generation).
3. **Conditional rules** `rules.db`: hand rules as CSV in git (station zones, buffers, wait rules, KAI halt methods,
   halt offsets), validated and compiled into rules.db together with FITTED values (values the pipeline computes by
   comparing physics with GAPEKA: calibration, per-section corrections, terminus buffers, wait gaps, gradient
   allowance). Fitted values are recomputed per GAPEKA edition and never edited by hand.
- Every row carries source and PDF page (1-based); meta table holds the PDF hash, decree, valid-from date.
- Pipeline stages: build_infra / timetable / rules + fit / estimate; `gapeka_pipeline.py` stays the one command.
- infra.db findings: 13 sheet groups (pages 7-12, 13-18, 19-24, 25-27, 28, 29-31, 32-34, 35-37, 38-40, 41-43,
  44-46, 47-49, 50-51). Each line on a sheet has its own margin table (left border rule = one segment); sections never
  cross tables. Bandung row shares its line with a vertical label ("GELANG RAJA ..."): longest-matching word run.
  Duplicate names (Karangsari KRR / KRAI, Larangan LR / LRA) are resolved by timetable neighbours. Bypass sections
  from a row missed on one time page are dropped (only if no train runs them and other sections give the same km).
  Coverage: 99.3 % of timetable legs by one section, 100 % with chains (see the coverage notes below).

- Housekeeping (done): `check.py` (regression checks; `--run` rebuilds; `--update-baseline` accepts new TfB exports
  in tests/baseline/), `requirements.txt`, the old distance-fit halt step only runs with --no-model, the
  gapeka_missing_links.csv file is gone (infra.db's qa table lists uncovered pairs), research/README.md.

## Next project: schedule simulator (planning stage, user)

- Goal: a website where users modify / create trains and simulate them in a GAPEKA-style graph, timed with all
  researched GAPEKA behaviour (running times, halts, waits, conflicts).
- GAPEKA data is public (user): shipping the full data package to every browser is fine.
- Direction agreed so far: the pipeline (this repo) builds one data package per edition offline; the simulation
  engine runs in the user's browser (TypeScript / WASM in a Web Worker), checked against the Python engine with a
  "replay the official GAPEKA" test; static hosting; a server only later for accounts / shared scenarios.
- Open decisions: who uses it (TfB internal or public), starting scope (all of Java or one region), check-only
  conflicts vs automatic re-planning first, station track counts (not in the GAPEKA margins).

## Domain rules from the user (do not re-guess these)

- **hu / hi** = **hulu / hilir**, the two tracks of a double track. Not related to km direction. Hulu = trains
  moving from the top of the graph sheet to the bottom (arrow on the right of the sheet); hilir = bottom to top.
  "hu hi" / "hi hu" = restriction on both tracks. Only "hu" or only "hi" = that track only. No tag = single
  track, or applies to both.
- The timetable `vmax` per section is already the combined rolling-stock and infrastructure limit; it is the
  main speed reference. Local restrictions on the sheet lower it further.
- Gradient and curves are already reflected in the local speed limits; do not model them separately.
- Siding tracks in stations are typically limited to 30 km/h (some 40 or 20); 30 is a good placeholder.
  Sidings are used by trains stopping to be crossed/overtaken, by trains that need a siding for platform
  access, and at double/single track transitions.
- Kiaracondong: 1180 m crossover to crossover; affects intercity trains stopping there (towards/from Gedebage);
  commuters use the other side and are mostly unaffected.
- Bandung / Ciroyom / Andir (user): two lines. Main line Andir-Bandung direct (2.729 km); branch Andir-Ciroyom-Bandung.
  Ciroyom-Bandung is 30 km/h (Umum); a 90 read on the branch table is wrong (manual delete override).
- Kasugihan (KH, not Kalibagor) (user): the main line uses the junction north-east of the station's km mark
  (Lebeng-Kasugihan-Maos, KH-MA 1.723 km); the Cilacap branch uses the km of the station itself (KH-MA 1.966 km).
  infra.db keeps both as section variants (variant + context = stations of the sheet table); pick the variant whose
  context holds the train's previous / next station.
- Umum (user): Kebasen-Notog 80; Kemranjen-Sumpiuh 120 (Umum cell Kebasen-Kutoarjo). In manual/section_overrides.csv.
- Umum (user): Masaran (from Kemiri) to Walikukun is 120 (one tall Umum cell; its bottom rule at Walikukun is
  slightly slanted, so rules are now horizontal within 1 pt). The 105 near Sragen is Sragen-Kebonromo only
  (km 230.000-229.400, hu). Restriction ends are never taken from the curve column (labelled HILIR / HULU).
  Ngunut-Blitar Umum 120, Blitar-Wlingi 90 (user). Page 41 draws its Umum / gradient cells as rectangles: cell
  borders are read from lines AND rectangle edges (also fixed page 41 gradients, e.g. Madiun-Babadan 4, Caruban-Saradan 8).
  Panarukan-Kalisat has no Umum printed.
- Timetable DB policy (user): printed GAPEKA times are used as they are for every timed station; the physics model
  only fills halts (and KA Dinas passing points, which have no printed times). Physics is for other uses later.
  Verified: estimates never replace a printed time (halt_est = 9706 halts + KA Dinas passes).
- Stations with two or more km values are junctions or historical termini (user): both values are the same station
  on different lines; keep both (line_stations.km / km_alt; an extra km beside a known station joins that row, e.g.
  Krenceng). Angke-Kampungbandan is 4.102 km (user; KPB 1.364 is on the Jakarta Kota-Cikampek line, 0.363 another
  chainage near Jakarta Gudang): manual/pair_overrides.csv. KPB km 0.363 is probably the other yard track of
  Kampungbandan (user); the unnamed sheet row ?row491 (page 19) next to it has no effect on calculations.
- Names drawn as outlines on text pages (Tanjung p29, Cilaku p28) and missed by OCR (Karangtengah: italic km;
  Ijo: spaced letters in a dashed box): build_infra OCRs single name / km cells where the Letak column has a km
  without a name or a name without a km; unreadable names are taken from the timetable (the one station all trains
  run between the neighbouring rows). Soka: name row without km, its km (447.916) printed below the signal lines:
  SRW-SOA 5.041, SOA-KM 2.892 (timetable confirms 7.933 km SRW-KM; the old manual 3.396 / 1.645 was wrong, removed).
  Coverage now 99.3 % of legs by one section, 100 % with chains.
- Jatinegara-Bekasi is quadruple track (user): pages 13-18 (1-2A..1-2F) draw both double tracks: the old commuter
  line with the halts (JNG-KLD-BUA-KLDB-CUK-RWB-KRI-BKS, used by KRL) and the new direct intercity line (JNG-BKS
  14.653 km, Umum 120, used by intercity and freight, which list JNG-BKS directly). Both marked double track in
  manual/section_overrides.csv (field double_track).
- Single track (user): Cicalengka - Banjar - Gandrungmangu - Kawunganten - Jeruklegi - Kroya (the sheet's hu/hi tags
  on Gandrungmangu-Jeruklegi are not double track): manual double_track 0. Kutoarjo-Purworejo has no speed data.
  Kadipiro-Solo Balapan Umum 120 (cell empty); its restrictions are directional: 40 at km 104.800-107.200 "Dari KDO
  ke SLO", 30 at km 107.315-107.799 "arah SK" (stored with the note, applied to both directions for now).
  Kadipiro-Purwosari is a false pair (not adjacent; Purwosari is after Solo Balapan; page 38: the Adi Soemarmo -
  Kadipiro table ends with an up triangle, the Purwosari - Solo Kota table starts with a down triangle). build_infra
  never pairs an up-triangle row with the following down-triangle row (manual delete kept as a backup).
  OCR names clipped at both ends match a station containing them ("rencen" = Krenceng); spaced letters on text
  pages are joined ("S r o y o" = Sroyo). The unnamed Kampungbandan yard-track row keeps its empty name (user).
- Halts between Wonokromo and Sidoarjo are inactive but still in GAPEKA (user): keep them.
- KA Dinas passing times (model, scaled to the index times) are OK for now (user will check the graph later); pass
  rows without a printed time stay empty (user).
- Rolling-stock duties (rosters.py, pipeline step 6b): O.18 diagrams are time-bar charts, one row per unit per day.
  Locomotive pages 1680-1700 (row label = number in the first column; train numbers with names above the bar,
  KA Dinas R/L moves below), KRD page 1704 and KRL loops 1796-1803 / 1817 / 1821-1824 (line above the bar alternates
  station and train number: "Dp 1100 Boo 1145 Jakk"). 345 units (198 loco, 13 KRD, 134 KRL); 97 % of consecutive
  trains connect (2011 of 2077). Tables roster_units / roster_duties in gapeka.db.
- Priority (user): a lower train number has priority at crossings and overtakes; the higher number is usually the
  one that stops and waits. Data (research/priority_numbers.py, priority_rules.py, waiting train from the notes):
  overtakes 98 % (564; 100 % between different train types, 89 % within a type); crossings where only one train
  stops 74 % (82 % between types, 67 % within a type); both-stop crossings 66 % (weak test: there "waiting" means
  arriving first). Ranking by type / class (Argo > Eksekutif > ...) / regular-before-facultative before the number
  adds nothing. Crossing exceptions are real waits (median 6 min, not at the train's usual stops): on single track
  the train that reaches the loop first has to stop, so timing decides where priority cannot. The timetable data
  needs no change (the notes say who waits); the simulator uses this rule to decide who yields.
  User: real-world data follows the timing (sometimes the first train to arrive is the one that stops; keep the
  printed waits); the simulation auto-calculates with the number rule. Written as hand rules in rules/priority.csv
  (rules.db table priority): overtake -> higher number waits; crossing -> lower number keeps running, unless it
  reaches the loop first while the other is still in the section, then the first to arrive waits; equal numbers ->
  regular before facultative (assumed).
- Station track diagrams (right of each graph sheet, user): label "track-effective length (m)" ("I-255,9").
  Roman numerals = main or siding tracks; Arabic = stabling / yard only (no crossing / overtaking use). Arrows show
  the NORMAL running direction; Indonesia runs on the RIGHT track on double track. Main straight through tracks are
  usually one direction only (from where normal trains come to where they go); some sidings can take the other
  direction if the layout allows. Box with a dot and a horizontal line = PPKA (station master) post; box with a dot
  only = PAP. The icon boxes further right are facilities for staff: not relevant. A train must fit the track
  (effective length) to wait for a crossing / overtake (ignore special cases). Diagrams show no speeds.
  Train length: stamformasi tables (PDF ~1705-1745: loco type, cars per class, set length in cars).
  Crossing with a train that does not fit (user): the long train departs first, the other train must stop and fit
  (or the two use non-adjacent tracks). Overtake: every train must fit. Rules in rules/priority.csv.
  Double / single transition (user, Cikadongdong): a train waiting on its own running track from the double-track
  side never needs to fit (rear on the double track); the through train uses the other track.
  'Emplasemen X periksa (pada) lintas Y' notes (user): X's diagram is drawn on line Y; skip X there (infra.db
  station_layout_refs; the note takes X's row so no diagram there is assigned to X by position). A station can
  have completely different diagrams on different lines (two levels, isolated tracks): keep one per line
  (station_layouts.line_id), never merge them.
  Extraction (station_layouts.py, called by build_infra): 419 diagrams / 388 stations / 1661 tracks -> infra.db
  station_layouts (evidence = how the station was identified: neighbours at both ends, one end + own name, own name,
  pointer arrow, nearest row; weak ones in the qa table) and station_tracks (track, kind roman / yard, length,
  arrows L / R / LR, towards = neighbour of the normal direction, through = runs over the whole diagram). Notes:
  end labels name the next STATIONS (halts skipped); some sheets draw tracks as filled outlines with arrowheads,
  hairline outlines or thin 0.28 pt lines, filled outlines can hold several tracks
  and draw long edges as 2.6 pt pieces (Cikadongdong III-260 was lost), chevrons up to 4 pt; double-track stations can be drawn as two unconnected halves; touching
  diagrams (Semarang Poncol / Tawang) are split by the end labels of their through tracks; page 28 repeats its
  diagrams; pages 19 / 35 / 50 need OCR for names (cache tag "layouts"). Single-track mains show arrows both ways
  (LR) or none (Merak line). Consists: rosters.read_consists -> gapeka.db consists (415 trains: cars per set =
  SF / RANGK, locomotive class). Checks (research/layout_checks.py): peak standing trains never exceed Roman tracks
  (298 stations, 0 exceptions); waiting trains fit the longest Roman track in 99.2 % (cars x 21 m + 16 m; p5 margin
  33 m); every misfit (Rogojampi, Temuguruh, Singosari, Kalisetail) follows the crossing rule above. Fit uses the
  diagram of the train's line (a 'periksa' note copies the other line's diagram, user). Reader also handles: pointer
  arrows from a name to a displaced diagram, combined end labels (WDW/TG), own-name labels only when on the
  station's own row and not italic, Y-numbered yard tracks. Manual: layout_station_fixes.csv (which station),
  layout_label_fixes.csv (misprinted end label, e.g. Gedebage Gdb -> KAC), layout_tracks.csv (unreadable tracks).
- Cipeundeuy (CPD, user): every train stops at least 10 min for a brake check (summit 768 m between 25 permil
  grades). GAPEKA: all 30 trains stop, 10-23 min, median 10; the only such station in Java. rules/mandatory_stops.csv.
- Station diagrams (user): A = Surabaya Kota, B = Benteng, C = Sidotopo on page 44 (manual/layout_station_fixes.csv);
  the 'Sb (Hilir)' label near Surabaya Kota belongs to another station. Italic names on the sheets are halts (no
  diagram). Track names Y1..Y3 (Surabaya Kota) are yard tracks.
- Halts on the graph sheets (user): their row line is drawn DASHED / DOTTED (e.g. Pondok Rajeg, Gunung Putri p19),
  and their names are often in italics; halts have no track diagram. Page 19: Cibinong = II-403 / I-454, Nambo =
  I-553 ... 8-530 (user). Page 35: Karangtalun I-368 II-656 III-628 IV-630 + yard 5-425 / 6-375 (cement loading,
  labels too small to read: manual/layout_tracks.csv); the diagram below it is Gumilir; Balapulang has none.
  Batutulis: one through track, no siding (manual). Saradan's diagram (ends Crb / Bgr): Wilangan has none.
  Remaining timetable stations without a diagram (29) all have none on the sheet (halts, Sukabumi-line stops, Soka,
  Cisomang, Sadang, ...).
- Dashed connections on a station diagram = out of service (user, Dawuan IV-388 / I-288). station_tracks.out_of_service
  (a non-through track reached only by dashed lines, dash pattern [2.4 2.4]); roman_tracks / longest_roman_m count
  usable tracks only. Found: Dawuan IV, I; Pabuaran IV, I. Dawuan in GAPEKA: 158 intercity + 32 freight pass, 13
  Walahar / Jatiluhur commuters stop 1 min on the mains, no waits: a two-track station in practice.
- Track use by direction (user, Dawuan): a train only moves forward, so a track is usable in a direction only if
  switches lead onto it from that direction's main and on to the exit main; through mains only in their arrow
  direction. Dawuan (if in service): IV westbound only (no crossover from the west), III westbound, II eastbound,
  I both ways. station_layouts.usable_directions() derives this from the switch geometry (connections = chains of
  curve pieces, joined at ends and mid-way on ladders; tiny flat steps of polyline curves count; entry / exit mains
  = tracks at that diagram edge with the direction's arrow, else every track there): station_tracks.usable (L / R
  on the sheet), usable_towards (neighbour stations), enter (directions that can get onto it, termini / dead ends).
  Also: running lines drawn as curves to arrowheads beyond the labelled tracks (Cikarang, Malang Kotalama) give
  extra entry / exit points (union of both runs; through mains with arrows keep their arrow direction); a switch
  may meet a track up to 20 pt beyond its labelled span (Haurgeulis IV); every pair of tracks one switch group
  touches is linked. Any track with a drawn arrow is used only in its arrow direction (user, Ciroyom: III to
  Andir, I to Bandung; II = arrival from Bandung). Filled outlines draw curves as 0.3 pt steps (Ciroyom I).
  research/layout_directions.py vs GAPEKA: overtakes with a usable waiting track for the direction 453 / 456
  (99.3 %), crossings with a usable track pair 730 / 732 (99.7 %); misses: Bowerno, Kalimas, Kretek (1-2 each).
  Roman tracks usable in no direction: 119 of 1635 (mostly termini and big yards; see 'enter'). Cikarang and
  Malang Kotalama draw their running lines as unlabelled curves (user): Malang Kotalama is single track, all Roman
  tracks both ways (the reader agrees); Cikarang mains VI eastbound only (pass-through) and VII westbound, VIII
  westbound only: manual/layout_track_directions.csv (page, station, track, usable, main). A missing end label
  (slanted / outside the diagram) is filled with the other neighbour station on that line (halts skipped).
  After that: crossings with a usable track pair 749 / 753, overtakes 453 / 456. Then: tall vertical lines (icon
  boxes) no longer join two diagrams (Bowerno was merged with the Gembong-side diagram below it); page 44 fixes:
  Mesigit = 'Empl. Mst' I-232 / II-236, Kalimas = the terminus yard X-354 ... I-461 (arrows from the names),
  Surabaya Pasarturi = VIII-337 ... I-281 (was taken by Kandangan). Now crossings 751 / 753, overtakes 457 / 458;
  Sidotopo (user): a yard station without routine through traffic, every Roman track usable both ways (manual
  row with track "*"). Kretek (KRT, double track BMA-KRT-PAT): IV-335 label is an outline on page 35, added by
  hand (manual/layout_tracks.csv), westbound only (loop on main III, like Dawuan IV). Now crossings 753 / 753,
  overtakes 458 / 458.
- Track labels drawn as outlines (pages 35-37, mostly IV / V): station_layouts.ocr_labels() OCRs the diagram column of
  all three pages (they repeat the diagrams; union fills one page's OCR misses, e.g. Bumiayu V-406 only on page 36)
  and adds labels where no text label sits: 19 tracks (Patuguran IV-297, Maos IV-254 / V-202, Kutoarjo IV-376 /
  V-286, ...). Filled-outline mains can be drawn very slightly sloped (Butuh: 0.7 pt over 89 pt): long edges count as
  horizontal up to a 1.2 % slope (and are not switch pieces) -> Butuh II / III / IV-500, Tambak I-538 / III-529.
  Kretek IV-335 is not found by OCR either (overlapping labels): manual. After this: overtakes 471 / 471, crossings
  743 / 743 with usable tracks; capacity still 0 exceptions.
- All 45 graph pages: OCR of the diagram column for every page (cache tag "layouts", ~4 s / page), every sheet group
  (OCR_LABEL_GROUPS); a label position is read on each page of the group and the majority reading wins (OCR mixes
  I / 1). Outside pages 35-37 OCR added only Bangil IV-380 / V-315 (page 50): the other sheets have text labels.
  Labels on no track line are flagged in the qa table ("track label on no track line"). Fallbacks for labels whose
  track is shorter than the label (line turns into a curve right beside the text) or sits on a curve: Jakarta Kota
  2 / IX / XI (both pages; its lower tracks are drawn apart: manual station fix on page 19), Arjawinangun V-459,
  Cikarang 1-208, Prupuk 6-257, Sukabumi III-399, Sidoarjo II-371, Wonokerto III-293, Kasugihan III-302. Manual
  (unreadable): Karanggandul I-334, Rangkasbitung yard 8 / 9 (hairlines). Direction edges use Roman tracks only (a
  long yard track moved Cikarang's edge). 1719 tracks; overtakes 463 / 463, crossings 751 / 751; capacity 0
  exceptions; 3 labels still flagged in qa (Karanggandul I-334, Rangkasbitung 8 / 9), covered by manual tracks.
  'Unusable' (usable = '') = no forward path from an entry main through the track to an exit main in either
  direction: termini / dead-end tracks (see 'enter'), or switches the reader could not follow.
- Entry-signal gradient mark (user): a small signal icon at a diagram end (post base tick - stem - lamp, lamp open or
  a small dot, with or without a raised arm) = "kelandaian sinyal masuk lebih dari 8 permil, tidak perlu
  mempertahankan sinyal masuk pada semboyan 7": trains arriving from that side are not held at the entry signal
  (plan the wait at the previous station). station_layouts.signal_marks() -> infra.db station_signal_marks (station,
  line, side, towards); 54 ends, all with a known gradient face 10-40 permil (7 on mountain lines without a stored
  gradient). Rejected look-alikes: switch points drawn as circles on track lines (pages 47 / 50), the dot of PPKA /
  PAP boxes, dots at the end of switch curves, stems lying on a track line (stub ends). Rule in rules/priority.csv
  (event entry_signal). Linggapura (LG) is the page 35 mark at y ~539 (not Lebeng).
- Tarik to Sepanjang has a long local restriction (100 km/h) on the hulu track only.
- Bekasi is where most trains stop.
- Kiaracondong arrivals from Gedebage also cross the crossover, slightly shorter than departures (one crossover fewer
  per end). Data still prefers about 1180 m for both; commuters fit a 600 m zone on their side.
- Yogyakarta: originally two emplacements (north, south), connected at both ends after the regauge. Stopping trains
  always use sidings (speed limit + stopping). Through trains from Patukan normally get the west crossover and use
  the south emplacement; through from Lempuyangan use the north one and get the east siding switches; using the
  other emplacement means two restrictions. Fitted (30 km/h): stopping 300 m dep / 600 m arr; through trains
  600 m entering + 300 m leaving (data says leaving is slowed too); freight fits better without zones.
- Semarang Tawang layout (user): trains from the east that stop use both crossovers (~1.5 km apart); trains from the
  west that stop only pass the siding switches (~750 m apart); through trains from the east are hardly affected,
  through trains from the west may be. Fitted (30 km/h): from the east 1000 m arriving + 1000 m leaving west; from
  the west 750 m arriving + 750 m leaving east; through from the west 750 m. Plus a buffer of +45 s per leg on
  Poncol-Tawang-Alastua for all trains (layout alone leaves 40-100 s). Mean bias over all Tawang cases 95 -> 36 s,
  > 1 min 80 -> 22 %.
- Semarang Tawang east side, west of the crossover: 30 km/h at km 0.400-0.800 (Tawang-Alastua). The sheet tags it
  hulu only, but GAPEKA slows westbound through trains there too; applied to both tracks as a manual override
  (user agreed: "if that's what the data proves"). Intercity through from the east -83 s -> +21 s.
- Manual overrides of sheet restrictions live in manual/restriction_overrides.csv (source = manual) and are
  applied by build_infra.py when infra.db is built, so re-extracting the sheets never loses them.
- Cibinong / Nambo branch: two tracks at Cibinong, KRL use one through track; no further speed limit (user).
  GAPEKA adds about 2 min per KRL leg on Citayam-Cibinong-Nambo (likely buffer time): rule type "buffer"
  (+120 s per leg, rules/section_buffers.csv) takes the branch from 98 % to 2 % legs > 1 min.
- Error sign convention in reports: model minus GAPEKA; minus = GAPEKA slower (allows more time) than physics.
  Say this explicitly to the user.
- Yogyakarta freight: same tracks as other trains (user). Most freight services (Bungtalun, Karsolo, Kalem,
  Cilace Trans) are ~40 s slower in GAPEKA on the Patukan side; Mawalo Tanker (50-55 km/h) is the opposite, so no
  freight zone yet. Freight keeps ~46 s fixed time per leg even passing at both ends.
- Calibration now has separate fixed time for starting from a stop and stopping at the end (ENDS=1).
- Sepanjang: commuters use siding tracks in both directions; towards Wonokromo more switches (fitted 900 m).
- Sukabumi line: trains do not use loops at crossing stations except maybe Sukabumi (enters slow). The line is
  slower than the rest of Java; its sheet restrictions were checked and are extracted correctly.
- Ciroyom: commuters to Bandung are often held at Bandung's home signal (slip switches, one train at a time);
  commuters to Andir wait for main-line trains (Bandung-Andir) to reach Cimindi's home signal. Operational waits,
  not physics: leave them to the learned corrections.
- Track notes in restriction cells: "Jalur ..." alone is usually loop/siding only, but not always.
  "Dari/Ke Jalur X, XI, XII, XIII" at Manggarai = commuter tracks to Jakarta Kota and Bogor (KRL only).
  Rangkasbitung: KRL use tracks 3 and 5, both 30 km/h for KRL (switches from the Tanah Abang side).
- Jakarta core: the timetable has its own speed limit; intercity trains there are mostly arriving at / leaving their
  terminus (Gambir, Pasarsenen), which carries recovery time (fitted: intercity +89 s at terminus arrival,
  +66 s at origin departure).
- Boharan / Kedinding halts: KAI may publish the halt departure there (not arrival).
- Bandung station: the "Empl. Bd" restriction on the sheet is the only one; nothing else leaving Bandung.
- Sepanjang to Wonokromo is single track; trains from Sepanjang take a 30 km/h switch at the Wonokromo end of
  Sepanjang (fitted zone 200 m).
- Crossing / overtaking waits (user): a crossed train only waits for the dispatcher to come back and set the route;
  an overtaken train must wait until the overtaking train has arrived at the NEXT station, then for the dispatcher
  to report clear and set the route. Data (research/waits*.py): waiting train departs +2 min after the reference:
  crossing = other train arrives/passes the crossing station (84 % within 0.5 min when the other passes, any line);
  overtaking = overtaker at the next station (single-track lines 78-88 % within 0.5 min; double-track main lines
  vary -1..+3 min, about 60-70 % within 1 min). Waiting for the overtaker to pass the station itself is wrong.
  Waits are not part of running time; running-time error on noted legs is spread, not a wait effect. GAPEKA lets
  a train run a little slower (+0.3-0.9 min) into a crossing when it has slack, and keeps it about 4 min before
  the other train. Training with noted legs + wait-role flags (NOTE_FEATS=1) takes noted legs from 12 % to 9 % > 1 min.
- Signalling (user): single-track lines mostly mechanical semaphores (overtaken train waits for the overtaker to
  arrive at the next station); double-track lines have electrical/PLC interlocking (the overtaker only has to reach
  the next block or home signal). Block signals are printed in the station-name column, usually with km
  ("B-0715 km 330.500 hi"), sometimes without ("B.hu - B.hi"). Extracted: 412 block signals on 118 station pairs
  (92 with km, 320 without). Signals without km are automatic block signals (user): spaced evenly over the
  section per track (N signals -> N+1 equal blocks); median block 0.57 km (user: ABS blocks are a few hundred m).
  Where overtakes happen there is usually only 1 signal per section on the waiting train's track, so "k blocks
  ahead" cannot be tested properly (research/waits10.py).
- Overtaking rule to use (user): a waiting train may depart when the overtaker has passed the NEXT BLOCK POINT.
  Double track (automatic block): first block signal on that track, including signals at halts; a halt is NOT a
  block point by itself (user: not all halts are); else the next real station. Single track (semaphores): next real
  station (halts have no signals).
  research/waits11.py: this is a lower bound that GAPEKA respects (only 4 % of double-track and 1 % of single-track
  waiting departures are earlier); GAPEKA often leaves later (median +3.5 min double, +2 min single; 75th pct
  16 / 11 min): planning slack. Use it as the earliest departure, not as the exact one. Double track within
  ±0.5 min of the typical gap: 12 % (block rule) vs 4 % (next-station rule).
  None found on Tegal-Semarang. With evenly spaced blocks the next station is still the best reference (40 %).
  Data: single track next station + 2 min fits (71-79 %). Double track: no block distance gives consistent
  departures; "overtaker reaches next station + ~0.5 min" fits best (40 %) where blocks are known; waiting trains
  (mostly freight) leave 5-8 min after the last train passed and ~20 min before the next one. Planners seem to work
  from the station lines of the graph even on double track.
- Waiting-train departure rules (research/wait_fix*.py, passenger trains; gaps in rules/wait_gaps.csv):
  crossing, other passes: other pass + 2 min (89 % exact). Crossing, both stop: if the other train dwells <= 2 min
  the waiter leaves 2 min after the other DEPARTS (sequential departures), else 2 min after it ARRIVES (48 -> 80 %).
  Overtake: overtaker at next block point + gap per line and track (default single 2, double 2.5; Cikampek-Bandung 0,
  Cikampek-Tegal / Tegal-Semarang double 3). Waiting for several trains: the latest rule wins; at least arrival + 1.
  All passenger waits 63/73 -> 75/84 % (exact / within 1 min). Double-track overtakes stay ~45 % exact (planning
  slack, not missing trains: notes are complete, no un-noted same-direction trains during waits).
- Coverage round: block-signal markers ("B.hu - B.hi") were being read as station names and broke pairs (Tangerang
  line, Bekasi line, Lentengagung); station names are now built from words (a text line can be "33.359 Tambun"),
  the km column tolerance is +-9 pt, OCR names are cleaned (stray marks from dashed CTC boxes, full-width letters).
  physics_model.profile() derives missing pairs: join through one sheet station (sheet wins over gapeka.db),
  split a sheet pair by gapeka.db distances or by shortest timetable running times, last resort gapeka.db
  distance; manual pairs in manual/pair_overrides.csv.
  Legs with sheet data 85 % -> 99 % (missing only airport/JIS/YIA/Larangan-Songgom/Cibadak/Indro branches).
  Held-out, no notes: 81 / 15 / 4 % on 6001 legs.
- Ka Feeder runs like an EMU (KRL accel/brake): physics bias +64 s -> +21 s, > 1 min 75 -> 25 % (EMU_LIKE in relearn).
  Walahar / Jatiluhur do not fit KRL performance; kept in the loco/KRD group.
- Prupuk-Purwokerto (Bumiayu climb, 36 -> 329 m, 14-16 permil): sheet limits are extracted correctly, GAPEKA still
  plans 50-80 s slower per section in both directions (like the Sukabumi line): left to learned corrections.
- Gambir / Pasarsenen (intercity) and Surabaya Kota / Gubeng (commuters) carry extra terminus time beyond the
  general allowance (user: trains are held at busy termini's home signals while the dispatcher clears traffic).
- East Java single track: errors are mostly GAPEKA's own spread (0.3-1 min for the same leg); legs with a
  consistent bias worth checking for limits: Leces-Malasan, Lawang-Singosari, Kalisat-Kotok, Arjasa-Kotok,
  Malasan-Ranuyoso. (KLT is Kalisat, not Klakah: look station names up, never guess them from codes.)
- Renamed stations: Klakah (KK in the timetable) is called Lumajang on the graph sheet (user). Aliases live in
  manual/station_aliases.csv; RDA-KK 11.506 km, KK-RN 4.903 km.
- Gradient check (research/grade_check.py, single-section pass->pass legs): the timetable does NOT lower the speed
  limit on steep sections (vmax < Umum only 3-14 % there vs 78 % on flat); instead GAPEKA adds time symmetrically
  uphill and downhill: time / time-at-limit 1.13 on flat (4.6 s/km), 1.22-1.29 at |grade| >= 5 permil (10-13 s/km).
  A planning allowance on steep sections, not a traction effect. Tested (user agreed): ruling gradient ("Lereng
  Penentu") extracted per pair (452 of 584; elevation gradient as fallback); calibration term per type for km on
  sections >= 10 permil: intercity +4.0 s/km, commuter +4.4, freight +2.0, KRL ~0; 5-10 permil ~0. Physics-only
  steep legs > 1 min 10 -> 8 %; full model unchanged (corrections already learned it). On by default (GRADE_ALLOW=1). User limits: Leces-Malasan 70, Malasan-Ranuyoso 100 with a 70 restriction,
  Lawang-Singosari 70 (all already extracted); Kotok "has complicated speed limits" (sheet shows only 90 general +
  70 at km 211.4-209.2 on Kalisat-Kotok).
- Dashed station boxes on the graph sheets = centrally controlled stations (CTC) (user). The marks OCR adds
  around names (":", "I", "F") come from these dashed boxes, not from halts. 26 CTC stations detected on the text
  pages (Cikampek-Cirebon, Klaten-Solo-Sragen; OCR pages not yet), stored in rules/station_attributes.csv.
  Overtakes on double track: CTC releases the waiting train a bit sooner and more consistently (median +3.0 vs
  +4.0 min after the next block point; 28 % vs 6 % within 0.5 min; n = 50). No crossings at CTC stations found.
- User decisions (this round): freight calculation is on hold (freight hardly follows GAPEKA; 1 min error is fine).
  Walahar and Jatiluhur are locomotive-hauled (stay in loco/KRD). Bandung halt misses: user does not know, leave.
  Kotok: covered by the gradient allowance (Kalisat-Kotok residual 77 s -> 21-35 s), no manual rule.
- Slow-zone rules (research/fit_slow.py, fitted from training residuals; rules/station_buffers.csv and
  section_buffers.csv): terminus time Gambir intercity +139 s arr / +97 s dep, Pasarsenen +124 / +147, Surabaya Kota
  commuter +117 / +115 (busy termini, trains held at the home signal); Ciroyom commuter stops +47 arr / +86 dep;
  Sukabumi line intercity +10.5 s/km; Prupuk-Purwokerto intercity +3.3 s/km. Rule kinds in relearn.py: buffer,
  buffer_km, station_buffer. Physics-only held-out 64/25/11 -> 66/24/10 %; terminus legs >1 min 95 -> 18 %;
  full model unchanged (81/16/4).
- Freight accel/brake: a grid over a0 0.05-0.35, power 0.6-3, brake 0.05-0.45 changes freight error by < 1 s; 73 %
  of freight legs pass at both ends. Freight error is pathing (fitting into gaps), not acceleration.
- Errors < 0.5 min: model slower than GAPEKA 55 %, faster 45 %, most within 6 s; larger errors are mostly GAPEKA
  slower (buffers, waits). Rounded to whole minutes: 77 % exact, 11 % 1 min early, 8 % 1 min late.
- GitHub: access granted to transportforbandung/Gapeka-parser; push to branch claude/timetable-halt-time-patterns-cdve9b.
- Errors below 0.5 min are acceptable (GAPEKA rounds to whole minutes). Goal: shrink the 0.5 to 1 min band
  and almost eliminate errors above 1 min.

## Reference documents (user, Nov 2026): PM 110/2017, ITB "Perencanaan Operasi KA", PTDI "Pembuatan Gapeka"
- PM 110/2017 Pasal 11(2): train numbers follow the train hierarchy -> confirms "lower number has priority".
  Graph line styles: solid = passenger, dashed = freight, cross marks + "F" = facultative. Running time is set from
  infrastructure / rolling-stock speed, train speed, distance and service type (Pasal 10).
- Planning = minimum technical running time + time supplement (our calibration margin / buffers). Conflicts are
  solved by geser (shift the departure at the origin), ulur (lengthen a stop) or batalkan (cancel); an extra train
  (KLB) is held at a station so regular trains stay on time; conflicts propagate (simulator actions).
- Single track: crossing / overtaking only at stations; one train per petak jalan; the longest petak sets capacity.
  Teaching example (only an example, user): the follower leaves B when the leader is at C + 4 min safety. Use the
  values learned from the GAPEKA graph, not the 4 min. Tested on GAPEKA 2026
  (research/headway_single.py, consecutive same-direction trains between timed stations): single track never has two
  trains in one section (0 of 908); gap >= 4 min in 97.5 %, the rest exactly 2-3 min (the 2 min dispatcher gap we
  already use for waits). Simulator: following gaps come from the learned GAPEKA data (per line / section), not
  from the teaching example; double track allows following inside the section (3 % gap < 0, block signals).
- Double track: no crossings; overtakes only for speed differences or a late train. Turnaround trains wait for the
  incoming trainset (rosters). Window time (track work) forces re-scheduling for that day only.

## Facts about the PDFs

- Page numbers: use 1-based PDF page numbers when talking to the user. `gapeka.db` page labels ("0:34")
  are 1-based; pymupdf indexes are 0-based.
- Graph sheets are PDF pages 7 to 51 (13 line groups, each over several time pages). Pages 19 to 24
  (Kampungbandan, Duri, Tanahabang, Manggarai) and 35 to 37 (Prupuk, Purwokerto, Kroya, Kutoarjo) draw
  station names as outlines, not text: OCR is needed (rapidocr-onnxruntime works, ships its own models).
- The margin "Jarak" column gives station-to-station distances that skip halts; use the "Letak" km positions.
  The current `gapeka.db` has some wrong links from this (e.g. MR-CRM 11.752 km is really SBO-MR; MR-CRM is 8.122).
- Some stops are on the sheet but not in the timetable (Bangsal, Kumendung, ...): join pairs through them.
- KAI Commuter Jabodetabek / Nambo / Basoetta station timetables (1 Des 2026, user; all KRL, Basoetta = express KRL):
  research/kai_jabo_extract.py -> reference/kai_krl_jabodetabek_2026.csv (20382 cells, 1238 train numbers, all in
  gapeka.db). At GAPEKA stops KAI prints the departure (8688) or, at the end, the arrival (1274); 9903 halt times to
  learn from. Basoetta trips carry two numbers (809 Bogor-Duri, 810 Duri-Bandara); KAI's Duri time is 810's departure.
- KAI Jabodetabek KRL halts vs model (research/jabo_halts.py, 8709 halts in 4346 halt chains): KAI follows the SPLIT
  rule (like Bandung), not front-loading (Surabaya): GAPEKA's time between the timed stations is shared by the
  calibrated sub-leg times, 20 s dwell per halt, KAI time = arrival rounded to the minute. Held-out: 75 % exact,
  99 % within 1 min, bias +0.04 min (front-loaded: 34-41 % exact). The misses sit on the rounding boundary (worst
  halts bias +-0.5 min); a fitted offset per halt + direction (halt, previous point, next point; median of training
  residuals) gives 91 % exact, 100 % within 1 min held-out. Raw model sub-legs between halts are ~50 s longer than
  KAI's (each rest-to-rest sub-leg carries the full leg constants), so KRL halt chains need their own dwell /
  start-stop constants if absolute times are wanted; the split rule makes this irrelevant for publication.
- KAI Commuter published timetables (Bandung, Surabaya, Dec 2026): halt time = arrival at the halt. Current rules
  (rules/halt_rules.csv, pipeline model): Bandung split-min (shortest leg time of the same leg shared, no dwell)
  79 % exact; Surabaya split (own leg time, no dwell) 84 % (front-loaded 79 %, 56 halts only); Jabodetabek split +
  20 s dwell + fitted offsets 90 % held-out. The older research notes below (front-loaded Surabaya) are superseded.

## Research results (scripts in `research/`; the model is now in the pipeline as gapeka_model.py)

- `research/line_profile.py`: extracts the sheet margins (585 station pairs, 352 restrictions) to `line_profile.json`.
- `research/physics_model.py`: running-time simulation: speed cap = min(timetable vmax, Umum, restriction on the
  train's track); accel min(a0, power/v); braking b; time = sim*(1+margin) + const + dwell*halts.
- Fixed accel/brake per group (from clustering per-service fits):
  KRL a0 0.45 m/s2, power 4.2, brake 0.85; locomotive/KRD a0 0.15, power 2.3, brake 0.15;
  freight a0 0.08, power 1.2, brake 0.08. KRD did not separate from locomotive trains.
- `research/siding_lib.py` + `research/errors.py`: evaluation on held-out trains (every 5th train number) and
  error breakdown by line/station. Best for running times: physics + per-section correction
  (about 76% < 0.5 min, 19% 0.5-1, 5% > 1 on legs without crossing notes). Standard-time lookup (identical
  leg on other trains of the same service) with physics as fallback brings > 1 min to about 1%.
- KAI halts (`research/halts_points.py`, `halts_final.py`): physics with restrictions and fixed group params,
  splitting the shortest leg time: Bandung 80% exact (100% within 1 min), Surabaya 61% exact.
- KAI halts re-tested with the latest model (`research/halts_latest.py`): the two regions publish differently.
  Bandung (Wilayah 2): split the shortest leg time by calibrated sub-leg times -> 80 % exact, 100 % within 1 min.
  Surabaya (Wilayah 8): previous departure + calibrated running time to the halt -> 86 % exact, 100 % within 1 min
  (was 61 %); Boharan / Kedinding now within 1 min. Combined rules (earlier of the two, average) are worse:
  store the method per KAI region in the rules database. Remaining Bandung miss: Cikudapateuh after Bandung
  (model 5.8, KAI 5).
- Learned per-station "points zones" overfit; use the manual station zones instead (rules database).
- The per-section correction must not be shared between commuter and intercity trains in the same group.
- Outputs of the research scripts go to the working directory; they expect the repo at /home/user/Gapeka-parser.

### Later round (research/relearn.py, the current best)
- Train types: krl / commuter (non-KRL commuter, Ka Feeder, Bandara SMO) / intercity / freight. Calibration
  (margin, const, per-halt, terminus arrival, origin departure) per type; corrections (ridge) per directed section
  and type, per station end (dep/arr, stop/pass, type) and per halt station and type (dwell).
- Held-out, legs without notes: physics + calibration 63 / 25 / 12 %; with corrections 80 / 16 / 4 %
  (< 0.5 / 0.5-1 / > 1 min). KRL 90 / 8 / 2.
- Timetable vmax on row i applies to the section i -> i+1 (tested against 'before' and 'min': slightly best).
- Station zones: rules/station_zones.csv (pipeline); research scripts use research/rules/rules.json (RULES env var).
- Extraction fixes: restriction start/end paired by midpoint closest to the speed (Madiun), long restrictions
  (Tarik-Sepanjang 31 km, Wates-Rewulu 10 km), joined tags 'hi-hu', notes in restriction cells (Jalur),
  validate_profile.py checks pair lengths against the timetable (fixed Gundih-Gambringan, Surabaya Kota-Gubeng).
- Gradient from sheet elevations: fixes the Sukabumi-line bias but is worse network-wide; elevations are crude. Off.
- KRL halts: one dwell per halt does not fit (Pasarminggu-Manggarai gets ~the same time with or without halts:
  path slots); per-halt-station dwell is learned instead.
