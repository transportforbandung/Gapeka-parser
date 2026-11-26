// GAPEKA simulator website: a GAPEKA-style graph (time across, stations down) per line, drawn on a canvas, with the
// simulator engine (engine/src/worker.ts) in a Web Worker. Unofficial: no KAI / Kemenhub logos.
/* eslint-disable @typescript-eslint/no-explicit-any */

type TRow = [string, string, number, number];
type Row = [string, string, number | null, number | null, number | null, string];
interface PlanTrain { no: string; rows: Row[]; waits: [string, string | null, string, number][]; late_s: number | null;
  filled: TRow[] }
interface PlanResult { trains: PlanTrain[]; info: any; texts: string[] }

const DAY = 86400;
const PKG_URL = "data/gapeka-2026/package.json.gz";
const TYPE_COLOR: Record<string, string> = { intercity: "#b3261e", commuter: "#1f5fb4", krl: "#2e7d32",
  freight: "#6d4c41", dinas: "#7a7a7a" };

// ------------------------------------------------------------------------------------------------ worker
const worker = new Worker(new URL("./worker.js", import.meta.url), { type: "module" });
let callId = 0;
const pending = new Map<number, [(v: any) => void, (e: Error) => void]>();
worker.onmessage = (ev) => {
  const p = pending.get(ev.data.id);
  if (!p) return;
  pending.delete(ev.data.id);
  ev.data.ok ? p[0](ev.data.result) : p[1](new Error(ev.data.error));
};
function call<T = any>(op: string, args: Record<string, unknown> = {}): Promise<T> {
  return new Promise((res, rej) => {
    const id = ++callId;
    pending.set(id, [res, rej]);
    worker.postMessage({ id, op, ...args });
  });
}

// ------------------------------------------------------------------------------------------------ state
const $ = <T extends HTMLElement = HTMLElement>(id: string) => document.getElementById(id) as T;
let pkg: any = null;
let lang: "id" | "en" = "id";
try { if (localStorage.getItem("gapeka-lang") === "en") lang = "en"; } catch { /* storage blocked */ }
let timeline: Record<string, TRow[]> = {};
let trainsBy = new Map<string, any>();          // package trains (scenario versions after apply)
let lineId = "";                                // graph id: line id + ":" + table index
interface Graph { id: string; line: any; codes: string[] }
let graphs: Graph[] = [];
let geom: Geom | null = null;
let paths = new Map<string, Path[]>();          // train -> polylines on the current line
let preview: PlanResult | null = null;
let previewPaths = new Map<string, Path[]>();
let selected: string | null = null;
let applied: { no: string; label: string; changed: string[] }[] = [];
let tab: "train" | "new" | "scenario" = "train";
const view = { t0: 5 * 3600, t1: 11 * 3600, y0: 0, y1: 1 };

function t(key: string, vals: Record<string, string | number> = {}): string {
  const e = pkg?.texts?.[key];
  const s = e ? (e[lang] || e.id) : key;
  return s.replace(/\{(\w+)\}/g, (m: string, k: string) => (k in vals ? String(vals[k]) : m));
}
function stName(c: string): string {
  const s = pkg?.stations?.[c];
  return (s && (s.display_name || s.name)) || c.replace(/^\?/, "");
}
function hhmm(s: number | null | undefined, sec = false): string {
  if (s === null || s === undefined) return "";
  const d = Math.floor(s / DAY);
  let x = ((s % DAY) + DAY) % DAY;
  x = sec ? x : Math.round(x / 60) * 60;
  if (x >= DAY) x -= DAY;
  const h = Math.floor(x / 3600), m = Math.floor((x % 3600) / 60);
  return `${String(h).padStart(2, "0")}:${String(m).padStart(2, "0")}${d > 0 ? "⁺¹" : ""}`;
}
function esc(s: string): string {
  return s.replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c]!);
}

// ------------------------------------------------------------------------------------------------ line geometry
interface GRow { code: string; y: number; seg: number; idx: number; kind: string }
interface Geom { rows: GRow[]; segs: { y0: number; y1: number }[]; pos: Map<string, GRow[]>; height: number }
interface Path { pts: [number, number][]; type: string; fac: boolean }

let pairKm = new Map<string, number>();
function buildPairKm(): void {
  pairKm = new Map();
  for (const r of pkg.profiles as any[]) {
    const k = r[0] + ">" + r[1];
    if (!r[2] || !pairKm.has(k)) { if (!r[2]) pairKm.set(k, r[4]); else if (!pairKm.has(k)) pairKm.set(k, r[4]); }
  }
}

function buildGeom(codes: string[]): Geom {
  const rows: GRow[] = [];
  const segs: { y0: number; y1: number }[] = [];
  let y = 0, seg = -1, prev: string | null = null, idx = 0;
  const gap = 4;
  for (const code of codes) {
    const d = prev ? (pairKm.get(prev + ">" + code) ?? pairKm.get(code + ">" + prev)) : undefined;
    if (prev === null || d === undefined) {
      if (seg >= 0) { segs[seg].y1 = y; y += gap; }
      seg++;
      segs.push({ y0: y, y1: y });
      idx = 0;
    } else {
      y += Math.max(d, 0.25);
      idx++;
    }
    rows.push({ code, y, seg, idx, kind: pkg.stations[code]?.kind ?? "sheet_only" });
    prev = code;
  }
  if (seg >= 0) segs[seg].y1 = y;
  const pos = new Map<string, GRow[]>();
  for (const r of rows) { if (!pos.has(r.code)) pos.set(r.code, []); pos.get(r.code)!.push(r); }
  return { rows, segs, pos, height: y };
}

