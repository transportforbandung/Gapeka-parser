# Notes for Claude: Gapeka-parser

Read this first. It records decisions and findings from earlier sessions so they are not re-derived.

## Decided: split the data into two databases

1. **Time database** (`gapeka.db`, as now): what the GAPEKA PDF says.
   Trains, stops, timetable times, notes/constraints, graph lines, and the estimated halt times.
2. **Rules database** (new, working name `infra.db`): infrastructure and local knowledge that helps the calculation.
   Built partly from the GAPEKA graph-sheet margins, partly by hand. It holds:
   - **station zones**: station-area speed zones per train type, e.g. Kiaracondong, side towards Gedebage,
     1180 m at 30 km/h for intercity trains that stop there (crossover to crossover); commuters use the other
     side of the station and are not affected
   - **local restrictions**: from the sheet column "Antara KM / Dengan KM / Dibatasi Sampai" plus manual
     additions/overrides, with track (hulu / hilir / both) and the train types they apply to
   - **line profile**: per adjacent station pair, length (from "Letak Stasiun di KM") and general speed ("Umum")
   - **halt offsets**: the KAI published halt rules, moved here from `halt_offsets.csv`
   - (likely) train type per service and rolling-stock parameters (accel/brake per group)

   Manual rows must carry a `source` (sheet / manual / KAI timetable) so extracted data can be rebuilt without
   losing hand-entered rules.

Not implemented yet. Next step is building `infra.db` and moving the research model (below) into the pipeline.

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
- Manual overrides of sheet restrictions live in research/rules/restriction_overrides.csv (source = manual) and are
  applied by physics_model.py on load, so re-extracting the sheets never loses them.
- Cibinong / Nambo branch: two tracks at Cibinong, KRL use one through track; no further speed limit (user).
  GAPEKA adds about 2 min per KRL leg on Citayam-Cibinong-Nambo (likely buffer time): rule type "buffer"
  (+120 s per leg, research/rules/section_buffers.csv) takes the branch from 98 % to 2 % legs > 1 min.
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
- GitHub: ignore pushing for now (no access yet); commit locally.
- Errors below 0.5 min are acceptable (GAPEKA rounds to whole minutes). Goal: shrink the 0.5 to 1 min band
  and almost eliminate errors above 1 min.

## Facts about the PDFs

- Page numbers: use 1-based PDF page numbers when talking to the user. `gapeka.db` page labels ("0:34")
  are 1-based; pymupdf indexes are 0-based.
- Graph sheets are PDF pages 7 to 51 (15 line groups, each over several time pages). Pages 19 to 24
  (Kampungbandan, Duri, Tanahabang, Manggarai) and 35 to 37 (Prupuk, Purwokerto, Kroya, Kutoarjo) draw
  station names as outlines, not text: OCR is needed (rapidocr-onnxruntime works, ships its own models).
- The margin "Jarak" column gives station-to-station distances that skip halts; use the "Letak" km positions.
  The current `gapeka.db` has some wrong links from this (e.g. MR-CRM 11.752 km is really SBO-MR; MR-CRM is 8.122).
- Some stops are on the sheet but not in the timetable (Bangsal, Kumendung, ...): join pairs through them.
- KAI Commuter published timetables (Bandung, Surabaya, Dec 2026): halt time = arrival at the halt, front-loaded:
  previous departure + fixed run time; spare time sits after the halt.

## Research results (scripts in `research/`, not yet in the pipeline)

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
- Station zones (draft rules table): research/rules/station_zones.csv, used via rules.json (RULES env var).
- Extraction fixes: restriction start/end paired by midpoint closest to the speed (Madiun), long restrictions
  (Tarik-Sepanjang 31 km, Wates-Rewulu 10 km), joined tags 'hi-hu', notes in restriction cells (Jalur),
  validate_profile.py checks pair lengths against the timetable (fixed Gundih-Gambringan, Surabaya Kota-Gubeng).
- Gradient from sheet elevations: fixes the Sukabumi-line bias but is worse network-wide; elevations are crude. Off.
- KRL halts: one dwell per halt does not fit (Pasarminggu-Manggarai gets ~the same time with or without halts:
  path slots); per-halt-station dwell is learned instead.
