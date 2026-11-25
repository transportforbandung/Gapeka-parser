// compose / minimum at full precision against the Python engine (fixtures from tools/py_fixtures.py)
import { test } from "node:test";
import assert from "node:assert/strict";
import { existsSync, readFileSync } from "node:fs";
import { join } from "node:path";
import { loadGz, repoRoot } from "../src/load.js";
import { Engine, type Train } from "../src/replay.js";

const fx = join(repoRoot, "engine/build/fixtures/legs.json");

test("compose and minimum equal Python to the last bit", { skip: !existsSync(fx) && "no fixtures" }, () => {
  const pkg = loadGz("dist/gapeka-2026/package.json.gz");
  const T = new Map<string, Train>((pkg.trains as Train[]).map((t) => [t.no, t]));
  const eng = new Engine(pkg);
  const rowsData = JSON.parse(readFileSync(fx, "utf8")) as [string, number, number, string | null, number | null,
    number | null][];
  let bad = 0;
  for (const [no, p, q, alt, c, m] of rowsData) {
    const t = T.get(no)!;
    let rows = t.stops;
    if (alt === "passes") {
      rows = rows.map((r, i) => (i > 0 && i < rows.length - 1 && r[1] === "stop" ? [r[0], "pass", ...r.slice(2)] : r) as
        typeof r);
    }
    const c2 = eng.compose(t, rows, p, q), m2 = eng.minimum(t, rows, p, q);
    if (c2 !== c || m2 !== m) {
      if (bad < 5) console.log("differs", no, p, q, alt, c, c2, m, m2);
      bad++;
    }
  }
  console.log(`legs: ${rowsData.length} compared, ${bad} differ`);
  assert.equal(bad, 0);
});
