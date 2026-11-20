# Gapeka Parser

Tools used by **Transport for Bandung (TfB)** to turn KAI's official train timetable, the
**GAPEKA** (*Grafik Perjalanan Kereta Api*), into the rail timetables published on the
**Bandung Basin Metropolitan Area Transit Map** and on TfB's social media posts.

GAPEKA is released as one very large PDF, with thousands of pages of timetables and train-graph
sheets for all of Java. This repository extracts that PDF into a SQLite database. The database
is then used to produce ready-to-edit timetable graphics for the Bandung services:

- **Commuter Line Bandung Raya**: Padalarang – Bandung – Cicalengka, one trip to Purwakarta (trains 390–431)
- **Commuter Line Garut**: Purwakarta / Padalarang – Bandung – Cibatu / Garut (trains 386–389, 412, 423)
- **Siliwangi**: Sukabumi – Cipatat (trains 351–356)

The outputs are vector SVG files that open directly in Illustrator, with live text and named
layers, plus CSV files for checking and further editing.

## How it works

```
GAPEKA-JAWA-2026.pdf
        │  gapeka_pipeline.py
        ▼
    gapeka.db  ──►  export_poster.py   → timetable tables (SVG) for the map and posts
   (SQLite)    ──►  export_headway.py  → station time bar with headway colours (SVG)
               ──►  export_trains.py   → station × train matrix (CSV)
               ──►  sanity_check.py    → list of suspicious times (CSV)
```

`gapeka_pipeline.py` does the heavy lifting:

1. **Timetable pages**: reads every train's stop list: station, speed limit, arrival and departure,
   and remarks (keterangan).
2. **Station distances**: reads the distance and cumulative-km columns on the graph sheets.
3. **Halt times**: the GAPEKA prints no time for halts (*Berh* / *Sb*), so the pipeline estimates them
   from running times and distances. For the Bandung halts, the estimate is replaced by the times
   KAI uses for ticketing, stored in `halt_offsets.csv` (see below).
4. **Crossing and overtaking notes**: links notes such as `x ka 391` to the other train.
5. **Train graph**: extracts the drawn train lines from the graph sheets.

## Requirements

- Python 3.9 or newer
- `pip install pymupdf numpy`. Only the pipeline needs these; the export scripts use the standard library only.

## Usage

### 1. Build the database

```
python gapeka_pipeline.py gapeka.db GAPEKA-JAWA-2026.pdf
```

This takes about 6–7 minutes for the full Java GAPEKA. You can give several PDFs or a folder.
`--dwell 30` sets the dwell time at halts in seconds, and `--no-graph` skips the graph sheets
(faster, but no distances).

The run prints a report. Check the `halt_offsets.csv` section: it shows how far the model is
from the KAI times, and warns about rules that were skipped or never used.

### 2. Export timetable graphics

```
# Cicalengka-bound and Padalarang-bound tables for Commuter Line Bandung Raya + Garut
python export_poster.py 386-431 --even -o cl_bandung_even.svg
python export_poster.py 386-431 --odd  -o cl_bandung_odd.svg

# Siliwangi
python export_poster.py 351-356 --odd  -o siliwangi_odd.svg
```

Colours, cell sizes and fonts follow the TfB timetable design. Run the script without arguments to
see every option: `--exclude`, `--stations`, `--bold`, `--highlight`, `--sort`, `--scale` and
`--fit-width`.

### 3. Station headway bar

```
python export_headway.py BD --service "Commuter Line Bandung Raya,Commuter Line Garut"
python export_headway.py BD,KAC,CCL 386-431 --even -o headway_even.svg
```

The bar is a time axis with one line per train. The space between two trains is coloured from green
(short headway) to red (long headway).

### 4. CSV matrix and checks

```
python export_trains.py 386-431 --odd -o trains_odd.csv
python sanity_check.py gapeka.db
```

## Halt times and `halt_offsets.csv`

The GAPEKA gives no time for halt stations such as Gadobangkong, Cikudapateuh, Cimekar, Haurpugur
and Leuwigoong. The model's estimates come within about 1–2 minutes of KAI's published times but
cannot match them exactly, because KAI's times depend on the direction of travel and differ for
some trains. For example, the Garut trains often need one minute more than Bandung Raya trains.

So for the TfB services, the halt time is set by a rule in `halt_offsets.csv`. The current values come
from KAI Commuter's *Jadwal Commuter Line Wilayah 2 Bandung* (update 1 December 2026):

```
# halt_code,from_code,minutes[,trains]
GK,PDL,5             # Gadobangkong = departure from Padalarang + 5 min
GK,PDL,6,386 388     # ... but + 6 min for trains 386 and 388
```

When a new GAPEKA or KAI schedule is released, update these rules. If a Bandung halt has no
rule, the pipeline lists it at the end of step 3.

In the outputs, halt times are shown as one time, the arrival, in whole minutes, the same way the
KAI ticket shows them. In the CSV they are marked with `*`.

## Files

| File | Purpose |
|---|---|
| `gapeka_pipeline.py` | GAPEKA PDF → `gapeka.db` |
| `export_poster.py` | Timetable table SVG for the transit map and posts |
| `export_headway.py` | Station time bar with headway colours (SVG) |
| `export_trains.py` | Station × train matrix (CSV) |
| `sanity_check.py` | Flags times going backwards, odd running times, long dwells → `sanity_flags.csv` |
| `halt_offsets.csv` | KAI halt times for the Bandung halts (manual input) |
| `manual_links.csv` | *(optional)* station distances to add or override, `code,code,km` |
| `gapeka_missing_links.csv` | Station pairs still without a distance (written by the pipeline) |
| `gapeka.db` | The parsed database |
| `*.svg` | Latest exported timetable graphics |

### Main database tables

- `trains`: train number, service, route
- `stops`: every row of each train's timetable (`kind`: stop / halt / pass / origin_or_end)
- `schedule`: view of `stops` with the estimated halt times filled in. **Use this one for exports.**
- `stations`, `station_links`: station codes, names and distances
- `halt_est`, `halt_fit`: halt time estimates and the fitted running-time models
- `constraints`: crossings and overtakes from the remarks

## Notes

- The GAPEKA is published by PT Kereta Api Indonesia. Times in the database are the planned schedule
  and may change with service announcements. Always check against KAI's current schedule before
  publishing.
- The parser is tuned to the layout of the GAPEKA 2026 PDF. A future edition with a different
  layout may need the column positions in `gapeka_pipeline.py` (`COLS`) adjusted.
