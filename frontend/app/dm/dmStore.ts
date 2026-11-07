// Starea mesageriei private (conversatii 1-la-1 intre prieteni) ca functii PURE - fara React, fara
// retea - ca sa poata fi testate izolat (vezi dmStore.test.mts). SEPARAT de chatul din joc
// (src/play/chatStore.ts), care nu e atins.
//
// IMPORTANT (securitate): clientul NU filtreaza, NU decide varsta si NU pune identitatea
// expeditorului. Textul din `text` e exact ce a decis serverul ca acest utilizator are voie sa vad
// (vezi backend/astran_sandbox/dm_core.py: view_message / chat_core.text_for).

export type DmFriend = {
  user_id: string;
  username: string;
  display_name?: string;
  avatar_url?: string | null;
  online?: boolean;
};

export type DmMsg = {
  id: string;
  conversation_id: string;
  sender_id: string;
  ts: number; // ms epoch, pus de SERVER
  text: string;
  mine: boolean;
};

export type DmConversation = {
  conversation_id: string;
  friend: DmFriend;
  last_message: DmMsg | null;
  unread: number;
  can_send: boolean;
  updated_at: number;
};

export type DmPending = {
  cid: string; // id local (client_id) - doar pentru a lega raspunsul serverului de bula "se trimite"
  text: string;
  status: "sending" | "failed";
  error?: string; // cod: rate_limited | duplicate | too_long | empty | muted | not_allowed | unavailable | offline
};

export const MAX_DM_LEN = 500; // doar pentru contor/maxLength in UI; limita REALA o aplica serverul

// ---- mesaje (lista inversata: cel mai nou primul) ---------------------------------------------

const newerFirst = (a: DmMsg, b: DmMsg) => (a.ts !== b.ts ? b.ts - a.ts : a.id < b.id ? 1 : a.id > b.id ? -1 : 0);

/** Combina mesaje (istoric + live), fara duplicate (dupa id), ordonate cele mai noi primele. */
export function mergeMessages(existing: DmMsg[], incoming: DmMsg[]): DmMsg[] {
  const byId = new Map<string, DmMsg>();
  for (const m of existing) byId.set(m.id, m);
  for (const m of incoming) byId.set(m.id, m);
  return [...byId.values()].sort(newerFirst);
}

// ---- trimitere (bule "se trimite" / "esuat") --------------------------------------------------

export function addPending(list: DmPending[], p: DmPending): DmPending[] {
  return [...list.filter((x) => x.cid !== p.cid), p];
}
export function failPending(list: DmPending[], cid: string, error: string): DmPending[] {
  return list.map((p) => (p.cid === cid ? { ...p, status: "failed", error } : p));
}
export function resolvePending(list: DmPending[], cid: string): DmPending[] {
  return list.filter((p) => p.cid !== cid);
}

// ---- lista de conversatii ---------------------------------------------------------------------

export const sortConversations = (list: DmConversation[]): DmConversation[] =>
  [...list].sort((a, b) => (b.updated_at !== a.updated_at ? b.updated_at - a.updated_at : a.conversation_id < b.conversation_id ? 1 : -1));

/**
 * Aplica un mesaj sosit live pe lista de conversatii: actualizeaza ultimul mesaj, creste
 * necititele (doar daca nu e al meu si conversatia nu e deschisa chiar acum) si o urca in capul
 * listei. Daca conversatia nu e in lista (prima conversatie cu acest prieten), intoarce
 * needsRefresh=true: lista completa (cu prietenul, avatar etc.) trebuie ceruta de la server.
 */
export function applyIncoming(
  list: DmConversation[],
  msg: DmMsg,
  openConversationId: string | null
): { list: DmConversation[]; needsRefresh: boolean } {
  const idx = list.findIndex((c) => c.conversation_id === msg.conversation_id);
  if (idx < 0) return { list, needsRefresh: true };
  const cur = list[idx];
  if (cur.last_message && cur.last_message.id === msg.id) return { list, needsRefresh: false }; // duplicat
  const seen = msg.mine || openConversationId === msg.conversation_id;
  const next: DmConversation = {
    ...cur,
    last_message: msg,
    unread: seen ? cur.unread : cur.unread + 1,
    updated_at: Math.max(cur.updated_at, msg.ts),
  };
  return { list: sortConversations([...list.slice(0, idx), next, ...list.slice(idx + 1)]), needsRefresh: false };
}

export function markConversationRead(list: DmConversation[], conversationId: string): DmConversation[] {
  return list.map((c) => (c.conversation_id === conversationId && c.unread !== 0 ? { ...c, unread: 0 } : c));
}

export const totalUnread = (list: DmConversation[]): number => list.reduce((n, c) => n + (c.unread || 0), 0);

// ---- erori ------------------------------------------------------------------------------------

export type DmErrorKey =
  | "dm_err_empty" | "dm_err_too_long" | "dm_err_invalid" | "dm_err_rate_limited" | "dm_err_duplicate"
  | "dm_err_muted" | "dm_err_not_allowed" | "dm_err_unavailable" | "dm_err_offline";

export function errorKey(code: string | undefined, status?: number): DmErrorKey {
  switch (code) {
    case "empty": return "dm_err_empty";
    case "too_long": return "dm_err_too_long";
    case "invalid": return "dm_err_invalid";
    case "rate_limited": return "dm_err_rate_limited";
    case "duplicate": return "dm_err_duplicate";
    case "muted": return "dm_err_muted";
    case "not_allowed": case "not_found": return "dm_err_not_allowed";
    case "offline": return "dm_err_offline";
  }
  if (status === 0) return "dm_err_offline";
  return "dm_err_unavailable";
}

/** Erorile dupa care retrimiterea aceluiasi text nu are sens (nu le oferim "atinge pentru retrimitere"). */
export const isPermanentError = (code?: string) => code === "empty" || code === "too_long" || code === "invalid" || code === "not_allowed" || code === "muted";

// ---- afisare ----------------------------------------------------------------------------------

/** "14:05" azi; "Ieri"/"Yesterday"; altfel data scurta. `now` injectabil pentru teste. */
export function formatTime(ts: number, now: number = Date.now(), labels = { yesterday: "Yesterday" }, locale?: string): string {
  const d = new Date(ts);
  const n = new Date(now);
  const startOf = (x: Date) => new Date(x.getFullYear(), x.getMonth(), x.getDate()).getTime();
  const days = Math.round((startOf(n) - startOf(d)) / 86_400_000);
  if (days <= 0) return d.toLocaleTimeString(locale, { hour: "2-digit", minute: "2-digit", hour12: false });
  if (days === 1) return labels.yesterday;
  return d.toLocaleDateString(locale, { day: "2-digit", month: "2-digit", year: days > 300 ? "2-digit" : undefined });
}

export const hourMinute = (ts: number, locale?: string) =>
  new Date(ts).toLocaleTimeString(locale, { hour: "2-digit", minute: "2-digit", hour12: false });

export function newClientId(): string {
  return `c${Date.now().toString(36)}${Math.random().toString(36).slice(2, 10)}`.slice(0, 40);
}