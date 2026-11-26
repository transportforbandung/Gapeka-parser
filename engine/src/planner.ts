// Port of planner.py (Timeline, Planner, dispatch, replan_test, capacity_warnings, explain). Keep it line by line
// with the Python reference: tests compare the two to the last bit (engine/test/planner.test.ts).
// Python semantics kept on purpose: insertion-ordered dicts (Map), tuple ordering (cmp), heapq order (Dijkstra),
// round() half to even, floor modulo, min / max return the first best element.
import { Profiles, type ProfileRow } from "./physics.js";
import { Engine, type Pkg, type Row, type Train } from "./replay.js";
import { pyRound, pyRoundInt, pySplit, pySum } from "./py.js";

type Num = number;
/** timeline row: [station, kind, arr, dep] (times filled) */
export type TRow = [string, string, Num, Num];
type BlockEntry = [Num, Num, string, boolean];      // enter, exit, train, stood at the entry station
type PointEntry = [Num, Num, string, string | null, string | null];
type Knot = [Num, Num, Num];                        // fraction, arrival - t0, departure - t0
/** conflict: (earliest new departure, other train, kind, other's entry) */
export type Conflict = [Num, string | null, string, Num?];
/** wait: [station, other train, kind, seconds] */
export type Wait = [string, string | null, string, Num];
type PRow = Row;                                    // planned row [station, kind, arr, dep, vmax, origin]

const K = (...p: (string | number | boolean | null)[]) => p.join("\u0000");
const DAY = 86400;

/** Python tuple ordering for the index entries */
export function cmp(a: readonly unknown[], b: readonly unknown[]): number {
  const n = Math.min(a.length, b.length);
  for (let i = 0; i < n; i++) {
    const x = a[i], y = b[i];
    if (x === y) continue;
    if (x === null || x === undefined) return -1;
    if (y === null || y === undefined) return 1;
    if (typeof x === "boolean" || typeof y === "boolean") return (x ? 1 : 0) - (y ? 1 : 0);
    return (x as number | string) < (y as number | string) ? -1 : 1;
  }
  return a.length - b.length;
}

/** Python % (floor modulo) */
export function pyMod(a: number, b: number): number {
  let m = a % b;
  if (m !== 0 && (b < 0) !== (m < 0)) m += b;
  return m;
}

/** first index whose entry's first field is >= v (bisect_left with (v,)) */
function lowerFirst(lst: readonly (readonly unknown[])[], v: number): number {
  let lo = 0, hi = lst.length;
  while (lo < hi) {
    const mid = (lo + hi) >> 1;
    if ((lst[mid][0] as number) < v) lo = mid + 1; else hi = mid;
  }
  return lo;
}

/** bisect.insort (insort_right) with tuple order */
function insort<T extends readonly unknown[]>(lst: T[], item: T): void {
  let lo = 0, hi = lst.length;
  while (lo < hi) {
    const mid = (lo + hi) >> 1;
    if (cmp(item, lst[mid]) < 0) hi = mid; else lo = mid + 1;
  }
  lst.splice(lo, 0, item);
}

// heapq (same sift algorithm as CPython, so equal-cost ties pop in the same order)
type HeapItem = [number, string];
function heapLess(a: HeapItem, b: HeapItem): boolean {
  return a[0] < b[0] || (a[0] === b[0] && a[1] < b[1]);
}
function siftdown(h: HeapItem[], start: number, pos: number): void {
  const item = h[pos];
  while (pos > start) {
    const pp = (pos - 1) >> 1;
    const parent = h[pp];
    if (heapLess(item, parent)) { h[pos] = parent; pos = pp; continue; }
    break;
  }
  h[pos] = item;
}
function siftup(h: HeapItem[], pos: number): void {
  const end = h.length, start = pos, item = h[pos];
  let child = 2 * pos + 1;
  while (child < end) {
    const right = child + 1;
    if (right < end && !heapLess(h[child], h[right])) child = right;
    h[pos] = h[child];
    pos = child;
    child = 2 * pos + 1;
  }
  h[pos] = item;
  siftdown(h, start, pos);
}
function heappush(h: HeapItem[], it: HeapItem): void { h.push(it); siftdown(h, 0, h.length - 1); }
function heappop(h: HeapItem[]): HeapItem {
  const last = h.pop()!;
  if (h.length) { const ret = h[0]; h[0] = last; siftup(h, 0); return ret; }
  return last;
}

function getOr<Kk, V>(m: Map<Kk, V>, k: Kk, mk: () => V): V {
  let v = m.get(k);
  if (v === undefined) { v = mk(); m.set(k, v); }
  return v;
}

// =============================================================================================== timeline
export class Timeline {
  kind = new Map<string, string>();
  prof: Profiles;
  trains = new Map<string, TRow[]>();
  sig = new Map<string, number[]>();
  sheet = new Map<string, Map<string, number>>();
  private bp = new Map<string, number[]>();
  block = new Map<string, BlockEntry[]>();
  point = new Map<string, PointEntry[]>();
  blockDouble = new Map<string, boolean>();
  bpts = new Map<string, number[]>();
  blockBase = new Map<string, [Num, Num, string][]>();
  knots = new Map<string, Knot[]>();
  keysOf = new Map<string, [string[], string[], string[]]>();
  ptimes = new Map<string, Map<string, [number[], number[]]>>();
  maxdur = new Map<string, number>();
  blockHmin = new Map<string, number>();

  constructor(public pkg: Pkg, skip: Iterable<string> = []) {
    for (const [c, s] of Object.entries(pkg.stations as Record<string, { kind: string }>)) this.kind.set(c, s.kind);
    this.prof = new Profiles(pkg.profiles as ProfileRow[]);
    this.signalsInit();
    const sk = new Set(skip);
    for (const t of pkg.trains as Train[]) {
      if (sk.has(t.no)) continue;
      const tl = this.fill(t);
      if (tl) this.trains.set(t.no, tl as TRow[]);
    }
    this.index();
  }

  setRows(no: string, rows: PRow[]): void {
    const out: TRow[] = [];
    for (const r of rows) {
      const a = r[2], d = r[3];
      const row = [r[0], r[1], a !== null ? a : d, d !== null ? d : a] as [string, string, Num | null, Num | null];
      if (row[2] !== null) out.push(row as TRow);
    }
    this.trains.set(no, out);  // Map.set keeps an existing key's position, like a Python dict
  }

  private signalsInit(): void {
    for (const sec of this.pkg.sections) {
      if (sec.variant) continue;
      const a: string = sec.a, b: string = sec.b, L: number = sec.km;
      getOr(this.sheet, a, () => new Map()).set(b, L);
      getOr(this.sheet, b, () => new Map()).set(a, L);
      for (const bs of sec.block_signals ?? []) {
        const off = bs[0] as number | null, track = bs[1] as string | null;
        if (off === null) continue;
        const tr = pySplit(track ?? "");
        if (!tr.length || tr.includes("hu")) getOr(this.sig, K(a, b), () => []).push(off);
        if (!tr.length || tr.includes("hi")) getOr(this.sig, K(b, a), () => []).push(L - off);
      }
    }
  }

  sheetRoute(a: string, b: string, limit: number): string[] | null {
    const dist = new Map<string, number>([[a, 0.0]]);
    const prev = new Map<string, string>();
    const pq: HeapItem[] = [[0.0, a]];
    while (pq.length) {
      const [d, x] = heappop(pq);
      if (x === b) {
        const out = [b];
        while (out[out.length - 1] !== a) out.push(prev.get(out[out.length - 1])!);
        return out.reverse();
      }
      if (d > (dist.get(x) ?? 1e9) || d > limit) continue;
      for (const [y, w] of getOr(this.sheet, x, () => new Map())) {
        if (d + w < (dist.get(y) ?? 1e9)) {
          dist.set(y, d + w);
          prev.set(y, x);
          heappush(pq, [d + w, y]);
        }
      }
    }
    return null;
  }

