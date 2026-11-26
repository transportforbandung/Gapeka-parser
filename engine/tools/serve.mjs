// Static server for the website during development: node tools/serve.mjs [port]  -> http://localhost:8080/
import http from "node:http";
import { readFileSync, statSync } from "node:fs";
import { join, extname, dirname, normalize } from "node:path";
import { fileURLToPath } from "node:url";

const web = join(dirname(fileURLToPath(import.meta.url)), "..", "..", "web");
const types = { ".js": "text/javascript", ".html": "text/html; charset=utf-8", ".css": "text/css", ".gz": "application/gzip",
  ".json": "application/json", ".map": "application/json" };
export function serve(port = 8080) {
  const server = http.createServer((req, res) => {
    let p = normalize(join(web, decodeURIComponent(req.url.split("?")[0])));
    if (!p.startsWith(web)) { res.writeHead(403); return res.end(); }
    try { if (statSync(p).isDirectory()) p = join(p, "index.html"); } catch { /* 404 below */ }
    try {
      const b = readFileSync(p);
      res.writeHead(200, { "content-type": types[extname(p)] ?? "application/octet-stream", "cache-control": "no-store" });
      res.end(b);
    } catch { res.writeHead(404); res.end("not found"); }
  });
  return new Promise((r) => server.listen(port, () => r(server)));
}
if (import.meta.url === `file://${process.argv[1]}`) {
  const port = Number(process.argv[2] ?? 8080);
  await serve(port);
  console.log(`http://localhost:${port}/`);
}
