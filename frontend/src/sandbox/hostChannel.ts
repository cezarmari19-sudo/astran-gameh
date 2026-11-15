// frontend/src/sandbox/hostChannel.ts
//
// Canalul dintre React Native si pagina-gazda din WebView (vezi runtime.ts). Logica e aici,
// separata de componenta, ca sa se poata testa cu pagina REALA fara React/WebView.
//
// Ce rezolva fata de varianta veche (care posta direct in WebView):
//   1. HANDSHAKE: un mesaj postat inainte ca pagina sa-si fi inregistrat listener-ele se pierdea
//      fara urma, iar rularea astepta tot watchdog-ul (5+ s) si abia apoi raporta o eroare
//      inselatoare. Acum rularea e tinuta in coada pana pagina anunta {type:"ready"}.
//   2. Pagina care nu se incarca niciodata (WebView stricat) esueaza dupa `loadTimeoutMs`, cu un
//      mesaj clar, si e recreata.
//   3. Pierderea procesului WebView (Android onRenderProcessGone / iOS content process
//      terminated, frecvente la memorie putina): rularea in curs se incheie imediat cu eroare,
//      WebView-ul se recreaza; inainte se astepta watchdog-ul.
//   4. stop(): opreste garantat orice rulare (WebView recreat => Worker distrus).
import { parseSandboxOutput } from "@/src/sandbox/runtime";
import type { SandboxResult } from "@/src/sandbox/runtime";

export const MAX_MESSAGE_CHARS = 2_000_000;
export const MAX_OUTPUT_CHARS = 1_500_000;
export const WATCHDOG_GRACE_MS = 3000;
export const LOAD_TIMEOUT_MS = 15_000;

export const emptyResult = (errors: string[]): SandboxResult => ({
  ok: false,
  output: [],
  errors,
  ops: [],
  duration: 0,
  truncated: false,
});

type RunMessage = { timeoutMs: number; [k: string]: unknown };

type Pending = {
  id: number;
  message: RunMessage;
  resolve: (r: SandboxResult) => void;
  posted: boolean;
  timer: ReturnType<typeof setTimeout> | null;
};

export type HostChannelDeps = {
  // Trimite un string JSON catre pagina din WebView.
  post: (json: string) => void;
  // Distruge si recreeaza WebView-ul (=> Worker-ul vechi e oprit garantat).
  restart: () => void;
  loadTimeoutMs?: number;
  watchdogGraceMs?: number;
};

export class HostChannel {
  private deps: HostChannelDeps;
  private ready = false;
  private pending: Pending | null = null;
  private nextId = 1;
  private loadTimer: ReturnType<typeof setTimeout> | null = null;
  private disposed = false;

  constructor(deps: HostChannelDeps) {
    this.deps = deps;
  }

  // Reversibil: React StrictMode (dev) ruleaza cleanup-ul efectului si apoi il reia pe aceeasi
  // instanta, deci dispose() nu are voie sa fie definitiv pentru un canal care se reataseaza.
  attach(): void {
    this.disposed = false;
  }

  isReady(): boolean {
    return this.ready;
  }

  // O singura rulare in zbor: una noua o inlocuieste pe cea veche (pagina oricum porneste un
  // worker nou, deci cea veche nu si-ar mai primi niciodata raspunsul).
  run(message: RunMessage): Promise<SandboxResult> {
    return new Promise<SandboxResult>((resolve) => {
      if (this.disposed) {
        resolve(emptyResult(["Sandbox unmounted"]));
        return;
      }
      if (this.pending) {
        this.finishPending(emptyResult(["Superseded by a newer run"]));
      }
      const p: Pending = { id: this.nextId++, message, resolve, posted: false, timer: null };
      this.pending = p;
      if (this.ready) {
        this.post(p);
      } else {
        this.startLoadTimer();
      }
    });
  }