  blockPoints(rows: TRow[], i: number, j: number, ctx?: Set<string>): number[] {
    const stKey = rows.slice(i, j + 1).map((r) => r[0]);
    const key = K(...stKey);
    const hit = this.bp.get(key);
    if (hit) return hit;
    ctx = ctx ?? new Set(stKey);
    let cum = 0.0;
    const pos: number[] = [];
    for (let k = i; k < j; k++) {
      const x = rows[k][0], y = rows[k + 1][0];
      const L = this.km(x, y, ctx) || 0.0;
      const route = this.sheet.get(x)?.has(y) ? [x, y] : this.sheetRoute(x, y, Math.max(3 * L, 5));
      if (route && route.length > 1) {
        const legs: number[] = [];
        for (let n = 0; n + 1 < route.length; n++) legs.push(this.sheet.get(route[n])!.get(route[n + 1])!);
        const rl = pySum(legs);
        let c = 0.0;
        for (let n = 0; n + 1 < route.length; n++) {
          const u = route[n], v = route[n + 1];
          for (const o of this.sig.get(K(u, v)) ?? []) pos.push(cum + (c + o) * (rl ? L / rl : 1.0));
          c += this.sheet.get(u)!.get(v)!;
        }
      }
      cum += L;
    }
    const fr = new Set<number>();
    for (const p of pos) if (cum && 0 < p && p < cum) fr.add(pyRound(p / cum, 4));
    const out = [0.0, ...[...fr].sort((x, y) => x - y), 1.0];
    this.bp.set(key, out);
    return out;
  }

  km(a: string, b: string, ctx: Set<string>): number | null {
    const p = this.prof.get(a, b, ctx);
    return p ? p.km : null;
  }

  /** every row timed: passes without a printed time in proportion to the distance; null if < 2 timed rows */
  fill(t: Train, keepAll = false): (string | Num | null)[][] | null {
    const rows = t.stops.map((r) => [r[0], r[1], r[2], r[3]] as [string, string, Num | null, Num | null]);
    const ctx = new Set(rows.map((r) => r[0]));
    const cum = [0.0];
    for (let i = 0; i < rows.length - 1; i++) {
      const k = this.km(rows[i][0], rows[i + 1][0], ctx);
      cum.push(cum[cum.length - 1] + (k ? k : 1.0));
    }
    const timed: number[] = [];
    rows.forEach((r, i) => { if (r[2] !== null || r[3] !== null) timed.push(i); });
    if (timed.length < 2) return null;
    for (let n = 0; n + 1 < timed.length; n++) {
      const p = timed[n], q = timed[n + 1];
      const t0 = rows[p][3] !== null ? rows[p][3]! : rows[p][2]!;
      const t1 = rows[q][2] !== null ? rows[q][2]! : rows[q][3]!;
      for (let i = p + 1; i < q; i++) {
        const x = t0 + (t1 - t0) * (cum[i] - cum[p]) / ((cum[q] - cum[p]) || 1.0);
        rows[i][2] = rows[i][3] = pyRoundInt(x);
      }
    }
    for (const r of rows) {
      if (r[2] === null) r[2] = r[3];
      if (r[3] === null) r[3] = r[2];
    }
    if (keepAll) return rows;
    return rows.filter((r) => r[2] !== null);
  }

  index(): void {
    this.block = new Map();
    this.point = new Map();
    this.blockBase = new Map();
    this.knots = new Map();
    this.keysOf = new Map();
    this.ptimes = new Map();
    this.maxdur = new Map();
    for (const no of this.trains.keys()) this.add(no, false);
    for (const v of this.block.values()) v.sort(cmp);
    for (const v of this.point.values()) v.sort(cmp);
    this.fitBlockSpacing();
  }

  private md(key: string): number { return this.maxdur.get(key) ?? 0.0; }

  add(no: string, sort = true): void {
    const rows = this.trains.get(no)!;
    const ctx = new Set(rows.map((r) => r[0]));
    const idx: number[] = [];
    rows.forEach((r, i) => { if (this.kind.get(r[0]) !== "halt") idx.push(i); });
    const st = idx.map((i) => rows[i]);
    const bk: string[] = [], pk: string[] = [], kk: string[] = [];
    for (let n = 0; n + 1 < idx.length; n++) {
      const i = idx[n], j = idx[n + 1], x = st[n], y = st[n + 1];
      const key = K(x[0], y[0]);
      const stood = (x[3] - x[2]) >= 60;
      getOr(this.blockBase, key, () => []).push([x[3], y[2], no]);
      const lst = getOr(this.block, key, () => []);
      for (const shift of [-DAY, 0, DAY]) {
        const e: BlockEntry = [x[3] + shift, y[2] + shift, no, stood];
        if (sort) insort(lst, e); else lst.push(e);
      }
      if (y[2] - x[3] > this.md(key)) this.maxdur.set(key, y[2] - x[3]);
      else if (!this.maxdur.has(key)) this.maxdur.set(key, 0.0);
      bk.push(key);
      if (!this.bpts.has(key)) this.bpts.set(key, this.blockPoints(rows, i, j, ctx));
      if (j > i + 1) {
        const kkey = K(x[0], y[0], no);
        this.knots.set(kkey, this.blockKnots(rows, i, j, ctx, x[3]));
        kk.push(kkey);
      }
      if (!this.blockDouble.has(key)) {
        let all = true;
        for (let k = i; k < j; k++) if (!this.double(rows[k][0], rows[k + 1][0], ctx)) { all = false; break; }
        this.blockDouble.set(key, all);
      }
    }
    st.forEach((r, i) => {
      const lst = getOr(this.point, r[0], () => []);
      for (const shift of [-DAY, 0, DAY]) {
        const e: PointEntry = [r[2] + shift, r[3] + shift, no, i ? st[i - 1][0] : null,
          i + 1 < st.length ? st[i + 1][0] : null];
        if (sort) insort(lst, e); else lst.push(e);
      }
      pk.push(r[0]);
      if (r[3] - r[2] > this.md(r[0])) this.maxdur.set(r[0], r[3] - r[2]);
      else if (!this.maxdur.has(r[0])) this.maxdur.set(r[0], 0.0);
    });
    this.keysOf.set(no, [bk, pk, kk]);
  }

  /** entries of block key (or station, point = true) that can be occupied between t0 and t1 (bisect slice) */
  window<T extends BlockEntry | PointEntry>(key: string, t0: number, t1: number, point = false): T[] {
    const lst = (point ? this.point : this.block).get(key) as T[] | undefined;
    if (!lst || !lst.length) return [];
    const lo = lowerFirst(lst, t0 - this.md(key) - 1.0);
    const hi = lowerFirst(lst, t1 + 1.0);
    return lst.slice(lo, hi);
  }

  private remove(no: string): void {
    const ks = this.keysOf.get(no) ?? [[], [], []];
    this.keysOf.delete(no);
    const [bk, pk, kk] = ks;
    for (const key of new Set(bk)) {
      this.block.set(key, this.block.get(key)!.filter((e) => e[2] !== no));
      this.blockBase.set(key, this.blockBase.get(key)!.filter((e) => e[2] !== no));
    }
    for (const st of new Set(pk)) this.point.set(st, this.point.get(st)!.filter((e) => e[2] !== no));
    for (const k of kk) this.knots.delete(k);
    this.ptimes.delete(no);
  }

  drop(no: string): void {
    if (this.trains.has(no)) {
      this.remove(no);
      this.trains.delete(no);
    }
  }

  /** put a train into the timeline: planned rows (planned = true) or timeline rows */
  put(no: string, rows: PRow[] | TRow[], planned = true): void {
    const had = this.trains.has(no);
    if (had) this.remove(no);
    if (planned) {
      const out: TRow[] = [];
      for (const r of rows) {
        const a = r[2], d = r[3];
        const row = [r[0], r[1], a !== null ? a : d, d !== null ? d : a];
        if (row[2] !== null) out.push(row as TRow);
      }
      this.trains.set(no, out);    // Map.set keeps the position of an existing key, like a Python dict
    } else {
      this.trains.set(no, rows as TRow[]);
    }
    this.add(no);
  }

  private fitBlockSpacing(): void {
    const fixed = this.pkg.rules?.headways?.double_block_spacing_s as [string, string, number][] | undefined;
    if (!fixed || !fixed.length) throw new Error("package has no rules.headways.double_block_spacing_s");
    this.blockHmin = new Map(fixed.map(([a, b, h]) => [K(a, b), h + 0.0]));
  }

  blockKnots(rows: TRow[], i: number, j: number, ctx: Set<string>, t0: number): Knot[] {
    const cum = [0.0];
    for (let k = i; k < j; k++) cum.push(cum[cum.length - 1] + (this.km(rows[k][0], rows[k + 1][0], ctx) || 1.0));
    const L = cum[cum.length - 1] || 1.0;
    const out: Knot[] = [[0.0, 0.0, 0.0]];
    for (let n = i + 1; n < j; n++) out.push([cum[n - i] / L, rows[n][2] - t0, rows[n][3] - t0]);
    out.push([1.0, rows[j][2] - t0, rows[j][2] - t0]);
    return out;
  }

