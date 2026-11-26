// Simulator session: one scenario on top of the package (the browser keeps one per tab, inside the Web Worker).
// v1 edits (user, 25 Nov 2026): departure, stops and dwell, vmax, train type, length (cars); no route changes.
// - an edited SCHEDULED train goes through the dispatching re-plan (planner.dispatch): it may hold lower trains when
//   that pays, held trains are re-timed, the unit's next trips follow
// - a train created outside the schedule is planned against everything and never moves a scheduled train
// - apply() makes a result part of the scenario: the planned rows become those trains' timetable (later edits start
//   from them); reset() goes back to GAPEKA
// Running times: an edit of the departure or the dwell keeps the printed running times; an edit of stops, vmax or
// type re-times the whole train by the physics (the package model, unbiased against GAPEKA).
import { capacityWarnings, dispatch, explain, Planner, Timeline, trainLength, type DispatchInfo, type Wait }
  from "./planner.js";
import { type Pkg, type Row, type Train } from "./replay.js";

export type TrainType = "krl" | "commuter" | "intercity" | "freight";

export interface Edit {
  /** new departure from the origin, seconds from 00:00 (or use `late`) */
  depart?: number;
  /** minutes late at the origin (adds to the printed departure) */
  late?: number;
  /** dwell (s) at stop rows, by row index */
  dwell?: Record<number, number>;
  /** stop / pass at station rows, by row index (halts and the ends cannot change) */
  stops?: Record<number, "stop" | "pass">;
  /** vmax (km/h) for every section, or per row index (the section after that row) */
  vmax?: number | Record<number, number>;
  type?: TrainType;
  /** cars (passenger / multiple unit) or wagons (freight) */
  cars?: number;
}

export interface NewTrain {
  no: string;
  name?: string;
  type: TrainType;
  /** station codes in order, every station / halt on the way (route() gives them) */
  route: string[];
  depart: number;
  /** stations where it stops -> dwell (s); the others are passed (halts too unless listed) */
  stops: Record<string, number>;
  vmax: number | number[];
  cars?: number;
}

export interface PlanResult {
  /** filled: every row timed (passes interpolated like the timeline), for drawing */
  trains: { no: string; rows: Row[]; waits: Wait[]; late_s: number | null; filled: [string, string, number, number][] }[];
  info: Partial<DispatchInfo> & { edited: string; physics: boolean };
  texts: string[];
}

const RUN_CLASS: Record<TrainType, string> = { krl: "krl", commuter: "commuter", intercity: "intercity",
  freight: "freight" };

function cloneTrain(t: Train): Train {
  return { ...t, stops: t.stops.map((r) => [...r] as Row), notes: { ...(t.notes ?? {}) },
    consist: t.consist ? { ...(t.consist as object) } : t.consist };
}

function endOf(rows: Row[]): number | null {
  for (let i = rows.length - 1; i >= 0; i--) {
    const r = rows[i];
    if (r[2] !== null || r[3] !== null) return (r[2] !== null ? r[2] : r[3]) as number;
  }
  return null;
}

export class Session {
  /** the scenario's package view: trains replaced by their scenario version (others shared with the package) */
  pkg: Pkg;
  tl: Timeline;
  P: Planner;
  private gapeka: Map<string, Train>;
  private created = new Set<string>();
  private drafts = new Map<string, Train>();     // created trains planned but not applied yet

  constructor(public base: Pkg) {
    this.gapeka = new Map((base.trains as Train[]).map((t) => [t.no, t]));
    this.pkg = { ...base, trains: [...base.trains] };
    this.tl = new Timeline(this.pkg);
    this.P = new Planner(this.pkg, this.tl);
  }

  reset(): void {
    this.pkg = { ...this.base, trains: [...this.base.trains] };
    this.created.clear();
    this.drafts.clear();
    this.tl = new Timeline(this.pkg);
    this.P = new Planner(this.pkg, this.tl);
  }

  train(no: string): Train | undefined {
    return (this.pkg.trains as Train[]).find((t) => t.no === no);
  }

