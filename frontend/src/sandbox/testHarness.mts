// Harness de test: ruleaza codul REAL din runtime.ts (worker + pagina-gazda) in contexte `vm`
// care imita doar API-urile browser-ului de care are nevoie (Worker, Blob, URL, postMessage).
// Luau/WASM e cel real (LUAU_GLUE_JS), nu un mock.
import vm from "node:vm";
import { buildHostHtml, buildRunMessage } from "./runtime.ts";
import type { SandboxRunOptions } from "./runtime.ts";
import { PRELUDE_SOURCE } from "./preludeSource.ts";

export { PRELUDE_SOURCE };

export function workerSourceFromHtml(html: string): string {
  const m = html.match(/var WORKER_SRC = (".*?");\n/s);
  if (!m) throw new Error("WORKER_SRC not found in host html");
  return JSON.parse(m[1]);
}
export function pageScriptFromHtml(html: string): string {
  const m = html.match(/<script>([\s\S]*)<\/script><\/body>/);
  if (!m) throw new Error("page script not found");
  return m[1];
}

const NET_NAMES = ["fetch", "XMLHttpRequest", "WebSocket", "EventSource", "importScripts", "indexedDB",
  "caches", "Worker", "SharedWorker", "BroadcastChannel", "WebTransport", "RTCPeerConnection", "Request"];

// Un "Worker" real al sandbox-ului: ruleaza WORKER_SRC intr-un context izolat, cu sentinele
// pe API-urile de retea, ca sa se poata verifica ca lockdown() le scoate.
export class FakeWorker {
  static all: FakeWorker[] = [];
  onmessage: ((e: any) => void) | null = null;
  onerror: ((e: any) => void) | null = null;
  terminated = false;
  ctx: any;
  src: string;
  constructor(src: string) {
    this.src = src;
    FakeWorker.all.push(this);
    const self: any = {};
    for (const n of NET_NAMES) self[n] = function sentinel() { throw new Error("NET:" + n); };
    Object.assign(self, {
      console, WebAssembly, TextDecoder, TextEncoder, URL, setTimeout, clearTimeout, performance,
      process: undefined, Buffer, location: { href: "blob:null/test" }, navigator: {},
      postMessage: (d: any) => { if (!this.terminated) queueMicrotask(() => this.onmessage?.({ data: d })); },
    });
    self.self = self;
    this.ctx = vm.createContext(self);
    vm.runInContext(src, this.ctx);
  }
  postMessage(d: any) {
    if (this.terminated) return;
    setTimeout(() => { try { this.ctx.onmessage?.({ data: d }); } catch (e) { this.onerror?.(e); } }, 0);
  }
  terminate() { this.terminated = true; }
  typeofGlobal(name: string): string {
    return vm.runInContext(`typeof ${name}`, this.ctx);
  }
}

// Pagina-gazda reala (PAGE_SCRIPT) intr-un "window" fals: mesajele spre RN se aduna in `sent`.
// deferLoad: pagina nu ruleaza scriptul pana la load() - ca un WebView care se incarca lent; pana
// atunci nu exista listener-e, deci orice mesaj livrat se pierde (ca in WebView-ul real).
export function createHostPage(opts: {
  workerFactory?: (src: string) => any;
  onSend?: (raw: string) => void;
  deferLoad?: boolean;
} = {}) {
  const html = buildHostHtml();
  const page = pageScriptFromHtml(html);
  const sent: any[] = [];
  const listeners: Record<string, ((e: any) => void)[]> = { window: [], document: [] };
  const win: any = {
    ReactNativeWebView: { postMessage: (s: string) => { sent.push(JSON.parse(s)); opts.onSend?.(s); } },
    addEventListener: (t: string, f: any) => { if (t === "message") listeners.window.push(f); },
  };
  const doc: any = { addEventListener: (t: string, f: any) => { if (t === "message") listeners.document.push(f); } };
  const blobs = new Map<string, string>();
  let n = 0;
  const ctx: any = {
    window: win, document: doc, WebAssembly, JSON, Math, String, setTimeout, clearTimeout, console,
    Blob: class { parts: string[]; constructor(parts: string[]) { this.parts = parts; } },
    URL: {
      createObjectURL: (b: any) => { const u = "blob:test/" + (++n); blobs.set(u, b.parts.join("")); return u; },
      revokeObjectURL: (u: string) => { blobs.delete(u); },
    },
    Worker: function (url: string) {
      const src = blobs.get(url)!;
      return opts.workerFactory ? opts.workerFactory(src) : new FakeWorker(src);
    },
  };
  vm.createContext(ctx);
  let loaded = false;
  const load = () => { if (!loaded) { loaded = true; vm.runInContext(page, ctx); } };
  if (!opts.deferLoad) load();
  return {
    html, sent, load,
    isLoaded: () => loaded,
    deliver(msg: unknown, via: "window" | "document" = "document") {
      for (const f of listeners[via]) f({ data: typeof msg === "string" ? msg : JSON.stringify(msg) });
    },
    async waitFor(pred: (m: any) => boolean, ms = 8000) {
      const t0 = Date.now();
      while (Date.now() - t0 < ms) {
        const m = sent.find(pred);
        if (m) return m;
        await new Promise((r) => setTimeout(r, 10));
      }
      throw new Error("timeout waiting for host-page message; got " + JSON.stringify(sent).slice(0, 300));
    },
  };
}

export function runMessage(src: string | Record<string, string>, extra: Partial<SandboxRunOptions> = {}) {
  const files = typeof src === "string" ? [{ name: "main", source: src }]
    : Object.entries(src).map(([name, source]) => ({ name, source }));
  return buildRunMessage({ files, preludeSource: PRELUDE_SOURCE, ...extra });
}