  double(a: string, b: string, ctx: Iterable<string> = []): boolean {
    const c = new Set(ctx);
    c.add(a); c.add(b);
    const p = this.prof.get(a, b, c);
    return !!(p && p.double);
  }
}

export const WAGON_M: Record<string, number> = { GD: 12.5, GT: 13.2, GK: 14.0 };
export const LOCO_M: Record<string, number> = { "CC 201": 15.2, "CC 203": 15.2, "CC 204": 15.85, "CC 206": 15.85 };

/** train length (m) from the package consist; null without a consist */
export function trainLength(t: Train): number | null {
  const c = (t.consist ?? {}) as { cars?: number; wagon?: string; locomotive?: string };
  if (!c.cars) return null;
  if (c.wagon) return c.cars * (WAGON_M[c.wagon.slice(0, 2)] ?? 14.0) + (LOCO_M[c.locomotive ?? ""] ?? 16.0);
  if (t.type === "krl" || t.run_class === "commuter-emu" || (!c.locomotive && t.type === "commuter")) return c.cars * 20.0;
  return c.cars * 21.0 + 16.0;
}

/** time (relative to the block entry) at fraction f: reaching it (clear = false) or having passed it */
export function knotTime(knots: Knot[] | null | undefined, f: number, run: number, clear: boolean): number {
  if (!knots || !knots.length) return run * f;
  for (let i = 0; i + 1 < knots.length; i++) {
    const [f0, a0, d0] = knots[i], [f1, a1, d1] = knots[i + 1];
    if (f <= f1 + 1e-9) {
      if (Math.abs(f - f1) < 1e-4) return clear ? d1 : a1;
      if (Math.abs(f - f0) < 1e-4) return clear ? d0 : a0;
      return d0 + (a1 - d0) * (f - f0) / ((f1 - f0) || 1.0);
    }
  }
  const l = knots[knots.length - 1];
  return clear ? l[2] : l[1];
}

function ends(t: Train): [[string, Num | null], [string, Num | null]] {
  const a = t.stops[0], b = t.stops[t.stops.length - 1];
  return [[a[0], a[3] !== null ? a[3] : a[2]], [b[0], b[2] !== null ? b[2] : b[3]]];
}

/** consecutive trains of one unit that connect at the same station: [prev, next, duty kind] */
export function dutyLinks(pkg: Pkg): [string, string, string][] {
  const T = new Map<string, Train>((pkg.trains as Train[]).map((t) => [t.no, t]));
  const out: [string, string, string][] = [];
  for (const d of pkg.duties) {
    const tr = (d.trains as string[]).filter((n) => T.has(n));
    for (let i = 0; i + 1 < tr.length; i++) {
      const a = tr[i], b = tr[i + 1];
      const [, [eaSt, ea]] = ends(T.get(a)!);
      const [[sb, db]] = ends(T.get(b)!);
      if (eaSt === sb && ea !== null && db !== null) out.push([a, b, d.kind]);
    }
  }
  return out;
}

export function hhmmS(s: string): number {
  const [h, m] = s.split(":");
  return parseInt(h, 10) * 3600 + parseInt(m, 10) * 60;
}

interface Track { name: string; length: number; towards: Set<string>; unknown: boolean }
type Prio = [boolean, number, boolean, string];
export interface Window { a: string; b: string; start: number; end: number; direction?: string | null }

const NOTE: Record<string, string> = { cross: "x", overtake: "=", follow: "=", release: "=", window: "=" };

// =============================================================================================== planner
export class Planner {
  eng: Engine;
  kind: Map<string, string>;
  singleFollow: number; singleCross: number; minWait: number; dispatcherGap: number; releaseMargin: number;
  approachBlocks: number; doubleGap: number; tol: number;
  prefer_stop = true;
  recovery = true;
  holdSlack = 600;
  overtakeGap: { single: number; double: number };
  type = new Map<string, string>();
  facultative = new Map<string, boolean>();
  scheduled = new Set<string>();
  windows: Window[] = [];
  ignore = new Set<string>();
  displaced = new Set<string>();
  displacedAt = new Map<string, string>();
  forced = new Set<string>();
  displacedBlk = new Map<string, [string, string]>();
  tail = false;
  local = false;
  noHold = new Set<string>();                 // K(holder, held)
  holdGain = new Map<string, number>();       // K(holder, held) -> gain
  root: string | null = null;
  chainRule = false;
  stopDisplace = false;
  displace = false;
  prioGap: number; displaceRatio: number;
  tracks = new Map<string, Track[]>();
  roman = new Map<string, number>();
  longest = new Map<string, number>();
  length = new Map<string, number | null>();
  dutyPrev = new Map<string, [string, number][]>();
  dutyNext = new Map<string, [string, number][]>();
  linkGap = new Map<string, [number, number, number]>();
  exempt = new Set<string>();
  closed = new Map<string, [number, number][]>();
  skip: string | null = null;
  printed: (string | Num | null)[][] | null = null;
  added = new Set<number>();
  private prioCache = new Map<string, Prio>();

  constructor(public pkg: Pkg, public tl: Timeline) {
    this.eng = new Engine(pkg);
    this.kind = tl.kind;
    const hw = pkg.rules.headways;
    this.singleFollow = hw.single_follow_min * 60;
    this.singleCross = hw.single_cross_min * 60;
    const gaps = new Map<string, number>();
    for (const w of pkg.rules.wait_gaps) if (w.line === "*") gaps.set(K(w.event, w.track), parseFloat(w.gap_min) * 60);
    const eps = (pkg.rules.engine_params ?? []) as { param: string; value: string }[];
    const ep = new Map<string, string>();
    for (const x of eps) ep.set(x.param, x.value);
    const first = (name: string) => { const x = eps.find((e) => e.param === name); return x ? parseFloat(x.value) : null; };
    this.minWait = (first("min_wait") ?? 1) * 60;
    this.dispatcherGap = parseFloat(ep.get("dispatcher_gap") ?? "2") * 60;
    this.releaseMargin = parseFloat(ep.get("release_margin") ?? "1") * 60;
    const ab = first("approach_blocks");
    this.approachBlocks = ab !== null ? Math.trunc(ab) : 1;
    this.doubleGap = (first("double_follow_gap") ?? 2) * 60;
    this.tol = first("time_tolerance") ?? 59.0;
    this.overtakeGap = { single: gaps.get(K("overtake", "single")) ?? 120, double: gaps.get(K("overtake", "double")) ?? 150 };
    for (const t of pkg.trains as Train[]) {
      this.type.set(t.no, t.type);
      this.facultative.set(t.no, !!t.facultative);
      this.scheduled.add(t.no);
    }
    this.prioGap = Math.trunc(parseFloat(ep.get("priority_number_gap") ?? "50"));
    this.displaceRatio = parseFloat(ep.get("displace_cost_ratio") ?? "3");
    const merged = new Map<string, Map<string, Track>>();
    for (const L of pkg.layouts) {
      const seen = new Map<string, number>();
      for (const tr of L.tracks) {
        const [name, kind, length, usable, towards, , through, oos] = tr as [string, string, number | null, string,
          string | null, string, boolean, boolean];
        if (kind !== "roman" || oos) continue;
        seen.set(name, (seen.get(name) ?? 0) + 1);
        const key = K(name, seen.get(name)!);
        const st = getOr(merged, L.station as string, () => new Map());
        const m = getOr(st, key, () => ({ name, length: 0.0, towards: new Set<string>(), unknown: false }));
        m.length = Math.max(m.length, length || 0.0);
        for (const x of pySplit(towards ?? "")) m.towards.add(x);
        if (!usable && !through) m.unknown = true;
      }
    }
    for (const [st, v] of merged) {
      const l = [...v.values()];
      this.tracks.set(st, l);
      this.roman.set(st, l.length);
      this.longest.set(st, l.length ? Math.max(...l.map((x) => x.length)) : 0.0);
    }
    for (const t of pkg.trains as Train[]) this.length.set(t.no, trainLength(t));
    const turn = (pkg.rules.headways?.turnaround_min ?? {}) as Record<string, number>;
    const T = new Map<string, Train>((pkg.trains as Train[]).map((t) => [t.no, t]));
    for (const [a, b, kind] of dutyLinks(pkg)) {
      let m = (turn[`${kind}:${this.type.get(b)}`] ?? 1.0) * 60;
      const printed = pyMod(ends(T.get(b)!)[0][1]! - ends(T.get(a)!)[1][1]!, DAY);
      m = Math.min(m, printed);
      getOr(this.dutyPrev, b, () => []).push([a, m]);
      this.linkGap.set(K(a, b), [printed, ends(T.get(a)!)[1][1]!, ends(T.get(b)!)[0][1]!]);
      getOr(this.dutyNext, a, () => []).push([b, m]);
    }
    for (const x of pkg.rules.fit_exemptions ?? []) this.exempt.add(K(x.train_no, x.station));
    for (const x of pkg.rules.headways?.station_exemptions ?? []) this.exempt.add(K(x[0], x[1]));
    for (const d of pkg.dinas_tutup) {
      for (const [st, h] of Object.entries((d.hours ?? {}) as Record<string, { closed_from: string; closed_to: string }>)) {
        getOr(this.closed, st, () => []).push([hhmmS(h.closed_from), hhmmS(h.closed_to)]);
      }
    }
  }