/** a train's rows -> polylines (time, y) on the current line */
function trainPaths(no: string, rows: TRow[]): Path[] {
  if (!geom) return [];
  const tr = trainsBy.get(no);
  const type = tr?.type ?? "intercity", fac = !!tr?.facultative;
  const out: Path[] = [];
  let cur: [number, number][] | null = null;
  let last: GRow | null = null;
  for (let i = 0; i < rows.length; i++) {
    const r = rows[i];
    const cands = geom.pos.get(r[0]);
    let here: GRow | null = null;
    if (cands && last) {
      let best = 99;
      for (const c of cands) {
        const dd = Math.abs(c.idx - last.idx);
        if (c.seg === last.seg && dd >= 1 && dd <= 4 && dd < best) { best = dd; here = c; }
      }
    }
    if (here && cur) {
      cur.push([r[2], here.y]);
      if (r[3] !== r[2]) cur.push([r[3], here.y]);
      last = here;
      continue;
    }
    if (cur && cur.length > 1) out.push({ pts: cur, type, fac });
    cur = null;
    last = null;
    if (cands && cands.length) {
      // start a new polyline at the candidate that continues to the next row
      const nx = rows[i + 1] ? geom.pos.get(rows[i + 1][0]) : undefined;
      let start = cands[0];
      if (nx) for (const c of cands) if (nx.some((n) => n.seg === c.seg && Math.abs(n.idx - c.idx) <= 4)) { start = c; break; }
      cur = [[r[2], start.y]];
      if (r[3] !== r[2]) cur.push([r[3], start.y]);
      last = start;
    }
  }
  if (cur && cur.length > 1) out.push({ pts: cur, type, fac });
  return out;
}

function rebuildPaths(): void {
  paths = new Map();
  for (const [no, rows] of Object.entries(timeline)) {
    const p = trainPaths(no, rows);
    if (p.length) paths.set(no, p);
  }
  previewPaths = new Map();
  if (preview) for (const tr of preview.trains) {
    const p = trainPaths(tr.no, tr.filled);
    if (p.length) previewPaths.set(tr.no, p);
  }
}

// ------------------------------------------------------------------------------------------------ drawing
const canvas = $<HTMLCanvasElement>("canvas");
const ctx = canvas.getContext("2d")!;
let LEFT = 150;
const TOP = 26, BOTTOM = 6;
let W = 0, H = 0, dpr = 1;
const xOf = (s: number) => LEFT + (s - view.t0) / (view.t1 - view.t0) * (W - LEFT);
const yOf = (y: number) => TOP + (y - view.y0) / (view.y1 - view.y0) * (H - TOP - BOTTOM);
const tOfX = (x: number) => view.t0 + (x - LEFT) / (W - LEFT) * (view.t1 - view.t0);

function resize(): void {
  const r = canvas.parentElement!.getBoundingClientRect();
  dpr = window.devicePixelRatio || 1;
  W = r.width; H = r.height;
  LEFT = W < 600 ? 92 : 150;
  canvas.width = Math.round(W * dpr); canvas.height = Math.round(H * dpr);
  draw();
}

let raf = 0;
function draw(): void {
  if (raf) return;
  raf = requestAnimationFrame(() => { raf = 0; paint(); });
}

function strokePath(p: Path, shift: number, color: string, width: number, dash: number[]): void {
  ctx.beginPath();
  let started = false;
  for (const [s, y] of p.pts) {
    const x = xOf(s + shift), yy = yOf(y);
    if (!started) { ctx.moveTo(x, yy); started = true; } else ctx.lineTo(x, yy);
  }
  ctx.strokeStyle = color; ctx.lineWidth = width; ctx.setLineDash(dash);
  ctx.stroke();
}

function crosses(p: Path, shift: number, color: string): void {
  // facultative trains: small cross marks along the line (PM 110/2017 graph style)
  ctx.strokeStyle = color; ctx.lineWidth = 0.8; ctx.setLineDash([]);
  let acc = 0;
  for (let i = 1; i < p.pts.length; i++) {
    const x0 = xOf(p.pts[i - 1][0] + shift), y0 = yOf(p.pts[i - 1][1]);
    const x1 = xOf(p.pts[i][0] + shift), y1 = yOf(p.pts[i][1]);
    const L = Math.hypot(x1 - x0, y1 - y0);
    for (let d = 40 - acc; d < L; d += 40) {
      const x = x0 + (x1 - x0) * d / L, y = y0 + (y1 - y0) * d / L;
      ctx.beginPath(); ctx.moveTo(x - 2.5, y - 2.5); ctx.lineTo(x + 2.5, y + 2.5);
      ctx.moveTo(x - 2.5, y + 2.5); ctx.lineTo(x + 2.5, y - 2.5); ctx.stroke();
    }
    acc = (acc + L) % 40;
  }
}

function label(no: string, p: Path, shift: number, color: string): void {
  for (let i = 1; i < p.pts.length; i++) {
    const x0 = xOf(p.pts[i - 1][0] + shift), y0 = yOf(p.pts[i - 1][1]);
    const x1 = xOf(p.pts[i][0] + shift), y1 = yOf(p.pts[i][1]);
    if (y0 === y1) continue;
    const L = Math.hypot(x1 - x0, y1 - y0);
    if (L < 46 || Math.max(x0, x1) < LEFT || Math.min(x0, x1) > W) continue;
    ctx.save();
    ctx.translate((x0 + x1) / 2, (y0 + y1) / 2);
    let a = Math.atan2(y1 - y0, x1 - x0);
    if (a > Math.PI / 2) a -= Math.PI; else if (a < -Math.PI / 2) a += Math.PI;
    ctx.rotate(a);
    ctx.fillStyle = color; ctx.font = "600 10px system-ui, sans-serif"; ctx.textAlign = "center";
    ctx.fillText(no, 0, -3);
    ctx.restore();
    return;
  }
}

