import { pathToFileURL } from "node:url";
import path from "node:path";
import fs from "node:fs";

const ROOT = path.resolve(path.dirname(new URL(import.meta.url).pathname), "../..");

export async function resolve(specifier, context, next) {
  if (specifier.startsWith("@/")) {
    const base = path.join(ROOT, specifier.slice(2));
    for (const ext of [".ts", ".tsx", ".mts", ""]) {
      if (fs.existsSync(base + ext) && fs.statSync(base + ext).isFile()) {
        return { url: pathToFileURL(base + ext).href, shortCircuit: true };
      }
    }
  }
  return next(specifier, context);
}