  // ---- helpers
  /** lower = higher priority: regular before KA Dinas, the number, regular before facultative, the number string */
  prio(no: string): Prio {
    let p = this.prioCache.get(no);
    if (!p) {
      const m = /^\D*(\d+)/.exec(no);
      p = [this.type.get(no) === "dinas", m ? parseInt(m[1], 10) : 1e6, this.facultative.get(no) ?? false, no];
      this.prioCache.set(no, p);
    }
    return p;
  }

  byPrio = (a: string, b: string): number => cmp(this.prio(a), this.prio(b));

  outranks(a: string, b: string): boolean {
    const pa = this.prio(a), pb = this.prio(b);
    if (pa[0] !== pb[0]) return !pa[0] && pb[0];
    if (pb[1] - pa[1] >= this.prioGap) return true;
    return pa[1] === pb[1] && !pa[2] && pb[2];
  }

  worthHolding(c: Conflict, d: number, run: number): boolean {
    if (!this.local || c.length < 4 || c[3] === null || c[3] === undefined) return true;
    const hk = K(this.skip, c[1]);
    if (this.noHold.has(hk)) return false;
    const gain = Math.max(c[0] - d, 0.0);
    const hold = Math.max(d + run + this.dispatcherGap - c[3], 0.0);
    const limit = gain * (this.outranks(this.skip!, c[1]!) ? this.displaceRatio : 1.0);
    if (hold <= limit) {
      this.holdGain.set(hk, (this.holdGain.get(hk) ?? 0.0) + gain);
      return true;
    }
    return false;
  }

  fitForced(st: string, other: string, length: number | null): boolean {
    const lo = this.longest.get(st);
    if (!lo || !length || length <= lo) return false;
    const ol = this.length.get(other);
    return ol === null || ol === undefined || ol <= lo;
  }

  canDisplace(other: string | null): boolean {
    if (!(this.displace && this.scheduled.has(this.skip!) && other !== null && this.scheduled.has(other) &&
      other !== this.skip)) return false;
    if (this.tail) return false;
    if ((this.dutyNext.get(this.skip!) ?? []).some(([n]) => n === other)) return false;
    if (this.chainRule && this.root !== null && this.skip !== this.root) return this.outranks(this.skip!, other);
    return !this.outranks(other, this.skip!);
  }

  windowConflict(a: string, b: string, d: number, arr: number): Conflict | null {
    if (!this.windows.length) return null;
    const route = this.tl.sheetRoute(a, b, 60) || [a, b];
    const legs = new Set<string>();
    for (let i = 0; i + 1 < route.length; i++) legs.add(K(route[i], route[i + 1]));
    for (const w of this.windows) {
      const hit = legs.has(K(w.a, w.b)) || legs.has(K(w.b, w.a));
      if (!hit) continue;
      if (w.direction) {
        const [x, y] = w.direction.split("->");
        if (!legs.has(K(x, y))) continue;
      }
      for (const sh of [-DAY, 0, DAY]) {
        const s0 = w.start + sh, s1 = w.end + sh;
        if (d < s1 && arr > s0) return [s1, null, "window"];
      }
    }
    return null;
  }

  readyTime(no: string, near: number): number {
    let ready: number | null = null;
    for (const [prev, m] of this.dutyPrev.get(no) ?? []) {
      const rows = this.tl.trains.get(prev);
      if (!rows || !rows.length) continue;
      const [gap, aOld, dOld] = this.linkGap.get(K(prev, no))!;
      const delta = pyMod(rows[rows.length - 1][2] - aOld + 43200, DAY) - 43200;
      ready = Math.max(ready || 0, dOld - gap + delta + m);
    }
    return ready !== null ? ready : near;
  }

  isClosed(st: string, t: number): boolean {
    for (const [a, b] of this.closed.get(st) ?? []) {
      const x = pyMod(t, DAY);
      if ((a <= b && a <= x && x < b) || (a > b && (x >= a || x < b))) return true;
    }
    return false;
  }

  canWait(st: string, t0: number, t1: number, length: number | null, towards: string | null = null): boolean {
    if (this.kind.get(st) === "halt" || !this.roman.get(st) || this.isClosed(st, t0)) return false;
    return this.stationFree(st, t0, t1, length, towards) === null;
  }

  /** null if T can be at st from t0 to t1 next to the others; else the trains present [arr, dep, train, next] */
  stationFree(st: string, t0: number, t1: number, length: number | null, towards: string | null = null):
    [Num, Num, string, string | null][] | null {
    if (this.kind.get(st) === "halt" || !this.roman.get(st)) return null;
    const others: [Num, Num, string, string | null, boolean][] = [];
    for (const [a, d, no, , nxt] of this.tl.window<PointEntry>(st, t0, t1, true)) {
      if (a <= t1 && d - this.tol >= t0 && no !== this.skip && !this.ignore.has(no)) {
        others.push([a, d - this.tol, no, nxt, d - a >= 60]);
      }
    }
    if (!others.length) return null;
    const tracks = this.tracks.get(st)!;
    const fits = (tr: Track, ln: number | null, tw: string | null): boolean => {
      if (ln && tr.length && ln > tr.length) return false;
      return tr.unknown || !tw || !tr.towards.size || tr.towards.has(tw) || !tracks.some((x) => x.towards.has(tw));
    };
    const exempt = this.exempt.has(K(this.skip, st));
    const moments = [...new Set(others.map((o) => Math.max(o[0], t0)))].sort((x, y) => x - y);
    for (const x of moments) {
      const present: [number | null, string | null][] = [];
      for (const [a, d, no, nxt, sd] of others) if (a <= x && x <= d) present.push([sd ? this.length.get(no) ?? null : null, nxt]);
      const need: [number | null, string | null][] = [...present, [exempt || t1 - t0 < 60 ? null : length, towards]];
      const score = need.map((q) => tracks.reduce((s, tr) => s + (fits(tr, q[0], q[1]) ? 1 : 0), 0));
      const order = need.map((_, i) => i).sort((i, j) => score[i] - score[j]);
      const free = [...tracks];
      for (const i of order) {
        const [ln, tw] = need[i];
        const ok = free.filter((tr) => fits(tr, ln, tw));
        if (!ok.length) return others.filter((o) => o[0] <= x && x <= o[1]).map((o) => [o[0], o[1], o[2], o[3]]);
        let best = ok[0];
        for (const tr of ok) if (tr.length < best.length) best = tr;    // stable sort by length: first shortest
        free.splice(free.indexOf(best), 1);
      }
    }
    return null;
  }

  release(rows: PRow[], i: number, j: number, d: number, run: number, stay: number, length: number | null,
    nxtAfter: string | null): [number, string | null] | null {
    const st = rows[j][0];
    if (this.isClosed(st, d + run)) return null;
    let arr = d + run;
    let freed = false;
    for (let n = 0; n < 30; n++) {
      const busy = this.stationFree(st, arr, arr + stay, length, nxtAfter);
      if (!busy || !busy.length) { freed = true; break; }
      arr = Math.min(...busy.map((b) => b[1])) + 1;
    }
    if (!freed) return null;
    if (arr <= d + run + 1) return null;
    const busyFirst = this.stationFree(st, d + run, d + run + stay, length, nxtAfter) || [];
    let lead: [Num, Num, string, string | null] | null = null;
    for (const o of busyFirst) if (lead === null || o[1] < lead[1]) lead = o;
    let nw = arr - run + this.dispatcherGap - 1;
    if (lead && nxtAfter !== null && lead[3] === nxtAfter) nw = Math.max(nw, lead[1] - this.releaseMargin);
    return [Math.max(nw, d + 1), lead ? lead[2] : null];
  }

