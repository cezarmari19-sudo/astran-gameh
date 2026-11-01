// frontend/src/sandbox/runtime.ts
//
// Sandbox-ul de scripturi CLIENT: WebView (pagina fara retea, CSP strict) -> Worker
// dedicat, nou la fiecare rulare -> Luau compilat in WebAssembly (VM-ul nu are acces la
// JS, la retea sau la fisiere: singurul canal e rezultatul text final).
//
// Straturi de izolare (de la exterior spre interior):
//   1. CSP al paginii-gazda: connect/img/frame/style = none; scripturi doar inline + blob.
//   2. Codul scriptului NU intra niciodata in HTML: ajunge ca STRING prin postMessage,
//      apoi in memoria WASM. Nu exista eval/new Function pe cod de utilizator.
//   3. Worker nou la fiecare rulare, distrus (terminate) la final sau la timeout - singura
//      oprire garantata pentru o bucla care nu ajunge la intreruperile VM-ului.
//   4. In worker, dupa initializarea WASM, API-urile de retea/stocare sunt blocate.
//   5. Limite de timp si memorie impuse de host (clamp aici, interrupt+allocator in WASM).
//   6. Rezultatul este VALIDAT si CURATAT aici (sanitizeOps), identic cu runner.py.
//
// Contractul fisierelor/bundle-ului/rezultatului reproduce backend/astran_sandbox/runner.py,
// ca acelasi script sa se comporte identic pe client si pe server.
import { LUAU_GLUE_JS } from "@/src/sandbox/luauWasm";

// ---------------- tipuri ----------------

export type SandboxFile = { name: string; source: string };
export type SandboxAsset = { id: string; name?: string; object: Record<string, unknown> };

export type SandboxRunOptions = {
  files: SandboxFile[];
  preludeSource: string;
  assets?: SandboxAsset[];
  scene?: unknown;
  capabilities?: string[];
  timeoutMs?: number;
  memMB?: number;
};

export type SandboxOp = { t: number; op: string; id: string; [key: string]: unknown };

export type SandboxResult = {
  ok: boolean;
  output: { level: "print" | "warn"; msg: string }[];
  errors: string[];
  ops: SandboxOp[];
  duration: number;
  truncated: boolean;
};

// ---------------- constante (oglinda runner.py) ----------------

export const ENTRY_NAME = "main";
const MAX_FILES = 32;
const MAX_NAME_CHARS = 64;
const MAX_SOURCE_CHARS = 20000;
const MAX_TOTAL_CHARS = 100000;
const MAX_OPS = 5000;
const MAX_LOG_LINES = 200;
const MAX_ASSETS = 200;
const MAX_SCENE_OBJECTS = 1000;
const MAX_PRELUDE_CHARS = 500000;
const MAX_OUTPUT_CHARS = 1500000;

const ASSETS_FILE_NAME = "__assets";
const SCENE_FILE_NAME = "__scene";
const RESERVED_NAMES = new Set([ASSETS_FILE_NAME, SCENE_FILE_NAME]);

// Singurele capabilitati pe care clientul le poate acorda (core e implicit in host).
const ALLOWED_CAPS = new Set(["assets"]);

const MIN_TIMEOUT_MS = 50;
const MAX_TIMEOUT_MS = 5000;
const DEFAULT_TIMEOUT_MS = 2000;
const MIN_MEM_MB = 8;
const MAX_MEM_MB = 128;
const DEFAULT_MEM_MB = 64;

const HEX_COLOR = /^#[0-9a-fA-F]{6}$/;
const NAME_RE = /^[A-Za-z0-9_-]+(\/[A-Za-z0-9_-]+)*$/;
const ID_RE = /^[A-Za-z0-9_.:-]{1,64}$/;
const MATERIAL_ID_RE = /^m[0-9]{1,4}$/;
const SHAPE_TYPES = new Set(["cube", "sphere", "cylinder", "cone", "pyramid"]);
const OP_KINDS = new Set(["create", "set", "destroy", "world", "material"]);

// ---------------- utilitare ----------------

function isNum(v: unknown): v is number {
  return typeof v === "number" && Number.isFinite(v);
}
function num(v: unknown): number | null {
  return isNum(v) ? v : null;
}
function clamp(v: number, lo: number, hi: number): number {
  return Math.max(lo, Math.min(hi, v));
}
function bool(v: unknown): boolean | null {
  return typeof v === "boolean" ? v : null;
}
function isPlainObject(v: unknown): v is Record<string, unknown> {
  return typeof v === "object" && v !== null && !Array.isArray(v);
}

