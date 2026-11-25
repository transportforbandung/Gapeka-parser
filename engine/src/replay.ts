// Port of replay.py: running time of a leg from the package alone (physics + calibration + buffers + corrections),
// and the golden replay of the official GAPEKA. Keep it line by line with the Python reference.
import { legCap, physGroup, Profiles, simulate, type ProfileRow, type Zone } from "./physics.js";
import { pyRound, pySplit, pySum } from "./py.js";

/** stop row: [station, kind, arr s, dep s, vmax of the next section, time origin] */
export type Row = [string, string, number | null, number | null, number | null, string, ...unknown[]];

export interface Train {
  no: string; type: string; run_class?: string; facultative?: boolean; notes?: Record<string, string>;
  stops: Row[]; [k: string]: unknown;
}

// eslint-disable-next-line @typescript-eslint/no-explicit-any
export type Pkg = any;

export const FEATURES = ["sim", "const", "per_halt", "terminus_arrival", "origin_departure", "start_from_stop",
  "stop_at_end", "start_wait_cross", "start_wait_overtaken", "start_pass_cross", "end_wait_cross",
  "end_wait_overtaken", "end_pass_cross", "km_5_10_permil", "km_10plus_permil"];

interface Buffer {
  kind: string; sections: [string, string][]; value: number; types: string[]; station?: string; side?: string;
  when?: string;
}

const pairKey = (a: string, b: string) => a + "\u0000" + b;

export class Engine {
  calib = new Map<string, Map<string, number>>();
  corr = new Map<string, number>();
  profiles: Profiles;
  zones: Zone[];
  phys: Record<string, { a0: number; power: number; brake: number }>;
  buffers: Buffer[] = [];
  private cache = new Map<string, number | null>();

  constructor(public pkg: Pkg) {
    const m = pkg.model;
    for (const c of m.calibration) {
      if (!this.calib.has(c.train_type)) this.calib.set(c.train_type, new Map());
      this.calib.get(c.train_type)!.set(c.feature, c.value);
    }
    for (const [kind, a, b, endKind, t, sec] of m.corrections as [string, string, string, string, string, number][]) {
      const key = kind === "section" ? ["sec", a, b, t] : kind === "halt" ? ["halt", a, t] : [kind, a, endKind, t];
      this.corr.set(key.join("\u0000"), sec);
    }
    this.profiles = new Profiles(pkg.profiles as ProfileRow[]);
    this.zones = m.station_zones;
    this.phys = m.physics;
    for (const b of m.section_buffers) {
      const val = parseFloat(pySplit(String(b.seconds))[0]);
      const secs = pySplit(b.sections).map((x) => x.split("-") as [string, string]);
      this.buffers.push({ kind: b.kind, sections: secs, value: val, types: pySplit(b.train_types) });
    }
    for (const b of m.station_buffers) {
      this.buffers.push({ kind: "station_buffer", sections: [], station: b.station, side: b.side,
        when: b.applies_when, value: parseFloat(b.seconds), types: pySplit(b.train_types) });
    }
  }

  private corrGet(...k: (string | null)[]): number {
    return this.corr.get(k.join("\u0000")) ?? 0.0;
  }

  bufferS(typ: string, st: string[], secs: [number, number | null][], startRest: boolean, endRest: boolean,
    first: boolean, last: boolean): number {
    const both = new Set<string>();
    for (let i = 0; i + 1 < st.length; i++) {
      both.add(pairKey(st[i], st[i + 1]));
      both.add(pairKey(st[i + 1], st[i]));
    }
    let tot = 0.0;
    for (const b of this.buffers) {
      if (!b.types.includes(typ)) continue;
      if (b.kind === "buffer" && b.sections.some(([x, y]) => both.has(pairKey(x, y)))) {
        tot += b.value;
      } else if (b.kind === "buffer_km") {
        const want = new Set(b.sections.flatMap(([x, y]) => [pairKey(x, y), pairKey(y, x)]));
        const kms: number[] = [];
        for (let i = 0; i + 1 < st.length && i < secs.length; i++) {
          if (want.has(pairKey(st[i], st[i + 1]))) kms.push(secs[i][0]);
        }
        tot += b.value * pySum(kms);
      } else if (b.kind === "station_buffer") {
        const [at, rest, term] = b.side === "dep" ? [st[0], startRest, first] : [st[st.length - 1], endRest, last];
        if (at === b.station) {
          const w = b.when;
          if ((w === "terminal" && rest && term) || (w === "stop" && rest) || w === "any") tot += b.value;
        }
      }
    }
    return tot;
  }

  private memo(kind: string, train: Train, rows: Row[], p: number, q: number, fn: () => number | null): number | null {
    const notes = train.notes ?? {};
    const parts: unknown[] = [kind, train.type, train.run_class ?? null, p === 0, q === rows.length - 1,
      notes[rows[p][0]] ?? null, notes[rows[q][0]] ?? null];
    for (let i = p; i <= q; i++) parts.push(rows[i][0], rows[i][1], rows[i][4]);
    const key = JSON.stringify(parts);
    if (this.cache.has(key)) return this.cache.get(key)!;
    if (this.cache.size > 500000) this.cache.clear();
    const v = fn();
    this.cache.set(key, v);
    return v;
  }