function paint(): void {
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.fillStyle = "#fff";
  ctx.fillRect(0, 0, W, H);
  if (!geom) return;
  const span = view.t1 - view.t0, pxPerMin = (W - LEFT) / (span / 60);
  // time grid: minute lines at a step that stays readable, hours strong
  const minor = [1, 2, 5, 10, 15, 30, 60].find((m) => m * pxPerMin >= 7) ?? 60;
  ctx.save();
  ctx.beginPath(); ctx.rect(LEFT, TOP, W - LEFT, H - TOP); ctx.clip();
  const g0 = Math.floor(view.t0 / 60 / minor) * minor, g1 = Math.ceil(view.t1 / 60 / minor) * minor;
  for (let m = g0; m <= g1; m += minor) {
    const x = Math.round(xOf(m * 60)) + 0.5;
    ctx.beginPath(); ctx.moveTo(x, TOP); ctx.lineTo(x, H);
    ctx.setLineDash([]);
    ctx.strokeStyle = m % 60 === 0 ? "#1d1d1b" : m % 10 === 0 ? "#cfcdc4" : "#eeede8";
    ctx.lineWidth = m % 60 === 0 ? 1 : 1;
    ctx.stroke();
  }
  // station rows (halts dashed), segment breaks
  for (const r of geom.rows) {
    const y = Math.round(yOf(r.y)) + 0.5;
    if (y < TOP - 2 || y > H + 2) continue;
    ctx.beginPath(); ctx.moveTo(LEFT, y); ctx.lineTo(W, y);
    ctx.setLineDash(r.kind === "halt" ? [3, 3] : r.kind === "sheet_only" ? [1, 4] : []);
    ctx.strokeStyle = r.kind === "station" ? "#55534c" : "#a9a69c";
    ctx.lineWidth = 1;
    ctx.stroke();
  }
  ctx.setLineDash([]);
  for (let i = 1; i < geom.segs.length; i++) {
    const y0 = yOf(geom.segs[i - 1].y1), y1 = yOf(geom.segs[i].y0);
    ctx.fillStyle = "#f4f3ef"; ctx.fillRect(LEFT, y0 + 2, W - LEFT, y1 - y0 - 4);
  }
  // trains (each also a day earlier / later so lines across midnight are drawn on both sides)
  const showLabels = pxPerMin >= 2.2;
  const changed = new Set(preview ? preview.trains.map((x) => x.no) : []);
  for (const shift of [-DAY, 0, DAY]) {
    if (view.t1 < shift - 3600 || view.t0 > shift + 2 * DAY) continue;
    for (const [no, ps] of paths) {
      const dim = changed.has(no);
      for (const p of ps) {
        const last = p.pts[p.pts.length - 1][0] + shift, first = p.pts[0][0] + shift;
        if (last < view.t0 || first > view.t1) continue;
        const col = TYPE_COLOR[p.type] ?? "#333";
        const dash = p.type === "freight" ? [7, 3] : p.type === "dinas" ? [2, 3] : [];
        if (dim) { ctx.globalAlpha = 0.28; strokePath(p, shift, "#888", 1, [4, 3]); ctx.globalAlpha = 1; continue; }
        strokePath(p, shift, col, no === selected ? 2.6 : 1.15, dash);
        if (p.fac && pxPerMin >= 1) crosses(p, shift, col);
        if (showLabels) label(no, p, shift, col);
      }
    }
    for (const [no, ps] of previewPaths) {
      for (const p of ps) {
        const col = TYPE_COLOR[p.type] ?? "#333";
        strokePath(p, shift, "#ff6d00", no === selected ? 3 : 2, []);
        strokePath(p, shift, col, 0.8, []);
        if (showLabels) label(no, p, shift, "#c44f00");
      }
    }
  }
  ctx.restore();
  // left gutter: station names
  ctx.fillStyle = "#fff"; ctx.fillRect(0, 0, LEFT, H);
  ctx.strokeStyle = "#1d1d1b"; ctx.lineWidth = 1;
  ctx.beginPath(); ctx.moveTo(LEFT + 0.5, 0); ctx.lineTo(LEFT + 0.5, H); ctx.stroke();
  const pxPerKm = (H - TOP - BOTTOM) / (view.y1 - view.y0);
  let lastY = -99;
  for (const r of geom.rows) {
    const y = yOf(r.y);
    if (y < TOP - 4 || y > H + 4) continue;
    const isSt = r.kind === "station";
    if (!isSt && (y - lastY < 11 || pxPerKm < 6)) continue;
    if (isSt && y - lastY < 9) continue;
    ctx.fillStyle = isSt ? "#1d1d1b" : "#8a877d";
    ctx.font = `${isSt ? "600 " : "italic "}${LEFT < 120 ? 10 : 11}px system-ui, sans-serif`;
    ctx.textAlign = "right"; ctx.textBaseline = "middle";
    const nm = stName(r.code);
    const max = LEFT < 120 ? 13 : 22;
    ctx.fillText(nm.length > max ? nm.slice(0, max - 1) + "…" : nm, LEFT - 8, y);
    lastY = y;
  }
  // top axis: hours
  ctx.fillStyle = "#fff"; ctx.fillRect(LEFT, 0, W - LEFT, TOP);
  ctx.beginPath(); ctx.moveTo(LEFT, TOP + 0.5); ctx.lineTo(W, TOP + 0.5); ctx.strokeStyle = "#1d1d1b"; ctx.stroke();
  const hourStep = [1, 2, 3, 6].find((h) => h * 60 * pxPerMin >= 44) ?? 6;
  ctx.fillStyle = "#1d1d1b"; ctx.font = "600 11px system-ui, sans-serif"; ctx.textAlign = "center";
  ctx.textBaseline = "alphabetic";
  for (let h = Math.floor(view.t0 / 3600); h <= Math.ceil(view.t1 / 3600); h++) {
    if (h % hourStep) continue;
    const x = xOf(h * 3600);
    if (x < LEFT + 12 || x > W - 12) continue;
    ctx.fillText(`${String(((h % 24) + 24) % 24).padStart(2, "0")}.00`, x, TOP - 9);
  }
  if (minor < 60 && pxPerMin * 10 >= 26) {
    ctx.fillStyle = "#8a877d"; ctx.font = "10px system-ui, sans-serif";
    for (let m = Math.ceil(view.t0 / 600) * 10; m * 60 <= view.t1; m += 10) {
      if (m % 60 === 0) continue;
      const x = xOf(m * 60);
      if (x > LEFT + 8 && x < W - 8) ctx.fillText(String(m % 60), x, TOP - 9);
    }
  }
  ctx.fillStyle = "#fff"; ctx.fillRect(0, 0, LEFT, TOP);
  ctx.fillStyle = "#6b6a65"; ctx.font = "10px system-ui, sans-serif"; ctx.textAlign = "left";
  ctx.fillText(lang === "id" ? "Stasiun / jam" : "Station / time", 8, TOP - 9);
}