// Lungimea in octeti UTF-8 (fara TextEncoder, care nu exista pe toate versiunile de RN).
// O jumatate de pereche surrogate izolata devine U+FFFD (3 octeti), exact ca la codificare.
export function utf8Length(s: string): number {
  let n = 0;
  for (let i = 0; i < s.length; i++) {
    const c = s.charCodeAt(i);
    if (c < 0x80) n += 1;
    else if (c < 0x800) n += 2;
    else if (c >= 0xd800 && c <= 0xdbff) {
      const d = i + 1 < s.length ? s.charCodeAt(i + 1) : 0;
      if (d >= 0xdc00 && d <= 0xdfff) { n += 4; i++; } else n += 3;
    } else n += 3;
  }
  return n;
}

// ---------------- fisiere / bundle ----------------

export function validateFiles(files: unknown): SandboxFile[] {
  if (!Array.isArray(files)) throw new Error("files must be a list");
  if (files.length > MAX_FILES) throw new Error(`Too many scripts (max ${MAX_FILES})`);

  const clean: SandboxFile[] = [];
  const seen = new Set<string>();
  let total = 0;
  for (const f of files) {
    if (!isPlainObject(f)) throw new Error("Invalid script entry");
    const name = f.name;
    const source = f.source === undefined ? "" : f.source;
    if (typeof name !== "string" || typeof source !== "string") throw new Error("Invalid script entry");
    if (name.length > MAX_NAME_CHARS || !NAME_RE.test(name)) {
      throw new Error(`Invalid script name '${name.slice(0, 40)}' (use letters, numbers, _ - and /)`);
    }
    if (RESERVED_NAMES.has(name)) throw new Error(`The script name '${name}' is reserved`);
    if (seen.has(name)) throw new Error(`Duplicate script name '${name}'`);
    if (source.length > MAX_SOURCE_CHARS) {
      throw new Error(`Script '${name}' is too long (max ${MAX_SOURCE_CHARS} characters)`);
    }
    seen.add(name);
    total += source.length;
    clean.push({ name, source });
  }
  if (total > MAX_TOTAL_CHARS) {
    throw new Error(`Scripts are too long together (max ${MAX_TOTAL_CHARS} characters)`);
  }
  return clean;
}

function luaStringReturn(payload: string): string {
  // Echivalent cu runner.py: "return " + json.dumps(payload, ensure_ascii=False)
  return "return " + JSON.stringify(payload);
}

function assetsFile(assets: unknown): SandboxFile | null {
  if (!Array.isArray(assets) || assets.length === 0) return null;
  const clean: { id: string; name: string; object: Record<string, unknown> }[] = [];
  for (const a of assets.slice(0, MAX_ASSETS)) {
    if (!isPlainObject(a)) continue;
    const id = a.id;
    const obj = a.object;
    if (typeof id !== "string" || !isPlainObject(obj)) continue;
    clean.push({ id, name: typeof a.name === "string" ? a.name : "", object: obj });
  }
  if (clean.length === 0) return null;
  return { name: ASSETS_FILE_NAME, source: luaStringReturn(JSON.stringify(clean)) };
}

function sceneFile(scene: unknown): SandboxFile | null {
  if (!isPlainObject(scene)) return null;
  const objects = scene.objects;
  if (!Array.isArray(objects) || objects.length === 0) return null;

  const counters: Record<string, number> = Object.create(null);
  const clean: Record<string, unknown>[] = [];
  for (const o of objects.slice(0, MAX_SCENE_OBJECTS)) {
    if (!isPlainObject(o)) continue;
    const oid = o.id;
    const otype = o.type;
    if (typeof oid !== "string" || !ID_RE.test(oid) || typeof otype !== "string") continue;
    if (otype === "spawn") continue;
    counters[otype] = (counters[otype] || 0) + 1;

    const custom = o.name;
    let name = typeof custom === "string" ? custom.replace(/[^\w\- .]/g, "").trim().slice(0, 64) : "";
    if (!name) name = `${otype.charAt(0).toUpperCase()}${otype.slice(1)}`.slice(0, 20) + String(counters[otype]);

    const entry: Record<string, unknown> = { id: oid, name, type: otype.slice(0, 20) };
    const defaults: [string, number][] = [
      ["x", 0], ["y", 0], ["z", 0], ["scale", 1], ["sx", 1], ["sy", 1], ["sz", 1],
    ];
    for (const [key, def] of defaults) {
      const n = num(o[key]);
      entry[key] = n !== null ? n : def;
    }
    const color = o.color;
    entry.color = typeof color === "string" && HEX_COLOR.test(color) ? color.toLowerCase() : "#a3a3a3";
    entry.solid = o.solid !== false;
    entry.visible = o.visible !== false;
    clean.push(entry);
  }
  if (clean.length === 0) return null;
  return { name: SCENE_FILE_NAME, source: luaStringReturn(JSON.stringify(clean)) };
}