  /** the edited train (not planned yet) and whether it needs the physics */
  applyEdit(t0: Train, e: Edit): { train: Train; dep: number; physics: boolean } {
    const t = cloneTrain(t0);
    let physics = false;
    const rows = t.stops;
    const dep0 = rows[0][3] as number;
    for (const [i, dw] of Object.entries(e.dwell ?? {})) {
      const r = rows[+i];
      if (r && r[1] === "stop" && r[2] !== null) r[3] = (r[2] as number) + Math.max(0, dw);
    }
    for (const [i, k] of Object.entries(e.stops ?? {})) {
      const n = +i, r = rows[n];
      if (!r || n === 0 || n === rows.length - 1 || r[1] === "halt" || r[1] === k) continue;
      if (this.tl.kind.get(r[0]) === "halt") continue;
      r[1] = k;
      if (k === "pass") r[2] = r[3] = null;
      else { r[2] = 0; r[3] = 60; }               // a new stop: 1 min (dwell from arr / dep; the planner times it)
      physics = true;
    }
    if (e.vmax !== undefined) {
      if (typeof e.vmax === "number") rows.forEach((r, i) => { if (i < rows.length - 1) r[4] = e.vmax as number; });
      else for (const [i, v] of Object.entries(e.vmax)) if (rows[+i] && +i < rows.length - 1) rows[+i][4] = v;
      physics = true;
    }
    if (e.type && e.type !== t.type) {
      t.type = e.type;
      t.run_class = RUN_CLASS[e.type];
      physics = true;
    }
    if (e.cars !== undefined) t.consist = { ...((t.consist as object) ?? {}), cars: e.cars };
    const dep = e.depart ?? dep0 + (e.late ?? 0) * 60;
    return { train: t, dep, physics };
  }

  /** re-plan an edited scheduled train (dispatching re-plan); the scenario is unchanged until apply() */
  edit(no: string, e: Edit, lang: "id" | "en" = "id"): PlanResult {
    const t0 = this.train(no);
    if (!t0) throw new Error(`no train ${no}`);
    const { train, dep, physics } = this.applyEdit(t0, e);
    const keep = this.prepare(train);
    try {
      const [plans, info] = dispatch(this.pkg, this.tl, this.P, train, dep, !physics, physics ? null : t0);
      const out = this.result(plans, train.no, lang, { ...info, edited: no, physics });
      return out;
    } finally {
      keep();
    }
  }

  /** plan a train created outside the schedule: it waits for every scheduled train and never moves one */
  create(nt: NewTrain, lang: "id" | "en" = "id"): PlanResult {
    if (this.gapeka.has(nt.no) || this.created.has(nt.no)) throw new Error(`train ${nt.no} exists`);
    const n = nt.route.length;
    const stops: Row[] = nt.route.map((st, i) => {
      const vmax = i < n - 1 ? (typeof nt.vmax === "number" ? nt.vmax : nt.vmax[i]) : null;
      const isHalt = this.tl.kind.get(st) === "halt";
      // listed stations are stops (a listed halt: kind 'halt'); every other station and halt is passed
      const kind: string = i === 0 || i === n - 1 ? "terminal" : st in nt.stops ? (isHalt ? "halt" : "stop") : "pass";
      const dw = nt.stops[st] ?? 0;
      return [st, kind, kind === "stop" ? 0 : null, kind === "stop" ? dw : i === 0 ? nt.depart : null, vmax, "simulated"];
    });
    const train: Train = { no: nt.no, name: nt.name ?? nt.no, type: nt.type, run_class: RUN_CLASS[nt.type],
      facultative: false, stops, consist: nt.cars ? { cars: nt.cars } : null, created: true };
    this.drafts.set(train.no, train);
    const keep = this.prepare(train);
    try {
      this.P.displace = false;
      this.P.ignore = new Set();
      this.P.displaced = new Set();
      const [rows, waits] = this.P.plan(train, nt.depart, undefined, 60, false, null);
      return this.result(new Map([[train.no, [rows, waits]]]), train.no, lang,
        { accepted: true, edited: train.no, physics: true, displaced: [], held_for: {} }, train);
    } finally {
      keep();
    }
  }

  /** the scenario's timeline: every train's rows [station, kind, arr, dep] with all times filled (for drawing) */
  timeline(): Record<string, [string, string, number, number][]> {
    return Object.fromEntries(this.tl.trains);
  }