// ------------------------------------------------------------------------------------------------ interaction
function clampView(): void {
  const span = Math.min(Math.max(view.t1 - view.t0, 20 * 60), 30 * 3600);
  const c = (view.t0 + view.t1) / 2;
  view.t0 = Math.max(-6 * 3600, Math.min(c - span / 2, 30 * 3600 - span));
  view.t1 = view.t0 + span;
  if (geom) {
    const h = geom.height || 1, ys = Math.min(Math.max(view.y1 - view.y0, Math.min(5, h)), h * 1.05 + 1);
    const yc = (view.y0 + view.y1) / 2;
    view.y0 = Math.max(-h * 0.03, Math.min(yc - ys / 2, h * 1.03 - ys));
    view.y1 = view.y0 + ys;
  }
}
function fitY(): void {
  if (!geom) return;
  const pad = Math.max(geom.height * 0.02, 0.3);
  view.y0 = -pad; view.y1 = geom.height + pad;
}
function zoomTime(f: number, at = (view.t0 + view.t1) / 2): void {
  view.t0 = at - (at - view.t0) * f;
  view.t1 = at + (view.t1 - at) * f;
  clampView(); draw();
}

let drag: { x: number; y: number; t0: number; t1: number; y0: number; y1: number; moved: boolean } | null = null;
canvas.addEventListener("pointerdown", (e) => {
  canvas.setPointerCapture(e.pointerId);
  drag = { x: e.offsetX, y: e.offsetY, t0: view.t0, t1: view.t1, y0: view.y0, y1: view.y1, moved: false };
});
canvas.addEventListener("pointermove", (e) => {
  if (drag) {
    const dx = e.offsetX - drag.x, dy = e.offsetY - drag.y;
    if (Math.abs(dx) + Math.abs(dy) > 3) drag.moved = true;
    if (!drag.moved) return;
    canvas.classList.add("drag");
    const dt = dx / (W - LEFT) * (drag.t1 - drag.t0), dyy = dy / (H - TOP - BOTTOM) * (drag.y1 - drag.y0);
    view.t0 = drag.t0 - dt; view.t1 = drag.t1 - dt; view.y0 = drag.y0 - dyy; view.y1 = drag.y1 - dyy;
    clampView(); draw();
    $("tip").hidden = true;
    return;
  }
  hover(e.offsetX, e.offsetY);
});
canvas.addEventListener("pointerup", (e) => {
  const d = drag;
  drag = null;
  canvas.classList.remove("drag");
  if (d && !d.moved) {
    const hit = hitTest(e.offsetX, e.offsetY);
    if (hit) selectTrain(hit.no);
  }
});
canvas.addEventListener("pointerleave", () => { $("tip").hidden = true; });
canvas.addEventListener("wheel", (e) => {
  e.preventDefault();
  if (e.shiftKey || e.ctrlKey) {
    const f = Math.exp(e.deltaY * 0.0015), at = view.y0 + (e.offsetY - TOP) / (H - TOP - BOTTOM) * (view.y1 - view.y0);
    view.y0 = at - (at - view.y0) * f; view.y1 = at + (view.y1 - at) * f;
    clampView(); draw();
    return;
  }
  zoomTime(Math.exp(e.deltaY * 0.0015), tOfX(Math.max(e.offsetX, LEFT)));
}, { passive: false });

function distSeg(px: number, py: number, x0: number, y0: number, x1: number, y1: number): number {
  const dx = x1 - x0, dy = y1 - y0, L = dx * dx + dy * dy;
  const u = L ? Math.max(0, Math.min(1, ((px - x0) * dx + (py - y0) * dy) / L)) : 0;
  return Math.hypot(px - (x0 + u * dx), py - (y0 + u * dy));
}
function hitTest(px: number, py: number): { no: string; t: number; y: number } | null {
  if (px < LEFT || py < TOP) return null;
  let best: { no: string; t: number; y: number } | null = null, bd = 6;
  const scan = (m: Map<string, Path[]>) => {
    for (const [no, ps] of m) for (const p of ps) for (const shift of [-DAY, 0, DAY]) {
      const a = p.pts[0][0] + shift, b = p.pts[p.pts.length - 1][0] + shift;
      if (b < view.t0 || a > view.t1) continue;
      for (let i = 1; i < p.pts.length; i++) {
        const x0 = xOf(p.pts[i - 1][0] + shift), y0 = yOf(p.pts[i - 1][1]);
        const x1 = xOf(p.pts[i][0] + shift), y1 = yOf(p.pts[i][1]);
        if (Math.max(x0, x1) < px - 8 || Math.min(x0, x1) > px + 8) continue;
        const d = distSeg(px, py, x0, y0, x1, y1);
        if (d < bd) { bd = d; best = { no, t: tOfX(px), y: py }; }
      }
    }
  };
  scan(previewPaths);
  if (!best) scan(paths);
  return best;
}
function hover(px: number, py: number): void {
  const tip = $("tip");
  const h = hitTest(px, py);
  if (!h) { tip.hidden = true; return; }
  const tr = trainsBy.get(h.no);
  tip.textContent = `${h.no}${tr?.name ? " " + tr.name : ""} · ${hhmm(h.t)}`;
  tip.style.left = px + "px"; tip.style.top = py + "px";
  tip.hidden = false;
}

