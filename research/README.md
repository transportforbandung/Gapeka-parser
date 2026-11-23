# Research scripts

One-off experiments behind the model now in the pipeline (`gapeka_model.py`, `build_infra.py`, `build_rules.py`).
They are kept so every finding in `CLAUDE.md` can be traced back and re-run. They are **not** part of the
pipeline. Most expect the repository at `/home/user/Gapeka-parser` and write their outputs to the working
directory; `physics_model.py` reads its line profile from `infra.db` (set `PROFILE=json` for the old
`line_profile.json`).

## Still useful

| Script | What it shows |
|---|---|
| `relearn.py` | The research version of the running-time model: physics + calibration per train type + ridge corrections; prints held-out error bands and diagnostics (moved into `gapeka_model.py`) |
| `physics_model.py` | Speed-profile simulation used by the scripts below |
| `jabo_halts.py` | KAI Jabodetabek halt times vs the model: split rule, dwell, per-halt offsets |
| `halts_latest.py` | KAI Bandung / Surabaya halt times vs the model (split vs front-loaded per region) |
| `kai_jabo_extract.py` | KAI Jabodetabek / Nambo / Basoetta timetables -> `reference/kai_krl_jabodetabek_2026.csv` |
| `kai_extract.py`, `match.py`, `halts.py`, `graph_probe.py` | KAI Bandung / Surabaya timetables -> halt reference data |
| `waits11.py`, `wait_fix*.py`, `wait_errors.py`, `waits_ctc.py` | Departure of trains waiting for a crossing / overtake (block-point rule, gaps per line, CTC) |
| `fit_slow.py` | Slow-zone rules (terminus time, buffers) fitted from residuals |
| `grade_check.py` | Steep sections: extra planning time rather than lower speed limits |
| `ctc.py` | Stations in dashed boxes (CTC) |
| `freight_grid.py` | Freight acceleration / braking grid (no effect: freight error is pathing) |

## Superseded (history of the investigation)

| Script | Superseded by |
|---|---|
| `line_profile.py`, `validate_profile.py` | `build_infra.py` |
| `runtime_model*.py`, `physics_fit2.py`, `physics_bands*.py`, `hybrid*.py` | `relearn.py` / `gapeka_model.py` |
| `siding*.py`, `points.py`, `kac*.py` | station zones in `rules/station_zones.csv` |
| `halts_points.py`, `halts_final.py`, `halts_sby.py`, `physics_halts.py`, `evaluate.py` | `halts_latest.py`, `jabo_halts.py` |
| `waits.py` .. `waits10.py` | `waits11.py`, `wait_fix*.py` |
| `diag_*.py`, `errors.py`, `scan*.py` | diagnostics for single questions (Yogyakarta, East Java, KRL, notes, ...) |

`rules/rules.json`, `station_aliases.csv` and `ctc_stations.json` are research copies; the pipeline uses `rules/`
and `manual/` in the repository root.
