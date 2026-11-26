// Build the website (web/): bundle the app and the engine worker, copy the data package.
//   node tools/build_web.mjs [edition folder, default dist/gapeka-2026]   (npm run web)
import { build } from "esbuild";
import { copyFileSync, mkdirSync } from "node:fs";
import { join, dirname, basename } from "node:path";
import { fileURLToPath } from "node:url";

const root = join(dirname(fileURLToPath(import.meta.url)), "..", "..");
const web = join(root, "web");
const edition = process.argv[2] ?? join(root, "dist", "gapeka-2026");
const common = { bundle: true, format: "esm", target: "es2022", minify: true, sourcemap: true, logLevel: "info" };
await build({ ...common, entryPoints: [join(web, "src", "main.ts")], outfile: join(web, "build", "app.js") });
await build({ ...common, entryPoints: [join(root, "engine", "src", "worker.ts")], outfile: join(web, "build", "worker.js") });
const out = join(web, "data", basename(edition));
mkdirSync(out, { recursive: true });
for (const f of ["package.json.gz", "manifest.json"]) copyFileSync(join(edition, f), join(out, f));
console.log("web/ ready:", out);