// ------------------------------------------------------------------------------------------------ lines / graphs
// a sheet group (pkg.lines) holds several margin tables, each its own graph (L04: Cikampek - Banjar, Andir - Ciroyom -
// Bandung, Cibatu - Garut): pkg.lines[].tables from export_package.line_tables
function buildGraphs(): void {
  graphs = [];
  for (const l of pkg.lines) {
    const tabs: [number, number][] = l.tables?.length ? l.tables : [[0, l.stations.length - 1]];
    tabs.forEach(([a, b], k) => graphs.push({ id: `${l.id}:${k}`, line: l,
      codes: l.stations.slice(a, b + 1).map((x: any[]) => x[0]) }));
  }
}
function graphTitle(g: Graph): string {
  return `${stName(g.codes[0])} – ${stName(g.codes[g.codes.length - 1])}`;
}
function selectLine(id: string, keepView = false): void {
  const g = graphs.find((x) => x.id === id) ?? graphs[0];
  lineId = g.id;
  ($("line") as HTMLSelectElement).value = g.id;
  geom = buildGeom(g.codes);
  rebuildPaths();
  if (!keepView) fitY();
  clampView();
  sheetTitle();
  draw();
}
function sheetTitle(): void {
  const g = graphs.find((x) => x.id === lineId);
  if (!g) return;
  const pages = String(g.line.pages ?? "").trim().split(/\s+/);
  const pg = pages.length > 1 ? `${pages[0]}–${pages[pages.length - 1]}` : pages[0];
  $("sheet-title").innerHTML = `${esc(t("web.line"))} ${esc(graphTitle(g))} <span class="sheet-sub">${
    esc(lineTitle(g.line))} · ${lang === "id" ? "hlm." : "p."} ${esc(pg)}</span>`;
}
function lineTitle(l: any): string {
  return l.title.split(" - ").map((x: string) => x.trim()).join(" – ");
}
function bestGraphFor(st: Set<string>): string | null {
  let best: string | null = null, n = 1;
  for (const g of graphs) {
    const c = new Set(g.codes.filter((x) => st.has(x))).size;
    if (c > n) { n = c; best = g.id; }
  }
  return best;
}
function bestLine(no: string): string | null {
  const rows = timeline[no];
  return rows ? bestGraphFor(new Set(rows.map((r) => r[0]))) : null;
}
function focusPaths(ps: Path[] | undefined): void {
  if (!ps || !ps.length) return;
  const ts = ps.flatMap((p) => p.pts.map((q) => q[0])), ys = ps.flatMap((p) => p.pts.map((q) => q[1]));
  const a = Math.min(...ts), b = Math.max(...ts);
  const span = Math.max(b - a + 3600, 3 * 3600);
  view.t0 = (a + b) / 2 - span / 2; view.t1 = view.t0 + span;
  const y0 = Math.min(...ys), y1 = Math.max(...ys), pad = Math.max((y1 - y0) * 0.15, 2);
  view.y0 = y0 - pad; view.y1 = y1 + pad;
  clampView(); draw();
}
function focusTrain(no: string): void {
  const lid = bestLine(no);
  if (!lid) { alert(t("web.not_found", { train: no })); return; }
  if (lid !== lineId) selectLine(lid);
  focusPaths(paths.get(no));
  selectTrain(no);
}

// ------------------------------------------------------------------------------------------------ panel: train
function setTab(k: typeof tab): void {
  tab = k;
  for (const b of document.querySelectorAll<HTMLButtonElement>(".tabs button")) b.classList.toggle("on", b.dataset.tab === k);
  $("pane-train").hidden = k !== "train"; $("pane-new").hidden = k !== "new"; $("pane-scenario").hidden = k !== "scenario";
  if (k === "scenario") renderScenario();
}
function typeBadge(type: string): string {
  return `<span class="badge" style="background:${TYPE_COLOR[type] ?? "#333"}">${esc(t("train.type." + type))}</span>`;
}

function selectTrain(no: string): void {
  selected = no;
  setTab("train");
  renderTrain();
  draw();
}

function renderTrain(): void {
  const pane = $("pane-train");
  const no = selected;
  const tr = no ? trainsBy.get(no) : null;
  if (!no || !tr) { pane.innerHTML = `<p class="empty">${esc(t("web.no_train"))}</p>`; return; }
  const rows: Row[] = tr.stops;
  const route = `${stName(rows[0][0])} – ${stName(rows[rows.length - 1][0])}`;
  const editable = tr.type !== "dinas";
  let html = `<h2>KA ${esc(no)} ${esc(tr.name && tr.name !== no ? tr.name : "")}</h2>
    <div class="meta">${typeBadge(tr.type)}${tr.facultative ? ` · ${esc(t("train.facultative"))}` : ""}<br>${esc(route)}</div>`;
  if (editable) {
    const vmax = Math.max(...rows.map((r) => r[4] ?? 0));
    html += `<div class="grid2">
      <label class="field">${esc(t("web.late"))}<input id="e-late" type="number" min="-120" max="600" step="1" value="0"></label>
      <label class="field">${esc(t("edit.vmax"))}<input id="e-vmax" type="number" min="20" max="160" step="5" placeholder="${vmax}"></label>
      <label class="field">${esc(t("edit.type"))}<select id="e-type">${["intercity", "commuter", "krl", "freight"].map((k) =>
        `<option value="${k}" ${k === tr.type ? "selected" : ""}>${esc(t("train.type." + k))}</option>`).join("")}</select></label>
      <label class="field">${esc(t("edit.length"))}<input id="e-cars" type="number" min="1" max="60" step="1" placeholder="${tr.consist?.cars ?? ""}"></label>
    </div>`;
  }
  html += `<h3>${esc(t("edit.stops"))}</h3><table class="tt"><thead><tr><th>${esc(t("web.station"))}</th><th>${esc(t("web.arr"))}</th>
    <th>${esc(t("web.dep"))}</th>${editable ? `<th title="${esc(t("stop.kind.stop"))}">${esc(t("stop.kind.stop"))}</th>
    <th title="${esc(t("edit.dwell"))}">${lang === "id" ? "Menit" : "Min"}</th>` : ""}</tr></thead><tbody>`;
  rows.forEach((r, i) => {
    const kind = r[1], isHalt = kind === "halt" || pkg.stations[r[0]]?.kind === "halt";
    const est = r[5] !== "printed";
    const cls = kind === "pass" ? "pass" : isHalt ? "halt" : "";
    let edit = "";
    if (editable) {
      const can = (kind === "stop" || kind === "pass") && !isHalt && i > 0 && i < rows.length - 1;
      edit = `<td>${can ? `<input type="checkbox" data-kind="${i}" ${kind === "stop" ? "checked" : ""} aria-label="${esc(t("stop.kind.stop"))}">` : ""}</td>
        <td>${kind === "stop" ? `<input data-dwell="${i}" type="number" min="0" max="240" step="1" value="${
          r[2] !== null && r[3] !== null ? Math.round((r[3] - r[2]) / 60) : 1}" aria-label="${esc(t("edit.dwell"))}">` : ""}</td>`;
    }
    const tt = (v: number | null) => v === null ? "" : `<span class="${est ? "est" : ""}" title="${esc(t(est ? "origin.model" : "origin.printed"))}">${hhmm(v)}</span>`;
    html += `<tr class="${cls}"><td>${esc(stName(r[0]))}</td><td class="t">${kind === "pass" ? "" : tt(r[2])}</td>
      <td class="t">${tt(r[3] ?? (kind === "pass" ? r[2] : null))}</td>${edit}</tr>`;
  });
  html += `</tbody></table>`;
  if (editable) {
    html += `<div class="actions"><button class="btn primary" id="e-sim">${esc(t("web.simulate"))}</button></div>
      <div class="note">${esc(t("ui.disclaimer_short"))}</div><div id="e-result"></div>`;
  }
  pane.innerHTML = html;
  if (!editable) return;
  $("e-sim").onclick = () => simulateEdit(no, rows);
  if (preview && preview.info.edited === no) renderPreview($("e-result"));
}

