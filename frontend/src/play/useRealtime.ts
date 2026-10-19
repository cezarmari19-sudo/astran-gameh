// Conexiunea WebSocket live catre ceilalti jucatori din ACEEASI instanta de joc - vezi
// backend/realtime.py pentru protocolul complet si limitarile reale (scalare orizontala
// prin Redis - optionala - deja documentata acolo; fara autoritate de server/anti-cheat
// ramane documentat acolo la fel).
//
// Nu e un hook React clasic: NU tine state care re-randeaza ecranul. Scrie direct intr-un
// Map simplu (`remotePlayers`), pe care bucla de randare din play/[id].tsx il citeste
// manual, o data pe frame - un pachet de retea la fiecare ~70ms NU trebuie sa forteze un
// re-render React al intregului ecran de joc (ar fi mult mai costisitor decat sincronizarea
// in sine). `connectRealtime` e un factory simplu, apelabil dintr-un useEffect/useRef.
//
// INTERPOLARE: pozitiile brute vin la fiecare ~70ms + jitter de retea - desenate direct,
// miscarea celorlalti jucatori arata sacadat. `getRenderState(now)` NU desenaza ultima
// pozitie primita, ci interpoleaza intre ultimele DOUA pozitii primite, cu o intarziere
// fixa (RENDER_DELAY_MS) fata de "acum" - e intarzierea cu care alti jucatori sunt desenati,
// aleasa sa fie aproape mereu intre doua pachete reale primite, ca sa nu fie nevoie sa
// "ghicim" (extrapolam) unde o sa fie, ci doar sa interpolam intre doua puncte reale.

import { wsUrl } from "@/src/api/client";

export type RemotePlayerState = {
  user_id: string;
  username?: string;
  x: number; y: number; z: number; ry: number;
  anim?: string;
  updatedAt: number; // Date.now() local, la ultimul pachet primit - vezi STALE_AFTER_MS mai jos
};

// O "poza" (snapshot) primita de la server, cu timpul LOCAL (Date.now()) la care a ajuns -
// nu timpul de pe server (nu sincronizam ceasuri intre client si server, nu e nevoie: tot
// ce conteaza e distanta intre doua sosiri consecutive, masurata local, pe acelasi ceas).
type Sample = { x: number; y: number; z: number; ry: number; anim?: string; t: number };

export type RenderState = { x: number; y: number; z: number; ry: number; anim?: string };
// Alias separat, NU inline - un generic scris direct ca tip de retur al unei functii
// (`function f(): Map<string, X>`) a blocat parser-ul Babel folosit de Metro la build-ul
// EAS (desi e sintaxa TS perfect valida, acceptata de tsc) - vezi getRenderState() mai jos.
export type RenderStateMap = Map<string, RenderState>;

export type RealtimeHandle = {
  remotePlayers: Map<string, RemotePlayerState>; // starea bruta, "ultima primita" - pastrata pentru roster/UI, NU pentru desenat (vezi getRenderState)
  // Pozitiile INTERPOLATE, gata de desenat, pentru momentul `now` dat (de obicei Date.now()
  // din bucla de randare). Recalculeaza putin la fiecare apel (cateva operatii per jucator) -
  // apelabil in siguranta o data pe frame.
  getRenderState: (now: number) => RenderStateMap;
  // Nu trimite imediat pe retea - doar retine "ultima stare cunoscuta", trimisa la
  // urmatorul tick al timer-ului intern (vezi SEND_INTERVAL_MS). Apelabil in fiecare
  // frame de randare, fara sa coste un send() de fiecare data.
  sendState: (s: { x: number; y: number; z: number; ry: number; anim?: string }) => void;
  connected: () => boolean;
  close: () => void;
};

