// Conexiunea in timp real a mesageriei private (WebSocket /ws/dm, vezi backend/astran_sandbox/dm_routes.py)
// + numarul total de mesaje necitite (insigna din tab). SEPARAT de chatul din joc: alt endpoint,
// alt socket, alt provider. Serverul trimite doar ce are voie sa vada utilizatorul (filtrat pe
// varsta, fara blocati); clientul nu trimite mesaje pe socket (trimiterea merge prin REST).
import React, { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState } from "react";
import { AppState } from "react-native";
import { api, wsUrl } from "@/src/api/client";
import { useAuth } from "@/src/context/AuthContext";
import type { DmMsg } from "./dmStore";

export type DmEvent =
  | { t: "dm"; conversation_id: string; message: DmMsg }
  | { t: "dm_sync" }       // acelasi cont a citit o conversatie pe alt dispozitiv
  | { t: "reconnected" };  // socket (re)deschis: ecranele isi reincarca datele ca sa recupereze ce s-a pierdut

type Listener = (e: DmEvent) => void;

type Ctx = {
  unread: number;
  connected: boolean;
  refreshUnread: () => Promise<void>;
  subscribe: (l: Listener) => () => void;
  setActiveConversation: (id: string | null) => void;
};

const DmContext = createContext<Ctx>({
  unread: 0,
  connected: false,
  refreshUnread: async () => {},
  subscribe: () => () => {},
  setActiveConversation: () => {},
});

const PING_MS = 25_000;

export function DmProvider({ children }: { children: React.ReactNode }) {
  const { user } = useAuth();
  const uid = user?.user_id ?? null;
  const [unread, setUnread] = useState(0);
  const [connected, setConnected] = useState(false);
  const listeners = useRef(new Set<Listener>());
  const activeConv = useRef<string | null>(null);
  const refreshTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  const refreshUnread = useCallback(async () => {
    try {
      const r = await api<{ unread: number }>("/dm/unread");
      setUnread(typeof r?.unread === "number" ? r.unread : 0);
    } catch {
      /* ramane valoarea anterioara */
    }
  }, []);

  const emit = useCallback((e: DmEvent) => {
    listeners.current.forEach((l) => {
      try { l(e); } catch { /* un listener stricat nu opreste ceilalti */ }
    });
  }, []);

  const subscribe = useCallback((l: Listener) => {
    listeners.current.add(l);
    return () => { listeners.current.delete(l); };
  }, []);

  const setActiveConversation = useCallback((id: string | null) => { activeConv.current = id; }, []);

  useEffect(() => {
    if (!uid) {
      setUnread(0);
      setConnected(false);
      return;
    }
    let disposed = false;
    let ws: WebSocket | null = null;
    let retryTimer: ReturnType<typeof setTimeout> | null = null;
    let pingTimer: ReturnType<typeof setInterval> | null = null;
    let attempts = 0;
    let connecting = false;

    const scheduleUnreadRefresh = () => {
      if (refreshTimer.current) clearTimeout(refreshTimer.current);
      refreshTimer.current = setTimeout(refreshUnread, 300);
    };

    const connect = async () => {
      if (disposed || connecting || (ws && ws.readyState <= 1)) return;
      connecting = true;
      try {
        const url = await wsUrl("/ws/dm");
        if (disposed) return;
        const sock = new WebSocket(url);
        ws = sock;
        sock.onopen = () => {
          attempts = 0;
          setConnected(true);
          if (pingTimer) clearInterval(pingTimer);
          pingTimer = setInterval(() => { try { if (sock.readyState === 1) sock.send("ping"); } catch { /* ignorat */ } }, PING_MS);
          refreshUnread();
          emit({ t: "reconnected" });
        };
        sock.onmessage = (ev) => {
          let data: any;
          try { data = JSON.parse(String(ev.data)); } catch { return; }
          if (data?.t === "dm" && data.message && typeof data.conversation_id === "string") {
            emit({ t: "dm", conversation_id: data.conversation_id, message: data.message as DmMsg });
            // mesajul din conversatia deschisa e citit imediat de ecranul ei (nu numaram/anuntam in plus)
            if (!data.message.mine && data.conversation_id !== activeConv.current) scheduleUnreadRefresh();
          } else if (data?.t === "dm_sync") {
            scheduleUnreadRefresh();
            emit({ t: "dm_sync" });
          }
        };
        sock.onerror = () => { try { sock.close(); } catch { /* ignorat */ } };
        sock.onclose = (ev: any) => {
          setConnected(false);
          if (pingTimer) { clearInterval(pingTimer); pingTimer = null; }
          if (ws === sock) ws = null;
          if (disposed || ev?.code === 4401) return; // 4401 = token invalid: nu reincercam in bucla
          const base = ev?.code === 4429 ? 30_000 : Math.min(15_000, 1000 * 2 ** attempts);
          attempts += 1;
          retryTimer = setTimeout(connect, base + Math.random() * 500);
        };
      } catch {
        if (!disposed) {
          attempts += 1;
          retryTimer = setTimeout(connect, Math.min(15_000, 1000 * 2 ** attempts));
        }
      } finally {
        connecting = false;
      }
    };

    connect();
    refreshUnread();

    // la revenirea in prim-plan, daca socketul a murit in fundal, reconectam imediat
    const sub = AppState.addEventListener("change", (s) => {
      if (s !== "active" || disposed) return;
      refreshUnread();
      if (!ws || ws.readyState > 1) {
        if (retryTimer) clearTimeout(retryTimer);
        attempts = 0;
        connect();
      }
    });

    return () => {
      disposed = true;
      sub.remove();
      if (retryTimer) clearTimeout(retryTimer);
      if (pingTimer) clearInterval(pingTimer);
      if (refreshTimer.current) clearTimeout(refreshTimer.current);
      try { ws?.close(); } catch { /* ignorat */ }
      setConnected(false);
    };
  }, [uid, emit, refreshUnread]);

  const value = useMemo(
    () => ({ unread, connected, refreshUnread, subscribe, setActiveConversation }),
    [unread, connected, refreshUnread, subscribe, setActiveConversation]
  );
  return <DmContext.Provider value={value}>{children}</DmContext.Provider>;
}

export function useDm() {
  return useContext(DmContext);
} 