// Format identic cu host.cpp: "@@FILE <nume> <nr_octeti_utf8>\n<continut>\n"
export function buildBundle(files: SandboxFile[], assets?: unknown, scene?: unknown): string {
  const all = files.slice();
  const a = assetsFile(assets);
  if (a) all.push(a);
  const s = sceneFile(scene);
  if (s) all.push(s);

  let out = "";
  for (const f of all) {
    out += `@@FILE ${f.name} ${utf8Length(f.source)}\n${f.source}\n`;
  }
  return out;
}

export function buildRunMessage(opts: SandboxRunOptions) {
  if (!opts || typeof opts.preludeSource !== "string" || opts.preludeSource.length === 0) {
    throw new Error("Missing prelude");
  }
  if (opts.preludeSource.length > MAX_PRELUDE_CHARS) throw new Error("Prelude too large");

  const clean = validateFiles(opts.files);
  if (!clean.some((f) => f.name === ENTRY_NAME)) {
    throw new Error(`Missing the '${ENTRY_NAME}' script (it runs first)`);
  }

  const rawTimeout = isNum(opts.timeoutMs) ? opts.timeoutMs : DEFAULT_TIMEOUT_MS;
  const timeoutMs = Math.round(clamp(rawTimeout, MIN_TIMEOUT_MS, MAX_TIMEOUT_MS));
  const rawMem = isNum(opts.memMB) ? opts.memMB : DEFAULT_MEM_MB;
  const memMB = Math.round(clamp(rawMem, MIN_MEM_MB, MAX_MEM_MB));
  // Doar capabilitatile din lista alba; orice altceva e ignorat, nu transmis host-ului.
  const caps = (opts.capabilities ?? []).filter((c) => typeof c === "string" && ALLOWED_CAPS.has(c)).join(",");

  return {
    type: "run" as const,
    prelude: opts.preludeSource,
    bundle: buildBundle(clean, opts.assets, opts.scene),
    timeoutMs,
    memMB,
    caps,
    entry: ENTRY_NAME,
  };
}

// ---------------- rezultat (oglinda runner.py: sanitize_ops + parse_output) ----------------

export function sanitizeOps(raw: unknown): SandboxOp[] {
  if (!Array.isArray(raw)) return [];
  const clean: SandboxOp[] = [];
  for (const op of raw.slice(0, MAX_OPS)) {
    if (!isPlainObject(op)) continue;
    const kind = op.op;
    const opId = op.id;
    if (typeof kind !== "string" || !OP_KINDS.has(kind)) continue;
    if (typeof opId !== "string" || !ID_RE.test(opId)) continue;
    const t = num(op.t);
    const item: SandboxOp = { op: kind, id: opId, t: clamp(t !== null ? t : 0, 0, 60) };

    if (kind === "world") {
      const gravity = num(op.gravity);
      if (gravity !== null) item.gravity = clamp(gravity, 0, 200);
      const air = num(op.air);
      if (air !== null) item.air = clamp(air, 0, 5);
      const jump = num(op.jump_speed);
      if (jump !== null) item.jump_speed = clamp(jump, 0, 50);
      const move = num(op.move_speed);
      if (move !== null) item.move_speed = clamp(move, 0, 50);
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
      if (typeof op.color === "string" && HEX_COLOR.test(op.color)) item.color = op.color.toLowerCase();
      clean.push(item);
      continue;
    }

    for (const axis of ["x", "y", "z"]) {
      const n = num(op[axis]);
      if (n !== null) item[axis] = clamp(n, -10000, 10000);
    }
    const scale = num(op.scale);
    if (scale !== null) item.scale = clamp(scale, 0.05, 100);
    if (typeof op.type === "string" && SHAPE_TYPES.has(op.type)) item.type = op.type;
    if (typeof op.color === "string" && HEX_COLOR.test(op.color)) item.color = op.color.toLowerCase();
    if (typeof op.name === "string") item.name = op.name.slice(0, 64);
    const material = op.material;
    if (typeof material === "string" && (material === "" || MATERIAL_ID_RE.test(material))) item.material = material;
    for (const key of ["anchored", "collide"]) {
      const b = bool(op[key]);
      if (b !== null) item[key] = b;
    }
    for (const axis of ["vx", "vy", "vz"]) {
      const n = num(op[axis]);
      if (n !== null) item[axis] = clamp(n, -200, 200);
    }
    clean.push(item);
  }
  // sortare stabila dupa timp (ordinea originala se pastreaza la timp egal)
  return clean
    .map((o, i) => ({ o, i }))
    .sort((a, b) => (a.o.t - b.o.t) || (a.i - b.i))
    .map((x) => x.o);
}