// Intarzierea cu care sunt desenati ceilalti jucatori fata de "acum" - trebuie sa fie putin
// mai mare decat SEND_INTERVAL_MS (+ jitter normal de retea), ca sa existe aproape mereu doua
// pachete reale "de o parte si de alta" a momentului redat, fara sa fie nevoie sa extrapolam.
const RENDER_DELAY_MS = 120;

// Interpolare liniara, cu grija la unghiuri (ry e un unghi, nu o distanta): diferenta dintre
// doua unghiuri trebuie normalizata la [-PI, PI] INAINTE de inmultire, altfel un personaj care
// trece de la 3.0 la -3.0 radiani (aproape acelasi unghi, doar ca a "trecut" de PI) s-ar roti
// gresit, pe partea lunga a cercului, in loc de pe partea scurta.
function lerp(a: number, b: number, t: number): number {
  return a + (b - a) * t;
}
function lerpAngle(a: number, b: number, t: number): number {
  let diff = (b - a) % (Math.PI * 2);
  if (diff > Math.PI) diff -= Math.PI * 2;
  if (diff < -Math.PI) diff += Math.PI * 2;
  return a + diff * t;
}

// Sub bugetul de 100ms cerut: restul bugetului e reteaua insasi (RTT), nu intervalul de
// trimitere. La 70ms, trimitem ~14 ori/secunda - suficient de des pentru miscare fluida
// fara sa incarce inutil reteaua/serverul (vezi si rate-limit-ul de pe server: 30/sec).
const SEND_INTERVAL_MS = 70;
// Daca un jucator nu a mai trimis nimic (nici implicit, prin server) de atata timp, il
// consideram plecat - acopera cazul unei retele cazute brusc, fara mesaj curat de "leave".
const STALE_AFTER_MS = 6000;
const STALE_CHECK_INTERVAL_MS = 2000;
const RECONNECT_DELAY_MS = 1500;

