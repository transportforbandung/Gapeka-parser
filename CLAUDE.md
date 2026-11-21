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