  /** technical minimum running time p -> q (s): raw physics + a short dwell per halt */
  minimum(train: Train, rows: Row[], p: number, q: number, dwellS = 20.0): number | null {
    return this.memo("min" + dwellS, train, rows, p, q, () => {
      const rc = train.run_class ?? train.type;
      const st = rows.slice(p, q + 1).map((r) => r[0]);
      const startRest = rows[p][1] !== "pass", endRest = rows[q][1] !== "pass";
      const haltIdx: number[] = [];
      for (let i = p + 1; i < q; i++) if (rows[i][1] === "halt") haltIdx.push(i - p);
      const leg = legCap(this.profiles, this.zones, st, rows.slice(p, q + 1).map((r) => r[4]), rc, startRest,
        endRest, haltIdx, p === 0, q === rows.length - 1);
      if (leg === null) return null;
      const ph = this.phys[physGroup(rc)];
      return simulate(leg.cap, startRest, endRest, leg.halts, ph.a0, ph.power, ph.brake) + dwellS * haltIdx.length;
    });
  }

  /** predicted seconds from timed point p to timed point q (physics + calibration + buffers + corrections) */
  compose(train: Train, rows: Row[], p: number, q: number): number | null {
    return this.memo("compose", train, rows, p, q, () => this.composeRaw(train, rows, p, q));
  }

  private composeRaw(train: Train, rows: Row[], p: number, q: number): number | null {
    const typ = train.type, rc = train.run_class ?? train.type;
    const st = rows.slice(p, q + 1).map((r) => r[0]);
    const first = p === 0, last = q === rows.length - 1;
    const startRest = rows[p][1] !== "pass", endRest = rows[q][1] !== "pass";
    const haltIdx: number[] = [];
    for (let i = p + 1; i < q; i++) if (rows[i][1] === "halt") haltIdx.push(i - p);
    const leg = legCap(this.profiles, this.zones, st, rows.slice(p, q + 1).map((r) => r[4]), rc, startRest, endRest,
      haltIdx, first, last);
    if (leg === null) return null;
    const ph = this.phys[physGroup(rc)];
    const sim = simulate(leg.cap, startRest, endRest, leg.halts, ph.a0, ph.power, ph.brake);
    const halts = rows.slice(p + 1, q).filter((r) => r[1] === "halt").map((r) => r[0]);
    const notes = train.notes ?? {};
    const role = (code: string, rest: boolean): number[] => {
      const s = new Set(pySplit(notes[code] ?? ""));
      return [rest && s.has("x") ? 1.0 : 0.0, rest && s.has("=") ? 1.0 : 0.0,
        !rest && (s.has("x") || s.has("//")) ? 1.0 : 0.0];
    };
    const km5 = pySum(leg.secs.filter(([, g]) => g !== null && 5 <= g && g < 10).map(([km]) => km));
    const km10 = pySum(leg.secs.filter(([, g]) => g !== null && 10 <= g && g < 99).map(([km]) => km));
    const x = [sim, 1.0, halts.length, last ? 1.0 : 0.0, first ? 1.0 : 0.0, startRest ? 1.0 : 0.0,
      endRest ? 1.0 : 0.0, ...role(st[0], startRest), ...role(st[st.length - 1], endRest), km5, km10];
    const c = this.calib.get(typ) ?? this.calib.get("intercity")!;
    let T = pySum(FEATURES.map((f, i) => (c.get(f) ?? 0.0) * x[i]));
    T += this.bufferS(typ, st, leg.secs, startRest, endRest, first, last);
    const secCorr: number[] = [];
    for (let i = 0; i + 1 < st.length; i++) secCorr.push(this.corrGet("sec", st[i], st[i + 1], typ));
    T += pySum(secCorr);
    T += pySum(halts.map((h) => this.corrGet("halt", h, typ)));
    T += this.corrGet("dep", st[0], startRest ? "stop" : "pass", typ);
    T += this.corrGet("arr", st[st.length - 1], endRest ? "stop" : "pass", typ);
    return T;
  }
}

/** indices of the stops with a printed time that a leg can start / end at */
export function timedPoints(rows: Row[]): number[] {
  const out: number[] = [];
  rows.forEach((r, i) => {
    if (r[5] === "printed" && (r[1] === "stop" || r[1] === "terminal" || (r[1] === "pass" && r[3] !== null))) out.push(i);
  });
  return out;
}

export type ReplayRow = [number, number | null, number | null, number?];

/** golden replay: each train from its printed first departure (replay.py replay(), trains part) */
export function replay(pkg: Pkg, types = ["krl", "commuter", "intercity", "freight"], eng = new Engine(pkg)):
  Record<string, ReplayRow[]> {
  const out: Record<string, ReplayRow[]> = {};
  for (const t of pkg.trains as Train[]) {
    if (!types.includes(t.type)) continue;
    const rows = t.stops;
    const tp = timedPoints(rows);
    if (tp.length < 2) continue;
    const o: ReplayRow[] = [];
    let clock: number | null = rows[tp[0]][3];
    o.push([tp[0], null, clock]);
    for (let k = 0; k + 1 < tp.length; k++) {
      const p = tp[k], q = tp[k + 1];
      const T = eng.compose(t, rows, p, q);
      if (T === null || clock === null) {
        clock = null;
        o.push([q, null, null]);
        continue;
      }
      const arr = clock + T;
      const aPr = rows[q][2], dPr = rows[q][3];
      const dep = rows[q][1] === "stop" && aPr !== null && dPr !== null ? arr + (dPr - aPr) :
        (rows[q][1] !== "terminal" ? arr : null);
      o.push([q, pyRound(arr, 1), dep === null ? null : pyRound(dep, 1), pyRound(T, 1)]);
      clock = dep;
    }
    out[t.no] = o;
  }
  return out;
}
