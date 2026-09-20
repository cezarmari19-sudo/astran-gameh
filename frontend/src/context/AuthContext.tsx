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
  created_at: string;
  online: boolean;
};

type Ctx = {
  user: User | null;
  loading: boolean;
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
    if (!token) { setUser(null); return; }
    try {
      const res = await authApi.me();
      setUser(res.user);
    } catch {
      setUser(null);
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
    <AuthContext.Provider value={{ user, loading, refresh, login, register, loginWithSessionId, logout, setUser }}>
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth() {
  return useContext(AuthContext);
}