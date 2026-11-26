// Node helpers for tests and tools: load the package / golden files of an edition
import { readFileSync } from "node:fs";
import { gunzipSync } from "node:zlib";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

export const repoRoot = join(dirname(fileURLToPath(import.meta.url)), "..", "..", "..");

export function loadGz(rel: string): any { // eslint-disable-line @typescript-eslint/no-explicit-any
  return JSON.parse(gunzipSync(readFileSync(join(repoRoot, rel))).toString("utf8"));
}
