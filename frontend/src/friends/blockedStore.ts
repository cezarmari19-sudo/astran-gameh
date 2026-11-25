// Starea listei „Utilizatori blocați” ca functii PURE (fara React, fara retea) - testabile izolat
// (vezi blockedStore.test.mts). Serverul decide totul (autorizare, cine poate debloca ce); clientul doar
// afiseaza lista primita si scoate local randul dupa un raspuns de succes.

export type BlockedUser = {
  user_id: string;
  username: string;
  display_name?: string | null;
  avatar_url?: string | null;
};

export type UnblockErrorKey = "unblock_rate_limited" | "dm_err_offline" | "unblock_failed";

/** Normalizeaza raspunsul GET /friends/blocked: doar randurile valide, fara duplicate. */
export function normalizeBlocked(payload: unknown): BlockedUser[] {
  const raw = (payload as { blocked?: unknown } | null)?.blocked;
  if (!Array.isArray(raw)) return [];
  const seen = new Set<string>();
  const out: BlockedUser[] = [];
  for (const r of raw) {
    if (!r || typeof r.user_id !== "string" || typeof r.username !== "string" || seen.has(r.user_id)) continue;
    seen.add(r.user_id);
    out.push({ user_id: r.user_id, username: r.username, display_name: r.display_name ?? null, avatar_url: r.avatar_url ?? null });
  }
  return out;
}

/** Lista fara utilizatorul deblocat (imutabil; id inexistent => aceeasi lista). */
export const removeBlocked = (list: BlockedUser[], userId: string): BlockedUser[] =>
  list.some((u) => u.user_id === userId) ? list.filter((u) => u.user_id !== userId) : list;

/** `not_blocked` (404) = utilizatorul nu mai e blocat (ex. deblocat de pe alt dispozitiv): pentru UI e acelasi
 * rezultat dorit, deci se trateaza ca succes si lista se reimprospateaza. */
export const isAlreadyUnblocked = (code: string | undefined, status?: number): boolean =>
  code === "not_blocked" || (status === 404 && code === undefined);

/** Eroarea la POST /friends/unblock -> cheie i18n. Fara retea => offline; 429 => prea multe incercari. */
export function unblockErrorKey(code: string | undefined, status?: number): UnblockErrorKey {
  if (status === 0) return "dm_err_offline";
  if (status === 429) return "unblock_rate_limited";
  return "unblock_failed";
}

export const BLOCKED_I18N_KEYS = [
  "blocked_users", "blocked_users_empty", "blocked_users_hint", "unblock", "unblock_confirm", "unblock_confirm_body",
  "unblock_success", "unblock_failed", "unblock_rate_limited", "blocked_load_failed",
] as const;