async function simulateEdit(no: string, rows: Row[]): Promise<void> {
  const edit: any = {};
  const late = Number(($("e-late") as HTMLInputElement).value || 0);
  if (late) edit.late = late;
  const vmax = ($("e-vmax") as HTMLInputElement).value;
  if (vmax) edit.vmax = Number(vmax);
  const type = ($("e-type") as HTMLSelectElement).value;
  if (type !== trainsBy.get(no).type) edit.type = type;
  const cars = ($("e-cars") as HTMLInputElement).value;
  if (cars) edit.cars = Number(cars);
  for (const el of document.querySelectorAll<HTMLInputElement>("[data-dwell]")) {
    const i = Number(el.dataset.dwell), r = rows[i];
    const old = r[2] !== null && r[3] !== null ? Math.round((r[3] - r[2]) / 60) : 1;
    if (Number(el.value) !== old) (edit.dwell ??= {})[i] = Number(el.value) * 60;
  }
  for (const el of document.querySelectorAll<HTMLInputElement>("[data-kind]")) {
    const i = Number(el.dataset.kind), k = el.checked ? "stop" : "pass";
    if (k !== rows[i][1]) (edit.stops ??= {})[i] = k;
  }
  await runPlan(() => call<PlanResult>("edit", { train: no, edit, lang }), $("e-result"));
}

async function runPlan(fn: () => Promise<PlanResult>, box: HTMLElement, focus = false): Promise<void> {
  box.innerHTML = `<div class="result">${esc(t("web.computing"))}</div>`;
  try {
    preview = await fn();
    rebuildPaths();
    if (focus) focusPaths(previewPaths.get(preview.info.edited));
    draw();
    renderPreview(box);
    box.scrollIntoView({ block: "nearest", behavior: "smooth" });
  } catch (e) {
    box.innerHTML = `<div class="result warn">${esc(String((e as Error).message).split("\n")[0])}</div>`;
  }
}

function lateText(s: number | null): string {
  if (s === null) return "";
  const m = Math.round(s / 60);
  const cls = m > 0 ? "pos" : m < 0 ? "neg" : "";
  return `<span class="late ${cls}">${m > 0 ? "+" : ""}${m}′</span> ${esc(m > 0 ? t("web.end_late", { minutes: m }) :
    m < 0 ? t("web.end_early", { minutes: -m }) : t("web.end_same"))}`;
}

function renderPreview(box: HTMLElement): void {
  if (!preview) { box.innerHTML = ""; return; }
  const me = preview.info.edited;
  const mine = preview.trains.find((x) => x.no === me);
  const others = preview.trains.filter((x) => x.no !== me);
  let html = `<div class="result"><strong>${esc(t("web.preview"))}</strong>`;
  if (mine && mine.late_s !== null) html += `<div>KA ${esc(me)}: ${lateText(mine.late_s)}</div>`;
  if (mine && mine.late_s === null && mine.filled.length) {
    const a = mine.filled[0], b = mine.filled[mine.filled.length - 1];
    html += `<div>KA ${esc(me)}: ${esc(stName(a[0]))} <span class="late">${hhmm(a[3])}</span> → ${esc(stName(b[0]))}
      <span class="late">${hhmm(b[2])}</span></div>`;
  }
  html += `<div class="note">${esc(t(preview.info.physics ? "web.physics" : "web.printed_kept"))}</div>`;
  if (preview.texts.length) html += `<ul>${preview.texts.map((x) => `<li>${esc(x)}</li>`).join("")}</ul>`;
  if (others.length) {
    html += `<h3>${esc(t("web.affected"))} (${others.length})</h3><ul>${others.map((o) =>
      `<li><a href="#" data-go="${esc(o.no)}">KA ${esc(o.no)}</a> ${lateText(o.late_s)}</li>`).join("")}</ul>`;
  }
  html += `<div class="actions"><button class="btn primary" id="p-apply">${esc(t("web.apply"))}</button>
    <button class="btn" id="p-discard">${esc(t("web.discard"))}</button></div></div>`;
  box.innerHTML = html;
  $("p-apply").onclick = applyPreview;
  $("p-discard").onclick = () => { preview = null; rebuildPaths(); draw(); box.innerHTML = ""; };
  for (const a of box.querySelectorAll<HTMLAnchorElement>("[data-go]")) a.onclick = (e) => { e.preventDefault(); focusTrain(a.dataset.go!); };
}