  recover(train: Train, rows: PRow[], p: number, q: number, run: number, t: number): number {
    if (!this.recovery || this.printed === null) return run;
    const pr = this.printed[p];
    const pd = pr[3] !== null ? pr[3] as number : pr[2] as number | null;
    if (pd === null) return run;
    const late = t - pd;
    if (late <= 60) return run;
    const mn = this.eng.minimum(train, rows, p, q);
    if (mn === null) return run;
    return Math.max(mn < run ? Math.min(run, mn + 0.0) : run, run - late);
  }

  printedRun(p: number, q: number): number | null {
    const a = this.printed![p], b = this.printed![q];
    if (a[3] === null || b[2] === null) return null;
    for (let n = p + 1; n < q; n++) if (this.printed![n][1] === "stop") return null;
    return (b[2] as number) - (a[3] as number);
  }

  runTime(train: Train, rows: PRow[], p: number, q: number): number {
    if (this.printed !== null) {
      let T = this.printedRun(p, q);
      if (T !== null) {
        for (const n of [p, q]) {
          if (this.added.has(n) && rows[n][1] === "stop") {
            const asPass = rows.map((r) => [...r] as PRow);
            asPass[n][1] = "pass";
            const a = this.eng.compose(train, rows, p, q), b = this.eng.compose(train, asPass, p, q);
            if (a !== null && b !== null) T += a - b;
          }
        }
        return T;
      }
    }
    let T = this.eng.compose(train, rows, p, q);
    if (T === null) T = 60.0 * Math.max(1, q - p);
    return T;
  }

  blockConflict(a: string, b: string, d: number, arr: number, mid: [string, string][], at: number | null = null,
    tknots: Knot[] | null = null): Conflict | null {
    at = at === null ? d : at;
    let worst = this.windowConflict(a, b, d, arr);
    const single = mid.length === 1;
    for (const [x, y] of mid) {
      const key = K(x, y);
      const dbl = this.tl.blockDouble.get(key) ?? this.tl.blockDouble.get(K(y, x)) ?? false;
      let tLo = 0.0, tHi = 0.0, gMax = 0.0;
      if (dbl) {
        const tk_ = single ? tknots : null;
        const vals: number[] = [];
        if (tk_ && tk_.length) for (const kn of tk_) vals.push(kn[1], kn[2]);
        tLo = tk_ && tk_.length ? Math.min(0.0, ...vals) : 0.0;
        tHi = tk_ && tk_.length ? Math.max(arr - d, ...vals) : arr - d;
        gMax = Math.max(this.doubleGap, this.overtakeGap.double, 0.0);
      }
      let tpts: [number[], number[]] | null = null;
      for (const [e1, x1, no, stood] of this.tl.window<BlockEntry>(key, d - 7200, arr + 7200)) {
        if (x1 < d - 7200 || e1 > arr + 7200 || no === this.skip || this.ignore.has(no)) continue;
        if (dbl) {
          const f = single ? (this.tl.bpts.get(key) ?? [0.0, 1.0]) : [0.0, 1.0];
          const run = arr - d;
          const tl_ = this.tol;
          let ok = this.tl.knots.get(K(x, y, no)) ?? null;
          if (ok && ok.length && Math.abs(ok[ok.length - 1][1] - (x1 - e1)) > 1) ok = null;
          let oLo: number, oHi: number;
          if (ok && ok.length) {
            const vals: number[] = [];
            for (const kn of ok) vals.push(kn[1], kn[2]);
            oLo = Math.min(0.0, ...vals);
            oHi = Math.max(x1 - e1, ...vals);
          } else {
            oLo = 0.0; oHi = x1 - e1;
          }
          if (d >= e1 + oHi + gMax - tLo - tl_ + 1e-6 || e1 + oLo >= d + tHi + this.doubleGap - tl_ + 1e-6) continue;
          const tk = single ? tknots : null;
          const pc = getOr(this.tl.ptimes, no, () => new Map<string, [number[], number[]]>());
          const pkey = K(x, y, x1 - e1, single);
          let pt: [number[], number[]] | undefined = pc.get(pkey);
          if (!pt) {
            pt = [f.map((g) => knotTime(ok, g, x1 - e1, false)), f.map((g) => knotTime(ok, g, x1 - e1, true))];
            pc.set(pkey, pt);
          }
          const oA = pt[0].map((v) => e1 + v), oC = pt[1].map((v) => e1 + v);
          if (tpts === null) tpts = [f.map((g) => knotTime(tk, g, run, false)), f.map((g) => knotTime(tk, g, run, true))];
          const [tA, tC] = tpts;
          const n = f.length - 1;
          const g_ = this.doubleGap;
          let needT = -Infinity;
          for (let k = 0; k < n; k++) needT = Math.max(needT, oC[k + 1] + g_ - tA[k]);
          const ab = (x1 - e1) < run - 30 ? this.approachBlocks : 0;
          let ahead = true;
          for (let k = 0; k < n; k++) {
            if (!(oA[Math.max(k - ab, 0)] >= (d + tC[k + 1]) + g_ - tl_)) { ahead = false; break; }
          }
          const H = single ? this.tl.blockHmin.get(key) : undefined;
          if (H !== undefined) {
            let m2 = -Infinity;
            for (let k = 0; k <= n; k++) m2 = Math.max(m2, oC[k] + H - tA[k]);
            needT = Math.min(needT, m2);
            if (!ahead) {
              let all = true;
              for (let k = 0; k <= n; k++) if (!(oA[k] >= (d + tC[k]) + H - tl_)) { all = false; break; }
              ahead = all;
            }
          }
          if (e1 >= at - tl_ && n) needT = Math.max(needT, oC[1] + this.overtakeGap.double);
          const behind = d >= needT - tl_;
          if (!(ahead || behind)) {
            const kind = x1 < arr ? "overtake" : "follow";
            if (worst === null || needT > worst[0]) worst = [needT, no, kind, e1];
          }
        } else {
          const followO = stood && e1 >= d ? 0 : this.singleFollow;
          if (!(d >= x1 + this.singleFollow - this.tol || e1 >= arr + followO - this.tol)) {
            const nw = x1 + (e1 > d - 1 ? this.overtakeGap.single : this.singleFollow);
            const kind = e1 >= d ? "overtake" : "follow";
            if (worst === null || nw > worst[0]) worst = [nw, no, kind, e1];
          }
        }
      }
      if (dbl) continue;
      for (const [e2, x2, no] of this.tl.window<BlockEntry>(K(y, x), d - 7200, arr + 7200)) {
        if (x2 < d - 7200 || e2 > arr + 7200 || no === this.skip || this.ignore.has(no)) continue;
        if (!(d >= x2 + this.singleCross - this.tol || e2 >= arr + this.singleCross - this.tol)) {
          const nw = x2 + this.singleCross;
          if (worst === null || nw > worst[0]) worst = [nw, no, "cross", e2];
        }
      }
    }
    return worst;
  }

  tKnots(tr: Train, rows: PRow[], i: number, j: number, run: number): Knot[] | null {
    if (j <= i + 1) return null;
    const ctx = new Set(rows.map((r) => r[0]));
    const cum = [0.0];
    for (let k = i; k < j; k++) cum.push(cum[cum.length - 1] + (this.tl.km(rows[k][0], rows[k + 1][0], ctx) || 1.0));
    const L = cum[cum.length - 1] || 1.0;
    const out: Knot[] = [[0.0, 0.0, 0.0]];
    for (let n = i + 1; n < j; n++) {
      const sub = rows[n][1] === "halt" ? this.runTime(tr, rows, i, n) : null;
      const x = sub ? sub : run * (n - i) / (j - i);
      out.push([cum[n - i] / L, x, x]);
    }
    out.push([1.0, run, run]);
    return out;
  }