  /** make a result part of the scenario */
  apply(res: PlanResult): void {
    for (const { no, rows } of res.trains) {
      const base = this.train(no) ?? this.drafts.get(no);
      if (!base) continue;
      const g = this.gapeka.get(no);
      const t: Train = { ...base, stops: rows.map((r, i) => {
        const o = g?.stops[i];
        const same = o && o[0] === r[0] && o[2] === r[2] && o[3] === r[3];
        return [r[0], r[1], r[2], r[3], r[4], same ? o![5] : "simulated"] as Row;
      }) };
      const idx = (this.pkg.trains as Train[]).findIndex((x) => x.no === no);
      if (idx >= 0) this.pkg.trains[idx] = t;
      else {
        this.pkg.trains.push(t);
        this.created.add(no);
        this.drafts.delete(no);
      }
      this.tl.put(no, rows);
      this.setTrainAttrs(t);
    }
  }

  /** sections over capacity K in the scenario */
  warnings(): { a: string; b: string; load: number; capacity: number }[] {
    return capacityWarnings(this.pkg, this.tl).map(([a, b, load, capacity]) => ({ a, b, load, capacity }));
  }

  /** station path between two stations over the package profiles (shortest km), halts included */
  route(a: string, b: string): string[] | null {
    const adj = new Map<string, Map<string, number>>();
    for (const r of this.pkg.profiles as [string, string, string, string, number][]) {
      if (r[2]) continue;
      if (!adj.has(r[0])) adj.set(r[0], new Map());
      const m = adj.get(r[0])!;
      if (!m.has(r[1]) || m.get(r[1])! > r[4]) m.set(r[1], r[4]);
    }
    const dist = new Map<string, number>([[a, 0]]);
    const prev = new Map<string, string>();
    const done = new Set<string>();
    while (true) {
      let x: string | null = null, dx = Infinity;
      for (const [k, v] of dist) if (!done.has(k) && v < dx) { x = k; dx = v; }
      if (x === null) return null;
      if (x === b) break;
      done.add(x);
      for (const [y, w] of adj.get(x) ?? []) {
        if (dx + w < (dist.get(y) ?? Infinity)) { dist.set(y, dx + w); prev.set(y, x); }
      }
    }
    const out = [b];
    while (out[out.length - 1] !== a) out.push(prev.get(out[out.length - 1])!);
    return out.reverse();
  }

  // ---- internals
  private setTrainAttrs(t: Train): void {
    this.P.type.set(t.no, t.type);
    this.P.facultative.set(t.no, !!t.facultative);
    this.P.length.set(t.no, trainLength(t));
  }

  /** temporary planner attributes for the train being planned (type, length); returns the undo */
  private prepare(t: Train): () => void {
    const P = this.P;
    const had = { type: P.type.get(t.no), fac: P.facultative.get(t.no), len: P.length.get(t.no),
      has: P.length.has(t.no) };
    P.type.set(t.no, t.type);
    P.facultative.set(t.no, !!t.facultative);
    const c = t.consist as { cars?: number } | null | undefined;
    if (c?.cars) P.length.set(t.no, trainLength(t));
    return () => {
      if (had.type === undefined) P.type.delete(t.no); else P.type.set(t.no, had.type);
      if (had.fac === undefined) P.facultative.delete(t.no); else P.facultative.set(t.no, had.fac);
      if (!had.has) P.length.delete(t.no); else P.length.set(t.no, had.len ?? null);
    };
  }

  private result(plans: Map<string, [Row[], Wait[]]>, me: string, lang: "id" | "en",
    info: PlanResult["info"], created?: Train): PlanResult {
    const trains: PlanResult["trains"] = [];
    for (const [no, [rows, waits]] of plans) {
      const g = this.gapeka.get(no) ?? created;
      const a = endOf(rows), b = g ? endOf(g.stops) : null;
      const filled = (this.tl.fill({ no, type: "", stops: rows }) ?? []) as [string, string, number, number][];
      trains.push({ no, rows, waits, late_s: a !== null && b !== null && !created ? a - b : null, filled });
    }
    const mine = plans.get(me);
    const texts = mine ? explain(this.pkg, me, mine[1], lang, info, this.P) : [];
    return { trains, info, texts };
  }
}