async function applyPreview(): Promise<void> {
  if (!preview) return;
  const res = preview;
  await call("apply", { result: res });
  const trains = await call<any[]>("trains");
  trainsBy = new Map(trains.map((x) => [x.no, { ...(trainsBy.get(x.no) ?? {}), ...x }]));
  timeline = await call("timeline");
  applied.push({ no: res.info.edited, label: describe(res), changed: res.trains.map((x) => x.no) });
  preview = null;
  rebuildPaths(); draw();
  renderTrain();
  renderScenarioCount();
}
function describe(res: PlanResult): string {
  const mine = res.trains.find((x) => x.no === res.info.edited);
  return `KA ${res.info.edited}${mine && mine.late_s !== null ? ` (${mine.late_s > 0 ? "+" : ""}${Math.round(mine.late_s / 60)}′)` : ""}`;
}

// ------------------------------------------------------------------------------------------------ panel: new train
function renderNew(): void {
  const pane = $("pane-new");
  const opts = Object.entries(pkg.stations as Record<string, any>)
    .filter(([c, s]) => s.kind === "station" && !c.startsWith("?"))
    .map(([c]) => [c, stName(c)] as [string, string]).sort((a, b) => a[1].localeCompare(b[1]));
  const dl = `<datalist id="st-list">${opts.map(([c, n]) => `<option value="${esc(n)} (${esc(c)})">`).join("")}</datalist>`;
  pane.innerHTML = `<h2>${esc(t("edit.new_train"))}</h2><p class="note">${esc(t("warn.unscheduled"))}</p>${dl}
    <div class="grid2">
      <label class="field">${esc(t("web.new_from"))}<input id="n-from" list="st-list" value="${esc(stName("BD"))} (BD)"></label>
      <label class="field">${esc(t("web.new_to"))}<input id="n-to" list="st-list" value="${esc(stName("CCL"))} (CCL)"></label>
      <label class="field">${esc(t("edit.departure"))}<input id="n-dep" type="time" value="08:00"></label>
      <label class="field">${esc(t("web.new_number"))}<input id="n-no" value="KLB${applied.length + 1}"></label>
      <label class="field">${esc(t("edit.type"))}<select id="n-type">${["commuter", "intercity", "krl", "freight"].map((k) =>
        `<option value="${k}">${esc(t("train.type." + k))}</option>`).join("")}</select></label>
      <label class="field">${esc(t("edit.vmax"))}<input id="n-vmax" type="number" value="90" min="20" max="160" step="5"></label>
      <label class="field">${esc(t("edit.length"))}<input id="n-cars" type="number" value="8" min="1" max="60"></label>
    </div>
    <div class="actions"><button class="btn" id="n-route">${esc(t("edit.stops"))} →</button></div>
    <div id="n-stops"></div><div id="n-result"></div>`;
  $("n-route").onclick = loadRoute;
}
const codeOf = (v: string) => (/\(([^)]+)\)\s*$/.exec(v)?.[1] ?? v).trim();
async function loadRoute(): Promise<void> {
  const a = codeOf(($("n-from") as HTMLInputElement).value), b = codeOf(($("n-to") as HTMLInputElement).value);
  const route = await call<string[] | null>("route", { a, b });
  const box = $("n-stops");
  if (!route) { box.innerHTML = `<p class="warn">${esc(t("web.no_route"))}</p>`; return; }
  const type = ($("n-type") as HTMLSelectElement).value;
  box.innerHTML = `<div class="chips">${route.map((c, i) => {
    const halt = pkg.stations[c]?.kind === "halt", end = i === 0 || i === route.length - 1;
    const on = end || (type === "krl" || (type === "commuter" && !halt)) ? "checked" : "";
    return `<label class="${halt ? "halt" : ""}"><input type="checkbox" data-st="${esc(c)}" ${on} ${end ? "disabled" : ""}>${esc(stName(c))}</label>`;
  }).join("")}</div><div class="actions"><button class="btn primary" id="n-sim">${esc(t("web.simulate"))}</button></div>`;
  $("n-sim").onclick = () => {
    const [h, m] = ($("n-dep") as HTMLInputElement).value.split(":").map(Number);
    const stops: Record<string, number> = {};
    for (const el of box.querySelectorAll<HTMLInputElement>("[data-st]")) if (el.checked && !el.disabled) stops[el.dataset.st!] = 60;
    const nt = { no: ($("n-no") as HTMLInputElement).value.trim() || "KLB", type, route, depart: h * 3600 + m * 60, stops,
      vmax: Number(($("n-vmax") as HTMLInputElement).value) || 80, cars: Number(($("n-cars") as HTMLInputElement).value) || undefined };
    trainsBy.set(nt.no, { no: nt.no, name: nt.no, type, stops: [] });
    void runPlan(async () => {
      const r = await call<PlanResult>("create", { train: nt, lang });
      trainsBy.set(nt.no, { no: nt.no, name: nt.no, type, stops: r.trains[0].rows });
      const lid = bestLineFor(route);
      if (lid && lid !== lineId) { selectLine(lid); }
      selected = nt.no;
      return r;
    }, $("n-result"), true);
  };
}
function bestLineFor(st: string[]): string | null {
  return bestGraphFor(new Set(st));
}

