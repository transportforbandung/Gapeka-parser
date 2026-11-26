# GAPEKA simulator website

Static site: a GAPEKA-style graph per line (time across, stations down, halts dashed, train numbers on the lines,
freight dashed, facultative trains with cross marks, KA Dinas dotted) drawn on a canvas, with the simulator engine
(`engine/`, same results as the Python planner) in a Web Worker. Unofficial: no KAI or Kemenhub logos; publisher
Transport for Bandung; Indonesian and English (texts from `texts/ui.csv`, keys `web.*`).

```
cd engine
npm ci
npm run web        # type-check, bundle web/build/app.js + worker.js, copy dist/gapeka-2026 to web/data/
npm run serve      # http://localhost:8080/
```

`web/build/` and `web/data/` are build output (not in git).

Published by GitHub Actions (`.github/workflows/pages.yml`): on every push to `main` that touches `web/`, `engine/` or
`dist/` (or by hand: Actions > Publish website > Run workflow) it runs the engine tests (equal to the Python goldens),
builds the site, and deploys `index.html`, `style.css`, `build/` and `data/` to GitHub Pages
(https://transportforbandung.github.io/Gapeka-parser/). One-time setting: repository Settings > Pages > Build and
deployment > Source: **GitHub Actions**. A new edition is published by committing its `dist/<edition>/` package. Host the `web/` folder on any static server (the package
is fetched as `data/gapeka-2026/package.json.gz` and unpacked in the browser).

What a user can do:
- pick a graph: each margin table of a sheet group is its own graph (package lines[].tables; e.g. Cikampek - Garut =
  Cikampek - Banjar, Andir - Ciroyom - Bandung, Cibatu - Garut), grouped by sheet group in the menu; drag to pan, wheel to zoom time (shift / ctrl + wheel: stations), find a
  train by number
- click a train line: its timetable (printed times in black, model estimates in grey) and the v1 edits: late at the
  origin, vmax, type, cars, stop / pass per station, dwell
- Simulate: the dispatching re-plan; the changed trains are drawn in orange over the dimmed GAPEKA lines, with the
  explanation (who waits where for whom, who is held) and each affected train's arrival change; Apply keeps it in the
  scenario, Discard drops it
- New train: from / to (shortest route), departure, type, vmax, cars, stops; it yields to every scheduled train
- Scenario: the applied changes, capacity warnings, back to GAPEKA
