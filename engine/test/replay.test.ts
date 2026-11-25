// Phase 5 check 1: the TypeScript engine reproduces the golden replay (replay.json.gz) exactly
import { test } from "node:test";
import assert from "node:assert/strict";
import { loadGz } from "../src/load.js";
import { replay } from "../src/replay.js";

test("golden replay reproduced exactly", () => {
  const pkg = loadGz("dist/gapeka-2026/package.json.gz");
  const gold = loadGz("dist/gapeka-2026/replay.json.gz");
  const t0 = Date.now();
  const out = replay(pkg);
  const ms = Date.now() - t0;
  const want = gold.trains as Record<string, unknown[]>;
  const bad: string[] = [];
  for (const no of new Set([...Object.keys(want), ...Object.keys(out)])) {
    if (JSON.stringify(out[no]) !== JSON.stringify(want[no])) bad.push(no);
  }
  console.log(`replay: ${Object.keys(out).length} trains in ${ms} ms, ${bad.length} differ`);
  if (bad.length) {
    const no = bad[0];
    console.log(no, JSON.stringify(out[no]).slice(0, 400), "\n", JSON.stringify(want[no]).slice(0, 400));
  }
  assert.equal(bad.length, 0);
});
