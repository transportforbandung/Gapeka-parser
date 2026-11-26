// Python float semantics the reference engine relies on, so the browser engine gives the SAME doubles.
// - sum(): CPython >= 3.12 sums floats with Neumaier compensation (builtin sum, not math.fsum)
// - round(x): half to even, returns an integer
// - round(x, n): the exact binary value rounded to n decimals, exact ties to even
// Never use x ** 2 for squares in the reference (pow differs from x * x in the last bit); use x * x on both sides.

/** CPython 3.12+ builtin sum() over floats (Neumaier compensation, compensation added at the end if finite) */
export function pySum(xs: Iterable<number>): number {
  let f = 0.0;
  let c = 0.0;
  for (const x of xs) {
    const t = f + x;
    if (Math.abs(f) >= Math.abs(x)) c += (f - t) + x;
    else c += (x - t) + f;
    f = t;
  }
  if (c !== 0 && Number.isFinite(c)) f += c;
  return f;
}

/** Python round(x) for a float: nearest integer, exact halves to even */
export function pyRoundInt(x: number): number {
  const f = Math.floor(x);
  const d = x - f; // exact for |x| < 2^52
  if (d < 0.5) return f;
  if (d > 0.5) return f + 1;
  return f % 2 === 0 ? f : f + 1;
}

/** Python round(x, nd), nd >= 0 */
export function pyRound(x: number, nd: number): number {
  if (!Number.isFinite(x)) return x;
  // toFixed rounds the exact binary value (ties away from zero); only exact ties differ from Python (to even)
  const exact = x.toFixed(Math.min(100, nd + 60));
  const dot = exact.indexOf(".");
  const tail = exact.slice(dot + 1 + nd);
  if (/^50*$/.test(tail)) {
    // exact tie: truncate and keep the even last digit
    const trunc = exact.slice(0, dot + 1 + nd).replace(/\.$/, "");
    const digits = trunc.replace(/[-.]/g, "");
    const last = Number(digits[digits.length - 1]);
    if (last % 2 === 0) return Number(trunc);
    const step = Math.pow(10, -nd);
    return Number((Number(trunc) + (x < 0 ? -step : step)).toFixed(nd));
  }
  return Number(x.toFixed(nd));
}

/** Python int(x) for a float: toward zero */
export const pyInt = Math.trunc;

/** str.split() (whitespace, no empty parts) */
export function pySplit(s: string | null | undefined): string[] {
  return (s ?? "").split(/\s+/).filter((p) => p.length > 0);
}
