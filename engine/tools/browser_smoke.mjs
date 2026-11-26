// Browser smoke test: the bundled worker (build/worker.js) in headless Chromium loads the package, re-plans
// delay cases and must equal the dispatch golden. node tools/browser_smoke.mjs  (needs: npm run bundle, playwright)
import http from "node:http";
import { readFileSync } from "node:fs";
import { gunzipSync } from "node:zlib";
import { join, extname, dirname } from "node:path";
import { fileURLToPath } from "node:url";
import { createRequire } from "node:module";

const root = join(dirname(fileURLToPath(import.meta.url)), "..", "..");
const require = createRequire(import.meta.url);
const { chromium } = require("playwright");
const types = { ".js": "text/javascript", ".html": "text/html", ".gz": "application/gzip", ".json": "application/json" };
const server = http.createServer((req, res) => {
  const p = join(root, decodeURIComponent(req.url.split("?")[0]));
  if (req.url === "/") {
    res.writeHead(200, { "content-type": "text/html" });
    return res.end("<!doctype html><title>engine smoke</title>");
  }
  try {
    const b = readFileSync(p);
    res.writeHead(200, { "content-type": types[extname(p)] ?? "application/octet-stream" });
    res.end(b);
  } catch { res.writeHead(404); res.end(); }
});
await new Promise((r) => server.listen(0, r));
const port = server.address().port;
const golden = JSON.parse(gunzipSync(readFileSync(join(root, "dist/gapeka-2026/dispatch.json.gz"))).toString()).cases;
const browser = await chromium.launch();
let failed = 0;
try {
  const page = await browser.newPage();
  page.on("console", (m) => console.log("  [page]", m.text()));
  await page.goto(`http://localhost:${port}/`);
  const out = await page.evaluate(async (cases) => {
    const w = new Worker("/engine/build/worker.js", { type: "module" });
    let n = 0;
    const call = (op, args = {}) => new Promise((res, rej) => {
      const id = ++n;
      const h = (ev) => { if (ev.data.id !== id) return; w.removeEventListener("message", h);
        ev.data.ok ? res(ev.data.result) : rej(new Error(ev.data.error)); };
      w.addEventListener("message", h);
      w.postMessage({ id, op, ...args });
    });
    const t0 = performance.now();
    const meta = await call("load", { url: "/dist/gapeka-2026/package.json.gz" });
    const loadMs = performance.now() - t0;
    const res = [];
    for (const c of cases) {
      const t1 = performance.now();
      const r = await call("edit", { train: c.train, edit: { late: c.late }, lang: "id" });
      res.push({ train: c.train, late: c.late, ms: performance.now() - t1, plans: Object.fromEntries(r.trains.map((t) => [t.no, [t.rows, t.waits]])), text: r.texts[0] });
    }
    return { meta, loadMs, res };
  }, golden);
  console.log(`package ${out.meta.edition}: ${out.meta.trains} trains, loaded in the worker in ${out.loadMs.toFixed(0)} ms`);
  for (const r of out.res) {
    const want = golden.find((c) => c.train === r.train && c.late === r.late).plans;
    const same = JSON.stringify(sortKeys(r.plans)) === JSON.stringify(sortKeys(want));
    if (!same) failed++;
    console.log(`  ${r.train} +${r.late} min: ${Object.keys(r.plans).length} trains, ${r.ms.toFixed(0)} ms, ${same ? "same" : "DIFFERS"}` + (r.text ? ` - ${r.text}` : ""));
  }
} finally {
  await browser.close();
  server.close();
}
function sortKeys(o) { return Object.fromEntries(Object.keys(o).sort().map((k) => [k, o[k]])); }
console.log(failed ? `${failed} case(s) differ` : "browser smoke test passed");
process.exit(failed ? 1 : 0);
