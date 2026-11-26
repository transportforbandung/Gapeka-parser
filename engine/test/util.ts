// shared test helpers
/** first difference (path) between two JSON-like values; numbers compared exactly; null if equal */
export function diff(a: unknown, b: unknown, path = ""): string | null {
  if (a === b) return null;
  if (typeof a === "number" && typeof b === "number") return a === b ? null : `${path}: ${a} != ${b}`;
  if (Array.isArray(a) && Array.isArray(b)) {
    if (a.length !== b.length) return `${path}: length ${a.length} != ${b.length}`;
    for (let i = 0; i < a.length; i++) { const d = diff(a[i], b[i], `${path}[${i}]`); if (d) return d; }
    return null;
  }
  if (a && b && typeof a === "object" && typeof b === "object") {
    const ka = Object.keys(a).sort(), kb = Object.keys(b).sort();
    if (ka.join() !== kb.join()) return `${path}: keys ${ka} != ${kb}`;
    for (const k of ka) {
      const d = diff((a as Record<string, unknown>)[k], (b as Record<string, unknown>)[k], `${path}.${k}`);
      if (d) return d;
    }
    return null;
  }
  return `${path}: ${JSON.stringify(a)} != ${JSON.stringify(b)}`;
}

