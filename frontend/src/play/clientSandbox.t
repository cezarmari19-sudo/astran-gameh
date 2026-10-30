// Tipuri + validare pentru rularea LOCALA (pe telefon) a fisierelor de proiect marcate
// target="client" (vezi CodeStudio.tsx). Functia sanitizeOps de mai jos e o portare
// LINIE CU LINIE a lui sanitize_ops din backend/astran_sandbox/runner.py - ACEEASI
// schema, ACELEASI limite - ca o operatie produsa de un script client sa ajunga la
// scena 3D reala validata exact la fel ca una produsa de un script server.
//
// De ce re-validam AICI (pe telefon), desi scriptul a rulat tot pe telefon, in acelasi
// proces ca UI-ul: WebView-ul ruleaza intr-un context JS SEPARAT (vezi
// ClientScriptSandbox.tsx) - nu avem incredere oarba in ce iese de acolo, la fel cum
// serverul nu are incredere oarba in subprocesul izolat (bwrap) din runner.py.

export type SandboxFile = { path: string; name: string; source: string };

export type RawOp = Record<string, unknown>;

export type CleanOp = {
  op: "create" | "set" | "destroy" | "world" | "material";
  id: string;
  t: number;
  [key: string]: unknown;
};

export type SandboxResult = {
  ok: boolean;
  output: { level: "print" | "warn"; msg: string }[];
  errors: string[];
  ops: CleanOp[];
  duration: number;
  truncated: boolean;
};

// ---------- constante, IDENTICE cu runner.py ----------

const MAX_OPS = 5000;
const OP_KINDS = new Set(["create", "set", "destroy", "world", "material"]);
const SHAPE_TYPES = new Set(["cube", "sphere", "cylinder", "cone", "pyramid"]);
const HEX_COLOR_RE = /^#[0-9a-fA-F]{6}$/;
const ID_RE = /^[A-Za-z0-9_.:-]{1,64}$/;
const MATERIAL_ID_RE = /^m[0-9]{1,4}$/;

function num(v: unknown): number | null {
  if (typeof v !== "number" || !Number.isFinite(v)) return null;
  return v;
}

function bool(v: unknown): boolean | null {
  return typeof v === "boolean" ? v : null;
}

function clamp(v: number, lo: number, hi: number): number {
  return Math.max(lo, Math.min(hi, v));
}

/** Portare exacta a sanitize_ops(raw) din runner.py - vezi acolo pentru comentariile originale. */
export function sanitizeOps(raw: unknown): CleanOp[] {
  if (!Array.isArray(raw)) return [];
  const clean: CleanOp[] = [];

  for (const opRaw of raw.slice(0, MAX_OPS)) {
    if (typeof opRaw !== "object" || opRaw === null) continue;
    const op = opRaw as RawOp;

    const kind = op.op;
    const opId = op.id;
    if (typeof kind !== "string" || !OP_KINDS.has(kind)) continue;
    if (typeof opId !== "string" || !ID_RE.test(opId)) continue;

    const t = num(op.t);
    const item: CleanOp = {
      op: kind as CleanOp["op"],
      id: opId,
      t: clamp(t ?? 0, 0, 60),
    };

    if (kind === "world") {
      const gravity = num(op.gravity);
      if (gravity !== null) item.gravity = clamp(gravity, 0, 200);
      const air = num(op.air);
      if (air !== null) item.air = clamp(air, 0, 5);
      const jumpSpeed = num(op.jump_speed);
      if (jumpSpeed !== null) item.jump_speed = clamp(jumpSpeed, 0, 50);
      const moveSpeed = num(op.move_speed);
      if (moveSpeed !== null) item.move_speed = clamp(moveSpeed, 0, 50);
      clean.push(item);
      continue;
    }

    if (kind === "material") {
      if (!MATERIAL_ID_RE.test(opId)) continue;
      if (typeof op.name === "string") item.name = op.name.slice(0, 64);
      const density = num(op.density);
      if (density !== null) item.density = clamp(density, 0.001, 30);
      const friction = num(op.friction);
      if (friction !== null) item.friction = clamp(friction, 0, 5);
      const bounce = num(op.bounce);
      if (bounce !== null) item.bounce = clamp(bounce, 0, 1.2);
      const liquid = bool(op.liquid);
      if (liquid !== null) item.liquid = liquid;
      if (typeof op.color === "string" && HEX_COLOR_RE.test(op.color)) item.color = op.color.toLowerCase();
      clean.push(item);
      continue;
    }

    for (const axis of ["x", "y", "z"] as const) {
      const n = num(op[axis]);
      if (n !== null) item[axis] = clamp(n, -10000, 10000);
    }
    const scale = num(op.scale);
    if (scale !== null) item.scale = clamp(scale, 0.05, 100);
    if (typeof op.type === "string" && SHAPE_TYPES.has(op.type)) item.type = op.type;
    if (typeof op.color === "string" && HEX_COLOR_RE.test(op.color)) item.color = op.color.toLowerCase();
    if (typeof op.name === "string") item.name = op.name.slice(0, 64);
    if (typeof op.material === "string" && (op.material === "" || MATERIAL_ID_RE.test(op.material))) {
      item.material = op.material;
    }
    for (const key of ["anchored", "collide"] as const) {
      const b = bool(op[key]);
      if (b !== null) item[key] = b;
    }
    for (const axis of ["vx", "vy", "vz"] as const) {
      const n = num(op[axis]);
      if (n !== null) item[axis] = clamp(n, -200, 200);
    }
    clean.push(item);
  }

  clean.sort((a, b) => a.t - b.t); // sortare stabila - Array.prototype.sort e stabil in motoarele moderne (V8/JSC)
  return clean;
}

/** Transforma lista de fisiere target="client" (din raspunsul /run) intr-un singur bundle
 * {name: source} trimis in WebView - acelasi format {name, source} ca luau_bundle_client(). */
export function buildBundle(files: SandboxFile[]): Record<string, string> {
  const bundle: Record<string, string> = {};
  for (const f of files) {
    bundle[f.name] = f.source;
  }
  return bundle;
}

/** Parseaza liniile JSON intoarse de harness (acelasi format {type, msg}/{type:"ops", data}
 * ca parse_output din runner.py) intr-un SandboxResult complet validat. */
export function parseSandboxLines(lines: unknown[]): SandboxResult {
  const result: SandboxResult = { ok: true, output: [], errors: [], ops: [], duration: 0, truncated: false };
  let sawOps = false;

  for (const raw of lines) {
    if (typeof raw !== "object" || raw === null) continue;
    const msg = raw as RawOp;
    const kind = msg.type;

    if (kind === "print" || kind === "warn") {
      if (result.output.length < 200) {
        result.output.push({ level: kind, msg: String(msg.msg ?? "").slice(0, 2000) });
      }
    } else if (kind === "error") {
      if (result.errors.length < 20) result.errors.push(String(msg.msg ?? "").slice(0, 2000));
    } else if (kind === "ops" && !sawOps) {
      sawOps = true;
      const data = msg.data as RawOp | undefined;
      if (data && typeof data === "object") {
        result.ops = sanitizeOps(data.ops);
        const duration = num(data.duration);
        result.duration = clamp(duration ?? 0, 0, 60);
        result.truncated = Boolean(data.truncated);
      }
    }
  }

  result.ok = result.errors.length === 0;
  return result;
}