  holdAtStop(rows: PRow[], idx: number[], k: number, other: string | null, hold: Map<number, number>,
    holdWhy: Map<number, Conflict>, why: Conflict, length: number | null): boolean {
    const oRows = other === null ? undefined : this.tl.trains.get(other);
    if (!oRows || !oRows.length) return false;
    const oAt = new Map<string, number>();
    oRows.forEach((r, n) => oAt.set(r[0], n));
    for (let z = k - 1; z >= 0; z--) {
      const n = idx[z];
      const r = rows[n];
      if (!(r[1] === "stop" || r[1] === "terminal") || hold.has(n) || !oAt.has(r[0])) continue;
      const q = oAt.get(r[0])!;
      const nxt = oRows.slice(q + 1).filter((x) => this.kind.get(x[0]) !== "halt");
      if (!nxt.length) return false;
      let ref = nxt[0][2] + this.overtakeGap.double;
      const cs = rows[idx[k]][0];
      const tAt = rows[idx[k]][2] !== null ? rows[idx[k]][2] : (r[3] !== null ? r[3] : r[2]);
      const oCsRow = oRows.find((x) => x[0] === cs);
      const oCs = oCsRow ? oCsRow[2] : null;
      const [baseT, near] = oCs !== null ? [oCs, tAt] : [oRows[q][2], r[3] !== null ? r[3] : r[2]];
      if (near !== null) {
        let best = -DAY;
        for (const sh of [-DAY, 0, DAY]) if (Math.abs(baseT + sh - near) < Math.abs(baseT + best - near)) best = sh;
        ref += best;
      }
      let nx: string | null = null;
      for (const x of rows.slice(n + 1)) if (this.kind.get(x[0]) !== "halt") { nx = x[0]; break; }
      const waitThere = tAt !== null ? Math.max((why[0] || 0) - (tAt || 0), 0) : 0;
      if (ref - (r[3] || r[2] || 0) > waitThere + this.holdSlack) return false;
      if (ref <= (r[3] || 0) || !this.canWait(r[0], (r[2] || r[3])!, ref, length, nx)) return false;
      hold.set(n, ref);
      holdWhy.set(n, why);
      return true;
    }
    return false;
  }

  plan(train: Train, dep0: number, length: number | null | undefined = undefined, maxIter = 60, printedTimes = false,
    printedFrom: Train | null = null): [PRow[], Wait[]] {
    this.skip = train.no;
    if (length === undefined || length === null) length = this.length.get(train.no) ?? null;
    dep0 = Math.max(dep0, this.readyTime(train.no, dep0));
    this.printed = null;
    this.added = new Set();
    if (printedTimes) {
      const tlRows = this.tl.fill(printedFrom || train, true);
      this.printed = tlRows && tlRows.length === train.stops.length ? tlRows : null;
    }
    return this.planInner(train, dep0, length, maxIter);
  }

  private planInner(train: Train, dep0: number, length: number | null, maxIter = 60): [PRow[], Wait[]] {
    const base = train.stops.map((r) => [...r] as PRow);
    const dwell = new Map<number, number>();
    base.forEach((r, i) => dwell.set(i, r[1] === "stop" && r[2] !== null && r[3] !== null ? r[3] - r[2] : 0));
    const extraStop = new Set<number>(), forbid = new Set<number>();
    const notes = new Map<string, string>();
    const hold = new Map<number, number>(), holdWhy = new Map<number, Conflict>();
    let rows: PRow[] = [], waits: Wait[] = [];
    for (let it = 0; it < maxIter; it++) {
      const final = it === maxIter - 1;
      rows = base.map((r) => [...r] as PRow);
      for (const i of extraStop) if (rows[i][1] === "pass") rows[i][1] = "stop";
      this.added = new Set([...extraStop].filter((n) => base[n][1] === "pass"));
      const tr: Train = { ...train, notes: Object.fromEntries(notes) };
      const idx: number[] = [];
      rows.forEach((r, i) => { if (this.kind.get(r[0]) !== "halt") idx.push(i); });
      let t = dep0;
      rows[0][2] = null; rows[0][3] = dep0;
      waits = [];
      let restart = false;
      let k = 0;
      while (k < idx.length - 1) {
        const i = idx[k];
        let m = k + 1;
        while (m < idx.length - 1 && rows[idx[m]][1] === "pass" && this.isClosed(rows[idx[m]][0], t)) m++;
        const j = idx[m];
        let run = this.recover(tr, rows, i, j, this.runTime(tr, rows, i, j), t);
        let d = Math.max(t, hold.get(i) ?? t);
        const mid: [string, string][] = [];
        for (let n = k; n < m; n++) mid.push([rows[idx[n]][0], rows[idx[n + 1]][0]]);
        let why: Conflict | null = d > t + 1 ? holdWhy.get(i) ?? null : null;
        const at = rows[i][2] !== null ? rows[i][2]! : t;
        let tknots = this.tKnots(tr, rows, i, j, run);
        const stay = rows[j][1] === "stop" ? dwell.get(j) ?? 0 : 0;
        const nxtAfter = m + 1 < idx.length ? rows[idx[m + 1]][0] : null;
        for (let it2 = 0; it2 < 200; it2++) {
          let c = this.blockConflict(rows[i][0], rows[j][0], d, d + run, mid, at, tknots);
          if (c === null) {
            const rl = this.release(rows, i, j, d, run, stay, length, nxtAfter);
            if (rl === null) break;
            c = [rl[0], rl[1], "release"];
          } else if (this.canDisplace(c[1]) && (base[i][1] === "pass" || ((this.stopDisplace || this.local) &&
            this.outranks(this.skip!, c[1]!))) && this.worthHolding(c, d, run)) {
            const o = c[1]!;
            this.ignore.add(o);
            this.displaced.add(o);
            if (!this.displacedAt.has(o)) this.displacedAt.set(o, rows[i][0]);
            if (!this.displacedBlk.has(o)) this.displacedBlk.set(o, [rows[i][0], rows[j][0]]);
            continue;
          }
          d = Math.max(c[0], d + 1);
          why = why || c;
        }
        if (d > t + 1) {
          const w = why!;
          const st = rows[i][0];
          let arrI = rows[i][2] !== null ? rows[i][2]! : t;
          if (i === 0 || ((rows[i][1] === "stop" || rows[i][1] === "terminal" || extraStop.has(i)) &&
            this.canWait(st, arrI, d, length, rows[j][0]))) {
            waits.push([st, w[1], w[2], d - t]);
            notes.set(st, NOTE[w[2]]);
          } else if (!final && this.prefer_stop && rows[i][1] === "pass" && (w[2] === "overtake" || w[2] === "follow") &&
            (this.tl.blockDouble.get(K(rows[i][0], rows[j][0])) ?? false) &&
            this.holdAtStop(rows, idx, k, w[1], hold, holdWhy, w, length)) {
            restart = true;
            break;
          } else if (rows[i][1] === "pass" && !forbid.has(i) && this.canWait(st, arrI, d, length, rows[j][0])) {
            const prevI = k ? idx[k - 1] : null;
            let lossIn = 0.0;
            if (prevI !== null) {
              const before = this.runTime(tr, rows, prevI, i);
              rows[i][1] = "stop";
              this.added.add(i);
              extraStop.add(i);
              lossIn = this.runTime(tr, rows, prevI, i) - before;
            } else {
              rows[i][1] = "stop";
              this.added.add(i);
              extraStop.add(i);
            }
            notes.set(st, NOTE[w[2]]);
            arrI = t + lossIn;
            rows[i][2] = pyRoundInt(arrI);
            run = this.recover(tr, rows, i, j, this.runTime(tr, rows, i, j), d);
            tknots = this.tKnots(tr, rows, i, j, run);
            d = Math.max(d, arrI + this.minWait);
            for (let it2 = 0; it2 < 200; it2++) {
              const c = this.blockConflict(rows[i][0], rows[j][0], d, d + run, mid, arrI, tknots);
              if (c === null) break;
              d = Math.max(c[0], d + 1);
            }
            waits.push([st, w[1], w[2], d - arrI]);
          } else if (this.displace && (w[2] === "cross" || w[2] === "overtake" || w[2] === "follow") && w[1] &&
            this.fitForced(st, w[1], length) && !(this.dutyNext.get(this.skip!) ?? []).some(([n]) => n === w[1]) &&
            this.scheduled.has(this.skip!) && this.scheduled.has(w[1])) {
            const o = w[1];
            this.ignore.add(o);
            this.displaced.add(o);
            if (!this.displacedAt.has(o)) this.displacedAt.set(o, st);
            if (!this.displacedBlk.has(o)) this.displacedBlk.set(o, [rows[i][0], rows[j][0]]);
            this.forced.add(o);
            restart = true;
            break;
          } else {
            forbid.add(i);
            const prev = idx.slice(0, k).filter((n) => !forbid.has(n) && this.kind.get(rows[n][0]) !== "halt");
            if (!prev.length || final) {
              waits.push([st, w[1], w[2] + " (no place to wait)", d - t]);
            } else {
              const pl = prev[prev.length - 1];
              extraStop.add(pl);
              notes.set(rows[pl][0], NOTE[w[2]]);
              restart = true;
              break;
            }
          }
        }
        rows[i][3] = d;
        const arr = d + run;
        for (let n = i + 1; n < j; n++) {
          const sub = rows[n][1] === "halt" ? this.runTime(tr, rows, i, n) : null;
          const x = d + (sub ? sub : run * (n - i) / (j - i));
          rows[n][2] = rows[n][3] = pyRoundInt(x);
        }
        rows[j][2] = pyRoundInt(arr);
        t = arr + (rows[j][1] === "stop" ? dwell.get(j) ?? 0 : 0);
        if (extraStop.has(j) && rows[j][1] === "stop" && (dwell.get(j) ?? 0) === 0) t = arr + 60;
        rows[j][3] = j < rows.length - 1 ? pyRoundInt(t) : null;
        k = m;
      }
      if (!restart) return [rows, waits];
    }
    return [rows, waits];
  }
}

