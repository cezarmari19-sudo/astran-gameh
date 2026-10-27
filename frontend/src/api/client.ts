import * as SecureStore from "expo-secure-store";
import { Platform } from "react-native";

const BASE = process.env.EXPO_PUBLIC_BACKEND_URL || "";
const TOKEN_KEY = "astran_session_token";

async function getToken(): Promise<string | null> {
  if (Platform.OS === "web") {
    if (typeof window === "undefined") return null;
    return window.localStorage.getItem(TOKEN_KEY);
  }
  return SecureStore.getItemAsync(TOKEN_KEY);
}

export async function setToken(token: string | null): Promise<void> {
  if (Platform.OS === "web") {
    if (typeof window === "undefined") return;
    if (token) window.localStorage.setItem(TOKEN_KEY, token);
    else window.localStorage.removeItem(TOKEN_KEY);
    return;
  }
  if (token) await SecureStore.setItemAsync(TOKEN_KEY, token);
  else await SecureStore.deleteItemAsync(TOKEN_KEY);
}

export async function api<T = any>(
  path: string,
  init: RequestInit = {}
): Promise<T> {
  const token = await getToken();
  const headers: Record<string, string> = {
    "Content-Type": "application/json",
    ...(init.headers as Record<string, string> | undefined),
  };
  if (token) headers.Authorization = `Bearer ${token}`;
  const res = await fetch(`${BASE}/api${path}`, { ...init, headers });
  const text = await res.text();
  let data: any = null;
  try {
    data = text ? JSON.parse(text) : null;
  } catch {
    data = { raw: text };
  }
  if (!res.ok) {
    const message = data?.detail || data?.message || `Request failed: ${res.status}`;
    const err = new Error(typeof message === "string" ? message : JSON.stringify(message));
    (err as any).status = res.status;
    throw err;
  }
  return data as T;
}

// Construieste link-ul catre WebSocket-ul de multiplayer live (vezi backend/realtime.py).
// Montat direct pe `app` (NU sub /api, vezi server.py) - de-aia `path` aici e absolut
// ("/ws/games/..."), nu trece prin acelasi prefix ca restul lui `api()` de mai sus.
// Token-ul merge ca query param: un WebSocket din React Native nu poate seta header-ul
// Authorization la handshake, asa ca serverul il decodeaza manual (vezi resolve_user_from_token).
export async function wsUrl(path: string): Promise<string> {
  const token = await getToken();
  const wsBase = BASE.replace(/^http/i, "ws"); // http(s):// -> ws(s)://
  const sep = path.includes("?") ? "&" : "?";
  return `${wsBase}${path}${sep}token=${encodeURIComponent(token ?? "")}`;
}

export const authApi = {
  register: (body: any) => api("/auth/register", { method: "POST", body: JSON.stringify(body) }),
  login: (body: any) => api("/auth/login", { method: "POST", body: JSON.stringify(body) }),
  session: (session_id: string) => api("/auth/session", { method: "POST", body: JSON.stringify({ session_id }) }),
  me: () => api("/auth/me"),
  logout: () => api("/auth/logout", { method: "POST" }),
};

// SECURITY: stergere/recuperare proiect + parola de securitate a proiectului. Oglindeste
// 1:1 endpointurile din backend/server.py (vezi project_security.py) - fiecare verificare
// reala (Owner, parola, email, biometrie) se intampla pe server; clientul doar colecteaza
// datele si afiseaza rezultatul, nu decide niciodata singur daca o actiune e permisa.
export const projectSecurityApi = {
  deleteProject: (gameId: string, projectPassword: string) =>
    api(`/games/${gameId}`, { method: "DELETE", body: JSON.stringify({ project_password: projectPassword, confirm: true }) }),
  setupPassword: (gameId: string, body: { account_password: string; project_password: string; project_password_confirm: string }) =>
    api(`/games/${gameId}/security/password/setup`, { method: "POST", body: JSON.stringify(body) }),
  listTrash: () => api("/games/trash"),

  recoverStart: (gameId: string, accountPassword: string) =>
    api(`/games/${gameId}/security/recover/start`, { method: "POST", body: JSON.stringify({ account_password: accountPassword }) }),
  recoverVerifyEmail: (gameId: string, tokenId: string, code: string) =>
    api(`/games/${gameId}/security/recover/verify-email`, { method: "POST", body: JSON.stringify({ token_id: tokenId, code }) }),
  recoverConfirm: (gameId: string, tokenId: string) =>
    api(`/games/${gameId}/security/recover/confirm`, { method: "POST", body: JSON.stringify({ token_id: tokenId }) }),

  resetPasswordStart: (gameId: string, accountPassword: string) =>
    api(`/games/${gameId}/security/password/reset/start`, { method: "POST", body: JSON.stringify({ account_password: accountPassword }) }),
  resetPasswordVerifyEmail: (gameId: string, tokenId: string, code: string) =>
    api(`/games/${gameId}/security/password/reset/verify-email`, { method: "POST", body: JSON.stringify({ token_id: tokenId, code }) }),
  resetPasswordConfirm: (gameId: string, tokenId: string, newPw: string, newPwConfirm: string) =>
    api(`/games/${gameId}/security/password/reset/confirm`, {
      method: "POST",
      body: JSON.stringify({ token_id: tokenId, new_project_password: newPw, new_project_password_confirm: newPwConfirm }),
    }),

  webauthnForToken: (gameId: string, tokenId: string, challengeId: string, credential: string) =>
    api(`/games/${gameId}/security/webauthn/verify?token_id=${encodeURIComponent(tokenId)}`, {
      method: "POST",
      body: JSON.stringify({ challenge_id: challengeId, credential }),
    }),
  webauthnAuthOptions: () => api("/auth/webauthn/authenticate/options", { method: "POST" }),
};