export function connectRealtime(
  gameId: string,
  instanceId: string,
  onRosterChange?: () => void, // optional: apelat dupa orice schimbare (join/state/leave) - util doar daca vrei UI gen "3 jucatori online", nu e necesar pentru randare
): RealtimeHandle {
  const remotePlayers = new Map<string, RemotePlayerState>();
  // Istoricul de interpolare: ultimele DOUA "poze" reale primite per jucator - vezi
  // getRenderState() mai jos, care interpoleaza intre ele.
  const history = new Map<string, { prev: Sample; next: Sample }>();
  let ws: WebSocket | null = null;
  let closedByUs = false;
  let isConnected = false;
  let pendingState: { x: number; y: number; z: number; ry: number; anim?: string } | null = null;

  function applyState(user_id: string, partial: Partial<RemotePlayerState>) {
    const prev = remotePlayers.get(user_id);
    const merged: RemotePlayerState = {
      user_id,
      username: partial.username ?? prev?.username,
      x: typeof partial.x === "number" ? partial.x : prev?.x ?? 0,
      y: typeof partial.y === "number" ? partial.y : prev?.y ?? 0,
      z: typeof partial.z === "number" ? partial.z : prev?.z ?? 0,
      ry: typeof partial.ry === "number" ? partial.ry : prev?.ry ?? 0,
      anim: partial.anim ?? prev?.anim,
      updatedAt: Date.now(),
    };
    remotePlayers.set(user_id, merged);

    const sample: Sample = { x: merged.x, y: merged.y, z: merged.z, ry: merged.ry, anim: merged.anim, t: merged.updatedAt };
    const h = history.get(user_id);
    if (!h) {
      // Primul pachet de la acest jucator (de obicei din "roster", la conectare) - nu avem
      // inca doua puncte reale, asa ca punem acelasi punct de doua ori: getRenderState va
      // desena instant pozitia initiala, fara nicio interpolare falsa de la (0,0,0).
      history.set(user_id, { prev: sample, next: sample });
    } else {
      history.set(user_id, { prev: h.next, next: sample });
    }
    onRosterChange?.();
  }

  function getRenderState(now: number): RenderStateMap {
    const renderTime = now - RENDER_DELAY_MS;
    const out: RenderStateMap = new Map(); // tipul vine din adnotarea variabilei, nu dintr-un generic inline pe new Map() - vezi nota de mai sus
    for (const [user_id, h] of history) {
      const { prev: p, next: n } = h;
      if (n.t <= p.t || renderTime <= p.t) {
        out.set(user_id, { x: p.x, y: p.y, z: p.z, ry: p.ry, anim: p.anim });
        continue;
      }
      if (renderTime >= n.t) {
        // n e cel mai nou punct cunoscut, dar "acum" a trecut deja de el - nu extrapolam
        // (ar putea "goni" in directia gresita daca jucatorul s-a oprit brusc); ramanem la n.
        out.set(user_id, { x: n.x, y: n.y, z: n.z, ry: n.ry, anim: n.anim });
        continue;
      }
      const t = (renderTime - p.t) / (n.t - p.t); // intotdeauna in [0,1] aici
      out.set(user_id, {
        x: lerp(p.x, n.x, t),
        y: lerp(p.y, n.y, t),
        z: lerp(p.z, n.z, t),
        ry: lerpAngle(p.ry, n.ry, t),
        anim: n.anim,
      });
    }
    return out;
  }

  async function connect() {
    if (closedByUs) return;
    try {
      const url = await wsUrl(`/ws/games/${gameId}/instances/${instanceId}`);
      const socket = new WebSocket(url);
      ws = socket;
      socket.onopen = () => { isConnected = true; };
      socket.onmessage = (ev: any) => {
        let msg: any;
        try { msg = JSON.parse(ev.data); } catch { return; }
        if (!msg || typeof msg !== "object") return;
        if (msg.t === "roster" && Array.isArray(msg.players)) {
          for (const p of msg.players) {
            if (p && typeof p.user_id === "string") applyState(p.user_id, p);
          }
        } else if (msg.t === "state" && typeof msg.user_id === "string") {
          applyState(msg.user_id, msg);
        } else if (msg.t === "join") {
          onRosterChange?.(); // fara stare inca - va aparea la primul "state" de la el
        } else if (msg.t === "leave" && typeof msg.user_id === "string") {
          remotePlayers.delete(msg.user_id);
          history.delete(msg.user_id);
          onRosterChange?.();
        }
      };
      socket.onclose = () => {
        isConnected = false;
        // o cadere scurta de retea nu trebuie sa scoata jucatorul definitiv din sincronizare -
        // reconectare automata, doar daca noi (clientul) n-am inchis-o intentionat (vezi close() mai jos)
        if (!closedByUs) setTimeout(connect, RECONNECT_DELAY_MS);
      };
      socket.onerror = () => {
        try { socket.close(); } catch {}
      };
    } catch {
      if (!closedByUs) setTimeout(connect, RECONNECT_DELAY_MS);
    }
  }
  connect();

  const sendTimer = setInterval(() => {
    if (!pendingState || !ws || ws.readyState !== WebSocket.OPEN) return;
    try { ws.send(JSON.stringify({ t: "state", ...pendingState })); } catch {}
  }, SEND_INTERVAL_MS);

  const staleTimer = setInterval(() => {
    const now = Date.now();
    let changed = false;
    for (const [uid, p] of remotePlayers) {
      if (now - p.updatedAt > STALE_AFTER_MS) { remotePlayers.delete(uid); history.delete(uid); changed = true; }
    }
    if (changed) onRosterChange?.();
  }, STALE_CHECK_INTERVAL_MS);

  return {
    remotePlayers,
    getRenderState,
    sendState: (s) => { pendingState = s; },
    connected: () => isConnected,
    close: () => {
      closedByUs = true;
      clearInterval(sendTimer);
      clearInterval(staleTimer);
      try { ws?.close(); } catch {}
    },
  };
}