// =============================================================================================== dispatching
export function holdIndex(P: Planner, o: Train, blk: [string, string] | undefined | null): number {
  if (!blk) return 0;
  const st = o.stops;
  const pos: number[] = [];
  st.forEach((r, k) => { if (r[0] === blk[0] || r[0] === blk[1]) pos.push(k); });
  if (!pos.length) return 0;
  for (let k = Math.min(...pos) - 1; k >= 0; k--) {
    const r = st[k];
    if (P.kind.get(r[0]) !== "halt" && (r[3] !== null || r[2] !== null)) return k;
  }
  return 0;
}

export function planFrom(P: Planner, o: Train, dep: number, pf: Train | null, k: number, printedTimes = true):
  [PRow[], Wait[]] {
  if (!k) return P.plan(o, dep, undefined, 60, printedTimes, pf);
  const st = o.stops;
  const r = st[k];
  const d0 = r[3] !== null ? r[3] : r[2]!;
  const sub: Train = { ...o, stops: st.slice(k).map((x) => [...x] as Row) };
  const [rows, waits] = P.plan(sub, d0, undefined, 60, printedTimes, { ...(pf || o), stops: st.slice(k) });
  const head = st.slice(0, k).map((x) => [...x] as PRow);
  if (rows.length) rows[0][2] = r[2];
  return [[...head, ...rows], waits];
}

export function endTime(rows: readonly (readonly unknown[])[]): number | null {
  for (let i = rows.length - 1; i >= 0; i--) {
    const r = rows[i];
    if (r[2] !== null || r[3] !== null) return (r[2] !== null ? r[2] : r[3]) as number;
  }
  return null;
}

export interface DispatchInfo {
  accepted: boolean; displaced: string[]; held_for: Record<string, [string, string | null]>;
  joined: Record<string, string>; rounds: number; delay_others_s: number; forbidden_holds?: [string, string][];
}
export type Plans = Map<string, [PRow[], Wait[]]>;

/** dispatching re-plan with the downstream check (planner.dispatch) */
export function dispatch(pkg: Pkg, tl: Timeline, P: Planner, train: Train, dep0: number, printedTimes = true,
  printedFrom: Train | null = null, maxTrains = 80, rounds = 8, passes = 3): [Plans, DispatchInfo] {
  const byno = new Map<string, Train>((pkg.trains as Train[]).map((t) => [t.no, t]));
  P.noHold = new Set();
  let out: Plans = new Map(), info!: DispatchInfo;
  for (let ps = 0; ps < passes; ps++) {
    P.holdGain = new Map();
    [out, info] = dispatchOnce(pkg, tl, P, train, dep0, printedTimes, printedFrom, maxTrains, rounds);
    const bad = new Set<string>();
    for (const [hk, gain] of P.holdGain) {
      const [holder, held] = hk.split("\u0000");
      if (!out.has(held) || (info.held_for[held] ?? [null])[0] !== holder) continue;
      const eNew = endTime(out.get(held)![0]), eOld = endTime(byno.get(held)!.stops);
      if (eNew === null || eOld === null) continue;
      const w = P.outranks(holder, held) ? P.displaceRatio : 1.0;
      if ((eNew - eOld) / w > gain + 60) bad.add(hk);
    }
    if (!bad.size) break;
    for (const b of bad) P.noHold.add(b);
  }
  info.forbidden_holds = [...P.noHold].map((s) => s.split("\u0000") as [string, string]).sort(cmp);
  P.noHold = new Set();
  return [out, info];
}

function dispatchOnce(pkg: Pkg, tl: Timeline, P: Planner, train: Train, dep0: number, printedTimes: boolean,
  printedFrom: Train | null, maxTrains: number, rounds: number): [Plans, DispatchInfo] {
  const byno = new Map<string, Train>((pkg.trains as Train[]).map((t) => [t.no, t]));
  const me = train.no;
  const members = new Map<string, [Train, number, Train]>([[me, [train, dep0, printedFrom || train]]]);
  const holdK = new Map<string, number>();
  const heldFor = new Map<string, [string, string | null]>();
  const whyJoined = new Map<string, string>();
  const saved = new Map<string, TRow[] | undefined>();
  let out: Plans = new Map();
  let rnd = 0;
  P.local = true;
  try {
    for (rnd = 0; rnd < rounds; rnd++) {
      for (const no of members.keys()) {
        if (!saved.has(no)) saved.set(no, tl.trains.get(no));
        tl.drop(no);
      }
      const nw = new Map<string, [Train, number, Train]>();
      out = new Map();
      for (const no of [...members.keys()].sort(P.byPrio)) {
        const [t, dep, pf] = members.get(no)!;
        P.displace = true; P.ignore = new Set(); P.displaced = new Set(); P.tail = false;
        P.displacedAt = new Map(); P.forced = new Set(); P.displacedBlk = new Map(); P.root = me;
        const [rows, waits] = planFrom(P, t, dep, pf, holdK.get(no) ?? 0, printedTimes);
        out.set(no, [rows, waits]);
        tl.put(no, rows);
        for (const y of [...P.displaced].sort(P.byPrio)) {
          if (!members.has(y) && !nw.has(y) && members.size + nw.size < maxTrains) {
            const o = byno.get(y)!;
            nw.set(y, [o, o.stops[0][3]!, o]);
            holdK.set(y, holdIndex(P, o, P.displacedBlk.get(y)));
            heldFor.set(y, [no, P.displacedAt.get(y) ?? null]);
            whyJoined.set(y, P.forced.has(y) ? "forced" : "held");
          }
        }
        const end = endTime(rows);
        const sv = saved.get(no);
        const old = sv && sv.length ? sv : byno.get(no)!.stops;
        const oldEnd = endTime(old);
        for (const [nxt, m] of P.dutyNext.get(no) ?? []) {
          if (members.has(nxt) || nw.has(nxt) || end === null || members.size + nw.size >= maxTrains) continue;
          const rn = byno.get(nxt)!.stops;
          let start = rn[0][3];
          const ref = oldEnd !== null ? oldEnd : end;
          if (start === null) continue;
          while (start < ref) start += DAY;
          while (start - DAY >= ref) start -= DAY;
          if (start < end + m) {
            const o = byno.get(nxt)!;
            nw.set(nxt, [o, o.stops[0][3]!, o]);
            whyJoined.set(nxt, "turnaround");
          }
        }
      }
      if (!nw.size) break;
      for (const [k2, v] of nw) members.set(k2, v);
    }
    if (rnd === rounds) rnd = rounds - 1;
  } finally {
    P.local = false; P.displace = false; P.ignore = new Set(); P.displaced = new Set(); P.root = null;
    for (const [no, rows] of saved) {
      if (rows === undefined) tl.drop(no);
      else tl.put(no, rows, false);
    }
  }
  const late = new Map<string, number>();
  for (const [no, [r]] of out) {
    const a = endTime(r), b = endTime(byno.get(no)!.stops);
    if (a !== null && b !== null) late.set(no, a - b);
  }
  const delays: number[] = [];
  for (const [k2, v] of late) if (k2 !== me) delays.push(Math.max(v, 0));
  const info: DispatchInfo = {
    accepted: true,
    displaced: [...out.keys()].filter((n) => n !== me && ["held", "forced"].includes(whyJoined.get(n) ?? "")),
    held_for: Object.fromEntries(heldFor), joined: Object.fromEntries(whyJoined), rounds: rnd + 1,
    delay_others_s: pySum(delays),
  };
  return [out, info];
}