  // Mesaj venit din pagina-gazda (string brut din onMessage).
  onHostMessage(raw: unknown): void {
    if (this.disposed || typeof raw !== "string" || raw.length > MAX_MESSAGE_CHARS) return;
    let msg: any;
    try {
      msg = JSON.parse(raw);
    } catch {
      return;
    }
    if (!msg || typeof msg !== "object") return;

    if (msg.type === "ready") {
      this.clearLoadTimer();
      const cur = this.pending;
      if (this.ready && cur && cur.posted) {
        // Pagina a anuntat "ready" a doua oara cat timp o rulare era in curs: s-a reincarcat
        // singura (procesul WebView repornit) si rularea in curs s-a pierdut.
        this.finishPending(emptyResult(["Sandbox page reloaded unexpectedly"]));
        return;
      }
      this.ready = true;
      if (cur && !cur.posted) this.post(cur);
      return;
    }

    if (msg.type !== "result") return;
    const cur = this.pending;
    if (!cur || !cur.posted) return; // raspuns intarziat la o cerere deja incheiata - ignorat
    if (msg.id !== cur.id) return; // un raspuns vechi nu se atribuie cererii curente
    const output = typeof msg.output === "string" ? msg.output : "";
    if (output.length > MAX_OUTPUT_CHARS) {
      this.finishPending(emptyResult(["Sandbox output too large"]));
      return;
    }
    this.finishPending(parseSandboxOutput(output, msg.fatal === true));
  }

  // Oprire explicita (butonul Stop, parasirea ecranului): rularea in curs se incheie, iar
  // WebView-ul e recreat ca sa fie sigur ca nu mai ruleaza nimic.
  stop(reason = "Stopped"): void {
    if (this.disposed) return;
    this.finishPending(emptyResult([reason]));
    this.recycle();
  }

  // Procesul WebView a murit / pagina nu se mai poate folosi.
  onHostLost(reason: string): void {
    if (this.disposed) return;
    this.finishPending(emptyResult([reason]));
    this.recycle();
  }

  dispose(): void {
    if (this.disposed) return;
    this.finishPending(emptyResult(["Sandbox unmounted"]));
    this.clearLoadTimer();
    this.disposed = true;
  }

  // ---- intern ----

  private post(p: Pending): void {
    this.clearLoadTimer();
    p.posted = true;
    // Folosim timeout-ul deja VALIDAT/clamp-uit de buildRunMessage (50..5000 ms), nu valoarea
    // bruta a apelantului: NaN/negativ ar declansa watchdog-ul instant, iar una uriasa l-ar
    // dezactiva (singura oprire daca thread-ul JS al paginii e blocat).
    const grace = this.deps.watchdogGraceMs ?? WATCHDOG_GRACE_MS;
    p.timer = setTimeout(() => {
      if (this.pending !== p) return;
      this.finishPending(emptyResult(["Sandbox watchdog: script did not finish, sandbox restarted"]));
      this.recycle();
    }, p.message.timeoutMs + grace);
    try {
      this.deps.post(JSON.stringify({ ...p.message, id: p.id }));
    } catch (e: any) {
      this.finishPending(emptyResult([String(e?.message || e || "Cannot reach sandbox")]));
      this.recycle();
    }
  }

  private startLoadTimer(): void {
    this.clearLoadTimer();
    const ms = this.deps.loadTimeoutMs ?? LOAD_TIMEOUT_MS;
    this.loadTimer = setTimeout(() => {
      this.loadTimer = null;
      if (!this.pending || this.pending.posted) return;
      this.finishPending(emptyResult(["Sandbox failed to load on this device"]));
      this.recycle();
    }, ms);
  }

  private clearLoadTimer(): void {
    if (this.loadTimer) clearTimeout(this.loadTimer);
    this.loadTimer = null;
  }

  private finishPending(result: SandboxResult): void {
    const cur = this.pending;
    if (!cur) return;
    this.pending = null;
    if (cur.timer) clearTimeout(cur.timer);
    cur.resolve(result);
  }

  private recycle(): void {
    this.ready = false;
    this.clearLoadTimer();
    this.deps.restart();
  }
}