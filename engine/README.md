# GAPEKA simulator engine (browser)

TypeScript port of the Python reference engine (`physics_ref.py`, `replay.py`, `planner.py`). It runs in a Web Worker on
the simulator package `dist/<edition>/package.json.gz` and gives **the same numbers as Python, to the last bit**.

```
npm ci                # once
npm test              # build + tests (golden replay, leg times, re-planning test, dispatch, session)
npm run bundle        # build/worker.js (one ES module, ~37 KB minified)
npm run smoke         # bundled worker in headless Chromium vs the dispatch golden (needs playwright)
npm run fixtures      # optional: full-precision leg times from Python (build/fixtures/legs.json)
```

## Files

| File | Port of | What |
|---|---|---|
| `src/py.ts` | | CPython float semantics: `sum()` (Neumaier), `round()` half to even, `round(x, n)` |
| `src/physics.ts` | `physics_ref.py` | speed cap per 50 m cell, station zones, acceleration / braking run |
| `src/replay.ts` | `replay.py` | `Engine.compose` (running time of a leg), `Engine.minimum`, golden replay |
| `src/planner.ts` | `planner.py` | `Timeline`, `Planner` (rules), `dispatch` (dispatching re-plan), `replanTest`, `capacityWarnings`, `explain` |
| `src/session.ts` | | a scenario: edit / create / apply / reset (v1 edits) |
| `src/worker.ts` | | Web Worker message protocol around `Session` |

## Worker protocol

Post `{ id, op, ...args }`, receive `{ id, ok: true, result }` or `{ id, ok: false, error }`.

| op | args | result |
|---|---|---|
| `load` | `url` or `data` (ArrayBuffer, gzip or plain JSON) | `{ edition, trains, stations }` |
| `edit` | `train` (number), `edit`, `lang` (`id` / `en`) | `PlanResult`; the scenario is unchanged until `apply` |
| `create` | `train` (`NewTrain`), `lang` | `PlanResult` for a train outside the schedule (it never moves a scheduled train) |
| `apply` | `result` | the result becomes part of the scenario (later edits start from it) |
| `reset` | | back to GAPEKA |
| `warnings` | | sections over capacity K |
| `route` | `a`, `b` | station codes from a to b (halts included), shortest km |
| `trains` | | the scenario's trains |

`edit` (v1, user decision): `depart` (s) or `late` (min), `dwell` {row: s}, `stops` {row: "stop" / "pass"}, `vmax`
(km/h, all sections or {row: km/h}), `type` (krl / commuter / intercity / freight), `cars`. Departure and dwell edits
keep the printed running times; stops, vmax and type re-time the train by the physics.

`PlanResult`: `trains` [{no, rows [station, kind, arr s, dep s, vmax, origin], waits [station, other, kind, s],
late_s}], `info` (held trains, why each joined, rounds, others' total delay), `texts` (sentences from the package texts
in the chosen language).

## Exactness rules (keep them when changing either side)

- Squares as `v * v` on both sides (`pow` differs in the last bit).
- Sums of floats with `pySum` where Python uses `sum()`; sequential `+=` where Python adds in a loop.
- `round()` is half to even (`pyRoundInt`, `pyRound`); `%` is floor modulo (`pyMod`).
- Maps for Python dicts (insertion order), the tuple order `cmp` for sorted index lists, the same heap algorithm as
  `heapq` (equal-cost routes), first element wins in min / max.
- Python iterates sets only where the order does not matter (held trains are taken in priority order).
- The goldens (`dist/<edition>/replay.json.gz`, `replan.json.gz`, `dispatch.json.gz`) are written by the pipeline and
  checked by `check.py`; `npm test` compares the engine with them.