/** the simulator's entry point for one edited scheduled train */
export const planEdit = dispatch;

/** sections whose daily load (trains over the path, from the timeline) exceeds capacity K */
export function capacityWarnings(pkg: Pkg, tl: Timeline): [string, string, number, number][] {
  const out: [string, string, number, number][] = [];
  const seqs = [...tl.trains.values()].map((rows) => rows.map((r) => r[0]));
  for (const c of pkg.capacity) {
    const path: string[] = c.path && c.path.length ? c.path : [c.a, c.b];
    if (path.length < 2 || !c.capacity) continue;
    const a = path[0], b = path[path.length - 1];
    let n = 0;
    for (const seq of seqs) {
      const pos = new Map<string, number>();
      seq.forEach((st, i) => pos.set(st, i));
      if (pos.has(a) && pos.has(b) && Math.abs(pos.get(a)! - pos.get(b)!) >= 1) n++;
    }
    if (n > c.capacity) out.push([a, b, n, c.capacity]);
  }
  return out;
}

function format(tpl: string, vals: Record<string, string | number>): string {
  return tpl.replace(/\{(\w+)\}/g, (m, k) => (k in vals ? String(vals[k]) : m));
}

/** Planner.outranks for the texts (no timeline needed) */
export function pOutranks(pkg: Pkg, a: string, b: string): boolean {
  const T = new Map<string, Train>((pkg.trains as Train[]).map((t) => [t.no, t]));
  const ep = new Map<string, string>(((pkg.rules.engine_params ?? []) as { param: string; value: string }[])
    .map((x) => [x.param, x.value]));
  const gap = Math.trunc(parseFloat(ep.get("priority_number_gap") ?? "50"));
  const key = (n: string): [boolean, number, boolean] => {
    const m = /^\D*(\d+)/.exec(n);
    const t = T.get(n);
    return [t?.type === "dinas", m ? parseInt(m[1], 10) : 1e6, !!t?.facultative];
  };
  const ka = key(a), kb = key(b);
  if (ka[0] !== kb[0]) return !ka[0] && kb[0];
  return kb[1] - ka[1] >= gap || (ka[1] === kb[1] && !ka[2] && kb[2]);
}

/** user-facing sentences (texts msg.*) for a plan */
export function explain(pkg: Pkg, trainNo: string, waits: Wait[], lang: "id" | "en" = "id",
  info: Partial<DispatchInfo> | null = null, P?: Planner): string[] {
  const tx = pkg.texts as Record<string, Record<string, string>>;
  const name = (c: string | null) => (c && (pkg.stations[c]?.display_name || pkg.stations[c]?.name)) || c;
  const out: string[] = [];
  for (const [st, other, kind, sec] of waits) {
    const k = kind.split(" ")[0];
    if (sec < 30 || !(`msg.${k}` in tx)) continue;
    out.push(format(tx[`msg.${k}`][lang], { train: trainNo, other: other || "-", station: name(st)!,
      minutes: pyRoundInt(sec / 60) }));
  }
  if (info && info.accepted) {
    const pl = P ?? null;
    for (const other of info.displaced ?? []) {
      const [by, st] = (info.held_for ?? {})[other] ?? [trainNo, null];
      const outr = pl ? pl.outranks(by, other) : pOutranks(pkg, by, other);
      out.push(format(tx[outr ? "msg.displaced" : "msg.displaced_equal"][lang],
        { train: by, other, station: name(st) || st || "-" }));
    }
  }
  return out;
}

export interface ReplanStats {
  trains: number; gapeka_waits: number; planner_waits: number; same_station: number; same_station_and_train: number;
  departure_within_1min: number; extra_waits: number; no_wait_trains_clean: number; end_within_2min: number;
  end_mean_abs_min: number;
}

/** re-planning test (planner.replan_test): every train planned again from its printed departure */
export function replanTest(pkg: Pkg, types = ["intercity", "commuter", "krl"], physics = false,
  onTrain?: (no: string, rows: PRow[], waits: Wait[]) => void): Record<string, ReplanStats> {
  const tl = new Timeline(pkg);
  const P = new Planner(pkg, tl);
  const gwait = new Map<string, Map<string, [string, string | null]>>();
  for (const w of pkg.waits) {
    if (w.waiter) {
      const other = (w.trains as (string | null)[]).filter((n) => n && n !== w.waiter);
      getOr(gwait, w.waiter, () => new Map()).set(K(w.station, other.length ? other[0] : null),
        [w.station, other.length ? other[0] : null]);
    }
  }
  const stats = new Map<string, Record<string, number>>();
  for (const t of pkg.trains as Train[]) {
    if (!types.includes(t.type) || !tl.trains.has(t.no)) continue;
    const G = gwait.get(t.no) ?? new Map();
    const gst = new Set([...G.values()].map(([st]) => st));
    const stops = t.stops.map((r) => [...r] as Row);
    for (const r of stops) if (gst.has(r[0]) && r[1] === "stop" && r[2] !== null && r[3] !== null) r[3] = r[2] + 60;
    const [rows, waits] = P.plan({ ...t, stops, notes: {} }, stops[0][3]!, undefined, 60, !physics, t);
    onTrain?.(t.no, rows, waits);
    const Pw = new Map<string, [string, string | null]>();
    for (const w of waits) if (w[3] >= 30) Pw.set(K(w[0], w[1]), [w[0], w[1]]);
    const pst = new Set([...Pw.values()].map(([st]) => st));
    const s = getOr(stats, t.type, () => ({}) as Record<string, number>);
    const add = (k2: string, v: number) => { s[k2] = (s[k2] ?? 0.0) + v; };
    add("trains", 1);
    add("g_waits", G.size);
    add("p_waits", Pw.size);
    add("station_hits", [...gst].filter((x) => pst.has(x)).length);
    add("pair_hits", [...G.keys()].filter((x) => Pw.has(x)).length);
    const pdep = new Map(rows.map((r) => [r[0], r[3]]));
    const gdep = new Map(t.stops.map((r) => [r[0], r[3]]));
    add("dep_within_1", [...gst].filter((st) => pdep.get(st) != null && gdep.get(st) != null &&
      Math.abs(pdep.get(st)! - gdep.get(st)!) <= 60).length);
    add("no_wait_trains", G.size ? 0 : 1);
    add("no_wait_trains_clean", !G.size && !Pw.size ? 1 : 0);
    const endP = rows[rows.length - 1][2], endG = t.stops[t.stops.length - 1][2];
    if (endP !== null && endG !== null) {
      add("end_n", 1);
      add("end_abs_min", Math.abs(endP - endG) / 60);
      add("end_within_2", Math.abs(endP - endG) <= 120 ? 1 : 0);
    }
  }
  const out: Record<string, ReplanStats> = {};
  for (const [typ, s] of stats) {
    const g = (k2: string) => s[k2] ?? 0.0;
    out[typ] = {
      trains: Math.trunc(g("trains")), gapeka_waits: Math.trunc(g("g_waits")), planner_waits: Math.trunc(g("p_waits")),
      same_station: g("station_hits") / Math.max(g("g_waits"), 1),
      same_station_and_train: g("pair_hits") / Math.max(g("g_waits"), 1),
      departure_within_1min: g("dep_within_1") / Math.max(g("g_waits"), 1),
      extra_waits: (g("p_waits") - g("station_hits")) / Math.max(g("p_waits"), 1),
      no_wait_trains_clean: g("no_wait_trains_clean") / Math.max(g("no_wait_trains"), 1),
      end_within_2min: g("end_within_2") / Math.max(g("end_n"), 1),
      end_mean_abs_min: g("end_abs_min") / Math.max(g("end_n"), 1),
    };
  }
  return out;
}
