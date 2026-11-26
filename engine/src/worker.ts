// Web Worker entry of the simulator engine. Messages: { id, op, ...args } -> { id, ok: true, result } or
// { id, ok: false, error }. Ops:
//   load     { url } or { data: ArrayBuffer (package.json.gz) }    -> { edition, trains, stations }
//   edit     { train, edit: Edit, lang }                            -> PlanResult (scenario unchanged)
//   create   { train: NewTrain, lang }                              -> PlanResult (scenario unchanged)
//   apply    { result: PlanResult }                                  -> null (result becomes part of the scenario)
//   reset    {}                                                      -> null (back to GAPEKA)
//   warnings {}                                                      -> sections over capacity K
//   route    { a, b }                                                -> station codes (halts included) or null
//   trains   {}                                                      -> the scenario's trains (no, name, type, stops)
//   timeline {}                                                      -> {train: rows [station, kind, arr, dep]} all timed
import { Session } from "./session.js";

declare const self: { onmessage: ((ev: { data: Req }) => void) | null; postMessage(m: unknown): void };
interface Req { id: number; op: string; [k: string]: unknown }

let session: Session | null = null;

async function gunzipJson(buf: ArrayBuffer): Promise<unknown> {
  const head = new Uint8Array(buf, 0, 2);
  if (head[0] !== 0x1f || head[1] !== 0x8b) return JSON.parse(new TextDecoder().decode(buf));  // served decompressed
  const ds = new Response(new Blob([buf]).stream().pipeThrough(new DecompressionStream("gzip")));
  return JSON.parse(await ds.text());
}

async function handle(m: Req): Promise<unknown> {
  if (m.op === "load") {
    const buf = m.data instanceof ArrayBuffer ? m.data : await (await fetch(String(m.url))).arrayBuffer();
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const pkg = (await gunzipJson(buf)) as any;
    session = new Session(pkg);
    return { edition: pkg.meta.edition, trains: pkg.trains.length, stations: Object.keys(pkg.stations).length };
  }
  if (!session) throw new Error("load a package first");
  switch (m.op) {
    case "edit": return session.edit(String(m.train), (m.edit ?? {}) as object, (m.lang as "id" | "en") ?? "id");
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    case "create": return session.create(m.train as any, (m.lang as "id" | "en") ?? "id");
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    case "apply": session.apply(m.result as any); return null;
    case "reset": session.reset(); return null;
    case "warnings": return session.warnings();
    case "timeline": return session.timeline();
    case "route": return session.route(String(m.a), String(m.b));
    case "trains": return session.pkg.trains.map((t: { no: string; name?: string; type: string; stops: unknown[] }) =>
      ({ no: t.no, name: t.name, type: t.type, stops: t.stops }));
    default: throw new Error(`unknown op ${m.op}`);
  }
}

self.onmessage = (ev) => {
  const m = ev.data;
  handle(m).then((result) => self.postMessage({ id: m.id, ok: true, result }),
    (err) => self.postMessage({ id: m.id, ok: false, error: String(err && err.stack || err) }));
};
