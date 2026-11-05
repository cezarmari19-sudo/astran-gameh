// Starea chatului din joc, ca functii PURE (fara React, fara retea) - ca sa poata fi testate
// izolat si ca useRealtime.ts / GameChat.tsx sa ramana subtiri.
//
// IMPORTANT (securitate): clientul NU filtreaza, NU decide varsta si NU pune identitatea
// expeditorului. Tot ce apare aici vine de la server (vezi backend/astran_sandbox/chat_core.py);
// "mode" e doar informativ (eticheta in UI), nu o permisiune.

export type ChatMode = "under_18" | "adult_18";

export type ChatMsg = { id: string; ts: number; user_id: string; username: string; text: string };

export type PendingMsg = {
  cid: string;
  text: string;
  createdAt: number;
  status: "sending" | "failed";
  error?: string; // cod: rate_limited | duplicate | too_long | empty | muted | unavailable | offline | timeout | ...
};

export type ChatNotice = { code: string; at: number };

export type ChatState = {
  messages: ChatMsg[]; // sortate dupa (ts, id), fara duplicate
  pending: PendingMsg[]; // mesaje trimise de noi, inca neconfirmate de server (sau esuate)
  mode: ChatMode | null;
  maxLen: number;
  connected: boolean;
  historyLoaded: boolean; // true dupa primul "chat_history" - ca UI-ul sa nu numere istoricul vechi drept "necitit"
  blockedLocal: string[]; // jucatori blocati in aceasta sesiune (ascunsi imediat; serverul aplica oricum blocarea)
  notice: ChatNotice | null; // ultima eroare/confirmare fara mesaj asociat (ex: raport trimis)
};

export const MAX_MESSAGES = 200;
export const DEFAULT_MAX_LEN = 200;
export const PENDING_TIMEOUT_MS = 8000;

export function initialChatState(): ChatState {
  return { messages: [], pending: [], mode: null, maxLen: DEFAULT_MAX_LEN, connected: false, historyLoaded: false, blockedLocal: [], notice: null };
}

/** Aceeasi curatare "blanda" ca pe server (serverul o reface oricum - asta e doar pentru UX). */
export function normalizeInput(text: string): string {
  return text.replace(/[\u0000-\u0008\u000b-\u001f\u007f-\u009f\u200b-\u200f\u202a-\u202e\u2060-\u2064\u2066-\u206f\ufeff]/g, "").replace(/\s+/g, " ").trim();
}

export function parseServerMessage(raw: any): ChatMsg | null {
  if (!raw || typeof raw !== "object") return null;
  const { id, ts, user_id, username, text } = raw;
  if (typeof id !== "string" || !id || typeof ts !== "number" || !Number.isFinite(ts)) return null;
  if (typeof user_id !== "string" || typeof username !== "string" || typeof text !== "string") return null;
  return { id, ts, user_id, username, text };
}

function compare(a: ChatMsg, b: ChatMsg): number {
  return a.ts !== b.ts ? a.ts - b.ts : a.id < b.id ? -1 : a.id > b.id ? 1 : 0;
}

/** Insereaza fara duplicate (dupa id), pastrand ordinea (ts, id), cu plafon MAX_MESSAGES. */
export function insertMessages(list: ChatMsg[], incoming: ChatMsg[]): ChatMsg[] {
  if (incoming.length === 0) return list;
  const known = new Set(list.map(m => m.id));
  const fresh = incoming.filter(m => !known.has(m.id));
  if (fresh.length === 0) return list;
  // cazul frecvent: mesaje noi, mai noi decat ultimul -> append, fara sortare completa
  let out = list.concat(fresh);
  out.sort(compare);
  if (out.length > MAX_MESSAGES) out = out.slice(out.length - MAX_MESSAGES);
  return out;
}

export function applyHistory(s: ChatState, rawMessages: any): ChatState {
  const parsed: ChatMsg[] = [];
  if (Array.isArray(rawMessages)) {
    for (const r of rawMessages) {
      const m = parseServerMessage(r);
      if (m) parsed.push(m);
    }
  }
  return { ...s, messages: insertMessages(s.messages, parsed), historyLoaded: true };
}

/** Un mesaj nou de la server. Daca poarta "cid", e ecoul mesajului nostru: scoatem varianta "pending". */
export function applyMessage(s: ChatState, raw: any): ChatState {
  const m = parseServerMessage(raw);
  if (!m) return s;
  const cid = raw && typeof raw.cid === "string" ? raw.cid : null;
  const pending = cid ? s.pending.filter(p => p.cid !== cid) : s.pending;
  return { ...s, messages: insertMessages(s.messages, [m]), pending };
}

export function addPending(s: ChatState, cid: string, text: string, now: number): ChatState {
  return { ...s, pending: [...s.pending, { cid, text, createdAt: now, status: "sending" }], notice: null };
}

export function failPending(s: ChatState, cid: string, error: string): ChatState {
  if (!s.pending.some(p => p.cid === cid && p.status === "sending")) return s;
  return { ...s, pending: s.pending.map(p => (p.cid === cid ? { ...p, status: "failed" as const, error } : p)) };
}

export function removePending(s: ChatState, cid: string): ChatState {
  return s.pending.some(p => p.cid === cid) ? { ...s, pending: s.pending.filter(p => p.cid !== cid) } : s;
}

/** Marcheaza ca esuate mesajele trimise si neconfirmate de mai mult de PENDING_TIMEOUT_MS. */
export function expirePending(s: ChatState, now: number): ChatState {
  if (!s.pending.some(p => p.status === "sending" && now - p.createdAt >= PENDING_TIMEOUT_MS)) return s;
  return {
    ...s,
    pending: s.pending.map(p => (p.status === "sending" && now - p.createdAt >= PENDING_TIMEOUT_MS ? { ...p, status: "failed" as const, error: "timeout" } : p)),
  };
}

export function failAllSending(s: ChatState, error: string): ChatState {
  if (!s.pending.some(p => p.status === "sending")) return s;
  return { ...s, pending: s.pending.map(p => (p.status === "sending" ? { ...p, status: "failed" as const, error } : p)) };
}

export function setConnection(s: ChatState, connected: boolean): ChatState {
  return s.connected === connected ? s : { ...s, connected };
}

export function setInfo(s: ChatState, mode: any, maxLen: any): ChatState {
  const m: ChatMode | null = mode === "adult_18" ? "adult_18" : mode === "under_18" ? "under_18" : null;
  const len = typeof maxLen === "number" && Number.isFinite(maxLen) && maxLen > 0 && maxLen <= 1000 ? Math.floor(maxLen) : DEFAULT_MAX_LEN;
  return { ...s, mode: m, maxLen: len };
}

export function setNotice(s: ChatState, code: string, now: number): ChatState {
  return { ...s, notice: { code, at: now } };
}

export function blockLocal(s: ChatState, userId: string): ChatState {
  return s.blockedLocal.includes(userId) ? s : { ...s, blockedLocal: [...s.blockedLocal, userId] };
}

export function visibleMessages(s: ChatState): ChatMsg[] {
  return s.blockedLocal.length === 0 ? s.messages : s.messages.filter(m => !s.blockedLocal.includes(m.user_id));
}

/** Eroare primita de la server pentru un mesaj: cu cid -> marcheaza mesajul pending ca esuat; fara cid -> notificare. */
export function applyServerError(s: ChatState, code: any, cid: any, now: number): ChatState {
  const c = typeof code === "string" ? code : "unavailable";
  if (typeof cid === "string" && s.pending.some(p => p.cid === cid)) return failPending(s, cid, c);
  return setNotice(s, c, now);
}