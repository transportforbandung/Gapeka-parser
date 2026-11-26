// Phase 5 check 2: the TypeScript planner equals the Python planner (golden files written by planner_golden.py next to
// the package): the re-planning test train by train (rows, waits, stats) and the dispatching re-plan of the delay cases
import { test } from "node:test";
import assert from "node:assert/strict";
import { loadGz } from "../src/load.js";
import { dispatch, Planner, replanTest, Timeline } from "../src/planner.js";
import type { Train } from "../src/replay.js";
import { diff } from "./util.js";

const pkg = loadGz("dist/gapeka-2026/package.json.gz");

test("re-planning test equals Python train by train", () => {
  const want = loadGz("dist/gapeka-2026/replan.json.gz");
  const got: Record<string, unknown> = {};
  const t0 = Date.now();
  const stats = replanTest(pkg, want.types, false, (no, rows, waits) => {
    got[no] = [rows, waits];
  });
  const ms = Date.now() - t0;
  const bad = Object.keys(want.trains).filter((no) => diff(got[no], want.trains[no]) !== null);
  console.log(`replan: ${Object.keys(got).length} trains in ${ms} ms, ${bad.length} differ` +
    (bad.length ? `, e.g. ${bad[0]} ${diff(got[bad[0]], want.trains[bad[0]])}` : ""));
  for (const [typ, s] of Object.entries(stats)) {
    console.log(`  ${typ}: same station ${(s.same_station * 100).toFixed(1)} %, end within 2 min ` +
      `${(s.end_within_2min * 100).toFixed(1)} %`);
  }
  assert.equal(bad.length, 0);
  assert.equal(diff(stats, want.stats), null);
});

test("dispatching re-plan equals Python", () => {
  const cases = loadGz("dist/gapeka-2026/dispatch.json.gz").cases;
  const tl = new Timeline(pkg);
  const P = new Planner(pkg, tl);
  const T = new Map<string, Train>((pkg.trains as Train[]).map((t) => [t.no, t]));
  let bad = 0;
  for (const c of cases) {
    const t = T.get(c.train)!;
    const t0 = Date.now();
    const [plans, info] = dispatch(pkg, tl, P, t, t.stops[0][3]! + c.late * 60, true, t);
    const ms = Date.now() - t0;
    const got = Object.fromEntries([...plans].map(([n, [r, w]]) => [n, [r, w]]));
    const d = diff(got, c.plans) ?? diff(info, c.info);
    console.log(`dispatch ${c.train} +${c.late} min: ${plans.size} trains, ${ms} ms ${d ? "DIFFERS " + d : "same"}`);
    if (d) bad++;
  }
  assert.equal(bad, 0);
});
