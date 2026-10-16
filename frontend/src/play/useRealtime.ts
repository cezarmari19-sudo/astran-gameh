// Conexiunea WebSocket live catre ceilalti jucatori din ACEEASI instanta de joc - vezi
// backend/realtime.py pentru protocolul complet si limitarile reale (deja documentate acolo:
// fara scalare orizontala intre procese, fara autoritate de server/anti-cheat, fara
// interpolare pe client inca).
//
// Nu e un hook React clasic: NU tine state care re-randeaza ecranul. Scrie direct intr-un
// Map simplu (`remotePlayers`), pe care bucla de randare din play/[id].tsx il citeste
// manual, o data pe frame - un pachet de retea la fiecare ~70ms NU trebuie sa forteze un
// re-render React al intregului ecran de joc (ar fi mult mai costisitor decat sincronizarea
// in sine). `connectRealtime` e un factory simplu, apelabil dintr-un useEffect/useRef.

import { wsUrl } from "@/src/api/client";

export type RemotePlayerState = {
  user_id: string;
  username?: string;
  x: number; y: number; z: number; ry: number;
  anim?: string;
  updatedAt: number; // Date.now() local, la ultimul pachet primit - vezi STALE_AFTER_MS mai jos
};

export type RealtimeHandle = {
  remotePlayers: Map<string, RemotePlayerState>;
  // Nu trimite imediat pe retea - doar retine "ultima stare cunoscuta", trimisa la
  // urmatorul tick al timer-ului intern (vezi SEND_INTERVAL_MS). Apelabil in fiecare
  // frame de randare, fara sa coste un send() de fiecare data.
  sendState: (s: { x: number; y: number; z: number; ry: number; anim?: string }) => void;
  connected: () => boolean;
  close: () => void;
};

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
  let ws: WebSocket | null = null;
  let closedByUs = false;
  let isConnected = false;
  let pendingState: { x: number; y: number; z: number; ry: number; anim?: string } | null = null;

  function applyState(user_id: string, partial: Partial<RemotePlayerState>) {
    const prev = remotePlayers.get(user_id);
    remotePlayers.set(user_id, {
      user_id,
      username: partial.username ?? prev?.username,
      x: typeof partial.x === "number" ? partial.x : prev?.x ?? 0,
      y: typeof partial.y === "number" ? partial.y : prev?.y ?? 0,
      z: typeof partial.z === "number" ? partial.z : prev?.z ?? 0,
      ry: typeof partial.ry === "number" ? partial.ry : prev?.ry ?? 0,
      anim: partial.anim ?? prev?.anim,
      updatedAt: Date.now(),
    });
    onRosterChange?.();
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
      if (now - p.updatedAt > STALE_AFTER_MS) { remotePlayers.delete(uid); changed = true; }
    }
    if (changed) onRosterChange?.();
  }, STALE_CHECK_INTERVAL_MS);

  return {
    remotePlayers,
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