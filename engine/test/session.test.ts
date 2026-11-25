// Session (scenario) checks: an edit that only delays a train equals planner.dispatch (golden), apply / reset, a
// created train never moves a scheduled train
import { test } from "node:test";
import assert from "node:assert/strict";
import { loadGz } from "../src/load.js";
import { Session } from "../src/session.js";
import { diff } from "./util.js";

const pkg = loadGz("dist/gapeka-2026/package.json.gz");
const golden = loadGz("dist/gapeka-2026/dispatch.json.gz").cases;

test("edit (late) = dispatch golden; apply and reset", () => {
  const s = new Session(pkg);
  for (const c of golden.slice(0, 3)) {
    const r = s.edit(c.train, { late: c.late }, "en");
    const got = Object.fromEntries(r.trains.map((t) => [t.no, [t.rows, t.waits]]));
    assert.equal(diff(got, c.plans), null, `${c.train} +${c.late}`);
    console.log(`edit ${c.train} +${c.late} min: ${r.trains.length} trains; ${r.texts.slice(0, 2).join(" | ")}`);
  }
  const r = s.edit("7", { late: 15 });
  s.apply(r);
  const t7 = s.train("7")!;
  assert.equal(t7.stops[0][3], pkg.trains.find((t: { no: string }) => t.no === "7").stops[0][3] + 900);
  assert.equal(t7.stops[0][5], "simulated");
  s.reset();
  assert.equal(s.train("7")!.stops[0][5], "printed");
});

test("edit stops / vmax / type re-times by physics", () => {
  const s = new Session(pkg);
  const t = s.train("121")!;
  const i = t.stops.findIndex((r, n) => n > 0 && n < t.stops.length - 1 && r[1] === "stop");
  const r = s.edit("121", { stops: { [i]: "pass" }, vmax: 100 });
  assert.equal(r.info.physics, true);
  const mine = r.trains.find((x) => x.no === "121")!;
  assert.equal(mine.rows[i][1], "pass");
  console.log(`121 without stop ${t.stops[i][0]}, vmax 100: ends ${Math.round((mine.late_s ?? 0) / 60)} min vs GAPEKA`);
});

test("created train waits for scheduled trains and moves none", () => {
  const s = new Session(pkg);
  const route = s.route("BD", "CCL")!;
  assert.ok(route && route.length > 2, "route Bandung - Cicalengka");
  const res = s.create({ no: "X1", type: "commuter", route, depart: 8 * 3600, stops: { KAC: 60 }, vmax: 80, cars: 8 },
    "en");
  assert.equal(res.trains.length, 1);
  const rows = res.trains[0].rows;
  assert.ok(rows[rows.length - 1][2]! > 8 * 3600);
  console.log(`X1 ${route.join("-")}: arrives ${(rows[rows.length - 1][2]! / 3600).toFixed(2)} h, waits ` +
    JSON.stringify(res.trains[0].waits));
  s.apply(res);
  assert.ok(s.tl.trains.has("X1"));
});
