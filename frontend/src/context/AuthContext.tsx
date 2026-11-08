import React, { createContext, useContext, useEffect, useState, useCallback } from "react";
import { authApi, setToken } from "../api/client";

export type User = {
  user_id: string;
  username: string;
  display_name: string;
  email?: string;
  avatar_url?: string;
  age_category: "under_18" | "adult_18";
  astrans_balance: number;
  language: string;
  is_platform_owner: boolean;
  is_platform_admin: boolean;
  // false = cont Google (fara parola de cont): fluxurile de securitate a proiectelor nu cer parola contului
  has_password?: boolean;
  created_at: string;
  online: boolean;
};

type Ctx = {
  user: User | null;
  loading: boolean;
  // true cand serverul nu a putut fi contactat (retea / 5xx) si NU putem sti starea sesiunii.
  // Diferit de "deconectat": tokenul e pastrat, iar UI-ul trebuie sa ofere "Reincearca", nu login.
  connectionError: boolean;
  refresh: () => Promise<void>;
  login: (email: string, password: string) => Promise<void>;
  register: (data: { email: string; password: string; username: string; age_category: "under_18" | "adult_18"; language: string }) => Promise<void>;
  loginWithSessionId: (session_id: string) => Promise<void>;
  logout: () => Promise<void>;
  setUser: (u: User | null) => void;
};

const AuthContext = createContext<Ctx>({
  user: null,
  loading: true,
  connectionError: false,
  refresh: async () => {},
  login: async () => {},
  register: async () => {},
  loginWithSessionId: async () => {},
  logout: async () => {},
  setUser: () => {},
});

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [user, setUser] = useState<User | null>(null);
  const [loading, setLoading] = useState(true);
  const [connectionError, setConnectionError] = useState(false);

  const refresh = useCallback(async () => {
    const { getItemAsync } = await import("expo-secure-store");
    let token: string | null = null;
    try {
      if (typeof window !== "undefined" && (window as any).localStorage) {
        token = (window as any).localStorage.getItem("astran_session_token");
      }
    } catch {}
    if (!token) {
      try { token = await getItemAsync("astran_session_token"); } catch {}
    }
    if (!token) { setUser(null); setConnectionError(false); return; }
    try {
      // Backend-ul gratuit (Render) poate dura zeci de secunde la "cold start": reincercam
      // erorile tranzitorii (retea / 5xx) inainte sa aratam utilizatorul ca deconectat.
      let res: any;
      for (let attempt = 0; ; attempt++) {
        try {
          res = await authApi.me();
          break;
        } catch (err: any) {
          const transient = err?.status === 0 || (typeof err?.status === "number" && err.status >= 500);
          if (!transient || attempt >= 3) throw err;
          await new Promise((r) => setTimeout(r, 2000 * (attempt + 1)));
        }
      }
      setUser(res.user);
      setConnectionError(false);
    } catch (e: any) {
      // Sesiunea se considera invalida DOAR cand serverul o respinge (401/403). La eroare de
      // retea / server indisponibil (status 0) sau 5xx pastram tokenul SI utilizatorul curent:
      // inainte se facea setUser(null) si aplicatia te trimitea pe ecranul de login desi
      // sesiunea era perfect valida (utilizatorul credea ca a fost deconectat).
      if (e?.status === 401 || e?.status === 403) {
        await setToken(null);
        setUser(null);
        setConnectionError(false);
      } else {
        setConnectionError(true);
      }
    }
  }, []);

  useEffect(() => {
    (async () => {
      await refresh();
      setLoading(false);
    })();
  }, [refresh]);

  const login = useCallback(async (email: string, password: string) => {
    const res = await authApi.login({ email, password });
    await setToken(res.token);
    setUser(res.user);
  }, []);

  const register = useCallback(async (data: any) => {
    const res = await authApi.register(data);
    await setToken(res.token);
    setUser(res.user);
  }, []);

  const loginWithSessionId = useCallback(async (session_id: string) => {
    const res = await authApi.session(session_id);
    await setToken(res.session_token);
    setUser(res.user);
  }, []);

  const logout = useCallback(async () => {
    try { await authApi.logout(); } catch {}
    await setToken(null);
    setUser(null);
  }, []);

  return (
    <AuthContext.Provider value={{ user, loading, connectionError, refresh, login, register, loginWithSessionId, logout, setUser }}>
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth() {
  return useContext(AuthContext);
}