export function parseSandboxOutput(output: string, fatal?: boolean): SandboxResult {
  const result: SandboxResult = { ok: true, output: [], errors: [], ops: [], duration: 0, truncated: false };
  const text = typeof output === "string" ? output.slice(0, MAX_OUTPUT_CHARS) : "";
  let sawOps = false;

  for (const line of text.split("\n")) {
    if (!line) continue;
    let msg: unknown;
    try {
      msg = JSON.parse(line);
    } catch {
      continue;
    }
    if (!isPlainObject(msg)) continue;

    const kind = msg.type;
    if (kind === "print" || kind === "warn") {
      if (result.output.length < MAX_LOG_LINES) {
        result.output.push({ level: kind, msg: String(msg.msg ?? "").slice(0, 2000) });
      }
    } else if (kind === "error") {
      if (result.errors.length < 20) result.errors.push(String(msg.msg ?? "").slice(0, 2000));
    } else if (kind === "ops" && !sawOps) {
      sawOps = true;
      const data = msg.data;
      if (isPlainObject(data)) {
        result.ops = sanitizeOps(data.ops);
        const d = num(data.duration);
        result.duration = clamp(d !== null ? d : 0, 0, 60);
        result.truncated = Boolean(data.truncated);
      }
    }
  }

  if (fatal && result.errors.length === 0) result.errors.push("Sandbox stopped unexpectedly");
  if (fatal) result.truncated = true;
  result.ok = result.errors.length === 0;
  return result;
}

// ---------------- pagina-gazda (WebView) ----------------

const CSP = [
  "default-src 'none'",
  "script-src 'unsafe-inline' 'wasm-unsafe-eval' blob:",
  "worker-src blob:",
  "child-src blob:",
  "connect-src 'none'",
  "img-src 'none'",
  "media-src 'none'",
  "font-src 'none'",
  "style-src 'none'",
  "object-src 'none'",
  "frame-src 'none'",
  "form-action 'none'",
  "base-uri 'none'",
].join("; ");

// Codul worker-ului (NU contine cod de utilizator: scriptul ajunge doar ca date).
// Atentie: fara backtick-uri, fara ${ } si fara backslash in acest text.
const WORKER_BODY = `
"use strict";
(function () {
  var post = self.postMessage.bind(self);
  function fail(msg) { post({ type: "fail", msg: String(msg) }); }

  // Dupa ce WASM e initializat, scoatem tot ce ar putea scoate date din worker.
  function lockdown() {
    var names = ["fetch", "XMLHttpRequest", "WebSocket", "EventSource", "importScripts",
      "indexedDB", "caches", "Worker", "SharedWorker", "BroadcastChannel", "WebTransport",
      "RTCPeerConnection", "Request", "XMLHttpRequestUpload"];
    for (var i = 0; i < names.length; i++) {
      try { Object.defineProperty(self, names[i], { value: undefined, writable: false, configurable: false }); } catch (e) {}
    }
    try { if (self.navigator) Object.defineProperty(self.navigator, "sendBeacon", { value: undefined, configurable: false }); } catch (e) {}
  }

  function put(M, s) {
    var n = M.lengthBytesUTF8(s);
    var p = M._malloc(n + 1);
    M.stringToUTF8(s, p, n + 1);
    return { ptr: p, len: n };
  }

  self.onmessage = function (e) {
    self.onmessage = null;
    var d = e.data;
    try {
      if (typeof createAstranLuau !== "function") { fail("Luau runtime is not installed"); return; }
      createAstranLuau({}).then(function (M) {
        lockdown();
        var bundle = put(M, d.bundle);
        var prelude = put(M, d.prelude);
        var caps = put(M, d.caps);
        var entry = put(M, d.entry);
        var outPtr = M._astran_run(bundle.ptr, bundle.len, prelude.ptr, prelude.len,
          caps.ptr, entry.ptr, d.timeoutMs | 0, d.memMB | 0);
        var out = M.UTF8ToString(outPtr);
        M._free(outPtr);
        post({ type: "done", output: out });
      }).catch(function (err) { fail(err && err.message ? err.message : err); });
    } catch (err) { fail(err && err.message ? err.message : err); }
  };
})();
`;