// ------------------------------------------------------------------------------------------------ panel: scenario
function renderScenarioCount(): void {
  $("tab-scenario").textContent = applied.length ? `${t("web.scenario")} (${applied.length})` : t("web.scenario");
}
async function renderScenario(): Promise<void> {
  const pane = $("pane-scenario");
  if (!applied.length) { pane.innerHTML = `<p class="empty">${esc(t("web.scenario_empty"))}</p>`; return; }
  const changed = new Set(applied.flatMap((a) => a.changed));
  let html = `<h2>${esc(t("web.changed", { n: changed.size }))}</h2><ul>${applied.map((a) =>
    `<li><a href="#" data-go="${esc(a.no)}">${esc(a.label)}</a> · ${a.changed.length} KA</li>`).join("")}</ul>`;
  const warns = await call<any[]>("warnings");
  if (warns.length) html += `<h3>${esc(t("warn.capacity").split(":")[0])}</h3><ul>${warns.map((w) =>
    `<li class="warn">${esc(t("warn.capacity", { a: stName(w.a), b: stName(w.b), load: w.load, capacity: w.capacity }))}</li>`).join("")}</ul>`;
  html += `<div class="actions"><button class="btn" id="s-reset">${esc(t("web.reset"))}</button></div>`;
  pane.innerHTML = html;
  $("s-reset").onclick = async () => {
    await call("reset");
    applied = [];
    preview = null;
    await reloadScenario();
    renderScenario(); renderScenarioCount(); renderTrain();
  };
  for (const a of pane.querySelectorAll<HTMLAnchorElement>("[data-go]")) a.onclick = (e) => { e.preventDefault(); focusTrain(a.dataset.go!); };
}
async function reloadScenario(): Promise<void> {
  timeline = await call("timeline");
  trainsBy = new Map((pkg.trains as any[]).map((x) => [x.no, x]));
  rebuildPaths(); draw();
}

// ------------------------------------------------------------------------------------------------ texts, legend
function md(s: string): string {
  const lines = esc(s).replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>").split("\n");
  let out = "", inList = false;
  for (const l of lines) {
    if (/^\s*- /.test(l)) { if (!inList) { out += "<ul>"; inList = true; } out += `<li>${l.replace(/^\s*- /, "")}</li>`; continue; }
    if (/^\s{2,}\S/.test(l) && inList) { out = out.replace(/<\/li>$/, " " + l.trim() + "</li>"); continue; }
    if (inList) { out += "</ul>"; inList = false; }
    if (l.trim()) out += `<p>${l}</p>`;
  }
  return out + (inList ? "</ul>" : "");
}
function applyTexts(): void {
  document.documentElement.lang = lang;
  document.title = t("web.title");
  $("title").textContent = t("web.title");
  $("publisher").textContent = t("web.publisher");
  $("unofficial").textContent = lang === "id" ? "tidak resmi" : "unofficial";
  $("edition").textContent = pkg.meta.edition;
  $("lbl-line").textContent = t("web.line");
  ($("search") as HTMLInputElement).placeholder = t("web.search");
  $("zoom-in").title = t("web.zoom_in"); $("zoom-out").title = t("web.zoom_out");
  $("fit").textContent = t("web.fit");
  $("lang").textContent = t("web.language");
  $("hint").textContent = t("web.hint");
  $("tab-train").textContent = lang === "id" ? "KA" : "Train";
  $("tab-new").textContent = lang === "id" ? "KA baru" : "New train";
  renderScenarioCount();
  $("disclaimer-short").textContent = t("ui.disclaimer_short");
  $("disclaimer-open").textContent = t("ui.disclaimer_title");
  $("disclaimer-close").textContent = t("web.close");
  $("disclaimer-body").innerHTML = md(pkg.meta.disclaimer?.[lang] ?? "");
  $("legend").innerHTML = ["intercity", "commuter", "krl", "freight", "dinas"].map((k) =>
    `<span><i style="border-color:${TYPE_COLOR[k]};${k === "freight" ? "border-top-style:dashed" : k === "dinas" ? "border-top-style:dotted" : ""}"></i>${esc(t("train.type." + k))}</span>`).join("") +
    `<span><i style="border-color:#ff6d00;border-top-width:3px"></i>${esc(t("origin.simulated"))}</span>`;
  const sel = $("line") as HTMLSelectElement;
  sel.innerHTML = pkg.lines.map((l: any) => `<optgroup label="${esc(lineTitle(l))}">${graphs.filter((g) => g.line === l)
    .map((g) => `<option value="${g.id}">${esc(graphTitle(g))}</option>`).join("")}</optgroup>`).join("");
  sel.value = lineId;
  if (geom) sheetTitle();
  renderTrain();
  renderNew();
  if (tab === "scenario") renderScenario();
}

// ------------------------------------------------------------------------------------------------ start
async function main(): Promise<void> {
  const buf = await (await fetch(PKG_URL)).arrayBuffer();
  const loading = call("load", { data: buf.slice(0) });
  const head = new Uint8Array(buf, 0, 2);
  pkg = head[0] === 0x1f && head[1] === 0x8b
    ? JSON.parse(await new Response(new Blob([buf]).stream().pipeThrough(new DecompressionStream("gzip"))).text())
    : JSON.parse(new TextDecoder().decode(buf));
  await loading;
  trainsBy = new Map((pkg.trains as any[]).map((x) => [x.no, x]));
  buildPairKm();
  timeline = await call("timeline");
  buildGraphs();
  const withBd = graphs.filter((g) => g.codes.includes("BD")).sort((a, b) => b.codes.length - a.codes.length);
  lineId = (withBd[0] ?? graphs[0]).id;
  applyTexts();
  selectLine(lineId);
  $("loading").hidden = true;
  resize();
}

$("line").addEventListener("change", (e) => selectLine((e.target as HTMLSelectElement).value));
$("search-form").addEventListener("submit", (e) => {
  e.preventDefault();
  const v = ($("search") as HTMLInputElement).value.trim().toUpperCase();
  if (v) focusTrain(v);
});
$("zoom-in").onclick = () => zoomTime(0.6);
$("zoom-out").onclick = () => zoomTime(1 / 0.6);
$("fit").onclick = () => { view.t0 = 0; view.t1 = DAY; fitY(); clampView(); draw(); };
$("lang").onclick = () => {
  lang = lang === "id" ? "en" : "id";
  try { localStorage.setItem("gapeka-lang", lang); } catch { /* storage blocked */ }
  applyTexts(); draw();
};
for (const b of document.querySelectorAll<HTMLButtonElement>(".tabs button")) b.onclick = () => setTab(b.dataset.tab as typeof tab);
$("disclaimer-open").onclick = () => ($("disclaimer") as HTMLDialogElement).showModal();
new ResizeObserver(resize).observe($("graph"));
main().catch((e) => { $("loading").textContent = String(e); });
