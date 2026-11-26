// Port of physics_ref.py (running-time physics). Keep it line by line with the Python reference.
import { pyInt, pyRoundInt, pySplit, pySum } from "./py.js";

export const DX = 50.0;
export const V_MIN = 8 / 3.6;

export function physGroup(runClass: string): string {
  return ({ krl: "krl", "commuter-emu": "krl", freight: "freight" } as Record<string, string>)[runClass] ?? "loco/krd";
}

export function trainType(runClass: string): string {
  return runClass === "commuter-emu" ? "commuter" : runClass;
}

/** package profile row: [a, b, variant, context, km, umum, ruling, derived, restrictions, double_track] */
export type ProfileRow = [string, string, string, string, number, number | null, number | null, string,
  [number, number, number, string][], boolean];

export interface Profile {
  variant: string;
  ctx: Set<string>;
  km: number;
  umum: number | null;
  double: boolean;
  ruling: number | null;
  restr: [number, number, number, string][];
}

export class Profiles {
  by = new Map<string, Profile[]>();

  constructor(rows: ProfileRow[]) {
    for (const [a, b, variant, ctx, km, umum, ruling, , restr, dbl] of rows) {
      const k = a + "\u0000" + b;
      let l = this.by.get(k);
      if (!l) this.by.set(k, (l = []));
      l.push({ variant, ctx: new Set(pySplit(ctx)), km, umum, double: dbl, ruling, restr });
    }
  }

  /** the variant whose context holds one of the leg's stations, else the default */
  get(a: string, b: string, ctx: Set<string>): Profile | null {
    const vs = this.by.get(a + "\u0000" + b);
    if (!vs || !vs.length) return null;
    for (const v of vs) if (v.variant && [...v.ctx].some((s) => ctx.has(s))) return v;
    for (const v of vs) if (!v.variant) return v;
    return vs[0];
  }
}

export interface Zone {
  station: string; towards: string; dep_length_m: string | null; arr_length_m: string | null; speed_kmh: string;
  train_types: string; applies_when: string;
}

export interface Leg { cap: number[]; secs: [number, number | null][]; halts: number[] }

/** speed cap per cell (km/h), sections [(km, ruling)], halt cells; null if a pair or a vmax is missing */
export function legCap(profiles: Profiles, zones: Zone[], stations: string[], vmaxes: (number | null)[],
  runClass: string, startRest: boolean, endRest: boolean, haltIdx: number[] = [], first = false,
  last = false): Leg | null {
  const emu = physGroup(runClass) === "krl";
  const typ = trainType(runClass);
  const ctx = new Set(stations);
  const cap: number[] = [];
  const secs: [number, number | null][] = [];
  const halts: number[] = [];
  for (let i = 0; i < stations.length - 1; i++) {
    const pr = profiles.get(stations[i], stations[i + 1], ctx);
    const v = vmaxes[i];
    if (pr === null || !v) return null;
    const km = pr.km;
    const n = Math.max(1, pyRoundInt(km * 1000 / DX));
    const c0 = Math.min(v, pr.umum || v);
    const c: number[] = new Array(n).fill(c0);
    for (const [o0, o1, rv, kind] of pr.restr) {
      if (kind === "platform-track" && !emu) continue;
      const i0 = pyInt(o0 * 1000 / DX);
      const i1 = Math.max(pyInt(o1 * 1000 / DX), i0 + 1);
      for (let j = i0; j < Math.min(i1, n); j++) c[j] = Math.min(c[j], rv);
    }
    for (const x of c) cap.push(x);
    secs.push([km, pr.ruling]);
    if (haltIdx.includes(i + 1)) halts.push(cap.length);
  }
  for (const z of zones) {
    if (!pySplit(z.train_types).includes(typ)) continue;
    for (const [side, L] of [["dep", parseInt(z.dep_length_m || "0", 10)], ["arr", parseInt(z.arr_length_m || "0", 10)]] as
      [string, number][]) {
      const n = Math.min(pyInt(L / DX), cap.length);
      if (!L || !n) continue;
      const [at, nb, rest] = side === "dep" ? [stations[0], stations[1], startRest] :
        [stations[stations.length - 1], stations[stations.length - 2], endRest];
      if (at !== z.station || (z.towards !== "*" && nb !== z.towards)) continue;
      const terminal = side === "dep" ? first : last;
      const w = z.applies_when;
      if ((w === "stop" && !rest) || (w === "pass" && rest) || (w === "midstop" && !(rest && !terminal)) ||
        (w === "terminal" && !(rest && terminal))) continue;
      const sp = parseFloat(z.speed_kmh);
      const [lo, hi] = side === "dep" ? [0, n] : [cap.length - n, cap.length];
      for (let j = lo; j < hi; j++) cap[j] = Math.min(cap[j], sp);
    }
  }
  return { cap, secs, halts };
}

/** running time (s) over the cells */
export function simulate(capKmh: number[], startRest: boolean, endRest: boolean, halts: number[], a0: number,
  power: number, brake: number): number {
  const n = capKmh.length;
  const cap = capKmh.map((c) => c / 3.6);
  const node = new Array<number>(n + 1).fill(0.0);
  node[0] = cap[0];
  for (let j = 1; j < n; j++) node[j] = Math.min(cap[j - 1], cap[j]);
  node[n] = cap[n - 1];
  if (startRest) node[0] = 0.0;
  if (endRest) node[n] = 0.0;
  for (const h of halts) node[Math.min(Math.max(h, 1), n - 1)] = 0.0;
  const v = new Array<number>(n + 1).fill(0.0);
  v[0] = node[0];
  for (let j = 0; j < n; j++) {
    const acc = Math.min(a0, power / Math.max(v[j], 1.0));
    v[j + 1] = Math.min(node[j + 1], Math.sqrt(Math.max(v[j] * v[j] + 2 * DX * acc, V_MIN * V_MIN)));
  }
  for (let j = n - 1; j >= 0; j--) v[j] = Math.min(v[j], Math.sqrt(v[j + 1] * v[j + 1] + 2 * DX * brake));
  const parts: number[] = new Array(n);
  for (let j = 0; j < n; j++) parts[j] = 2 * DX / Math.max(v[j] + v[j + 1], 1e-3);
  return pySum(parts);
}