function jsonForScript(value: unknown): string {
  // JSON valid si sigur in interiorul unui <script> (fara </script>, <!--, U+2028/2029).
  return JSON.stringify(value)
    .replace(/</g, "\\u003c")
    .replace(/\u2028/g, "\\u2028")
    .replace(/\u2029/g, "\\u2029");
}

const PAGE_SCRIPT = `
"use strict";
(function () {
  var WORKER_SRC = __WORKER_SRC__;
  var MAX_OUT = ${MAX_OUTPUT_CHARS};
  var current = null;

  function send(obj) {
    try { window.ReactNativeWebView.postMessage(JSON.stringify(obj)); } catch (e) {}
  }
  function errLine(msg) { return JSON.stringify({ type: "error", msg: msg }) + String.fromCharCode(10); }
  function cleanup() {
    if (!current) return;
    clearTimeout(current.timer);
    try { current.worker.terminate(); } catch (e) {}
    try { URL.revokeObjectURL(current.url); } catch (e) {}
    current = null;
  }
  function finish(id, output, fatal) {
    if (!current || current.id !== id) return;
    cleanup();
    if (typeof output !== "string") output = errLine("Invalid sandbox output");
    if (output.length > MAX_OUT) { output = errLine("Sandbox output too large"); fatal = true; }
    send({ type: "result", id: id, output: output, fatal: !!fatal });
  }

  function run(m) {
    cleanup();
    var id = m.id;
    if (typeof m.prelude !== "string" || typeof m.bundle !== "string" || typeof m.caps !== "string" || typeof m.entry !== "string") {
      send({ type: "result", id: id, output: errLine("Invalid run request"), fatal: true });
      return;
    }
    var timeoutMs = Math.max(50, Math.min(5000, m.timeoutMs | 0));
    var memMB = Math.max(8, Math.min(128, m.memMB | 0));
    if (typeof WebAssembly !== "object" || typeof Worker !== "function" || typeof Blob !== "function") {
      send({ type: "result", id: id, output: errLine("Sandbox is not supported on this device"), fatal: true });
      return;
    }
    var url, w;
    try {
      url = URL.createObjectURL(new Blob([WORKER_SRC], { type: "text/javascript" }));
      w = new Worker(url);
    } catch (e) {
      try { if (url) URL.revokeObjectURL(url); } catch (e2) {}
      send({ type: "result", id: id, output: errLine("Cannot start sandbox worker"), fatal: true });
      return;
    }
    current = {
      id: id, worker: w, url: url,
      // Oprirea garantata: worker-ul e distrus chiar daca VM-ul nu mai raspunde.
      timer: setTimeout(function () { finish(id, errLine("Script timed out"), true); }, timeoutMs + 1500)
    };
    w.onmessage = function (e) {
      var d = e.data;
      if (d && d.type === "done") finish(id, d.output, false);
      else if (d && d.type === "fail") finish(id, errLine(String(d.msg).slice(0, 500)), true);
    };
    w.onerror = function () { finish(id, errLine("Sandbox worker crashed"), true); };
    w.postMessage({ prelude: m.prelude, bundle: m.bundle, caps: m.caps, entry: m.entry, timeoutMs: timeoutMs, memMB: memMB });
  }

  function onMsg(e) {
    var m;
    try { m = JSON.parse(e.data); } catch (err) { return; }
    if (!m || m.type !== "run" || typeof m.id !== "number") return;
    run(m);
  }
  window.addEventListener("message", onMsg);
  document.addEventListener("message", onMsg); // Android RN WebView posteaza pe document
})();
`;

export function buildHostHtml(): string {
  const workerSrc = (LUAU_GLUE_JS || "") + "\n" + WORKER_BODY;
  const script = PAGE_SCRIPT.replace("__WORKER_SRC__", () => jsonForScript(workerSrc));
  return (
    '<!DOCTYPE html><html><head><meta charset="utf-8">' +
    `<meta http-equiv="Content-Security-Policy" content="${CSP}">` +
    '<meta name="referrer" content="no-referrer">' +
    "</head><body><script>" +
    script +
    "</script></body></html>"
  );
}