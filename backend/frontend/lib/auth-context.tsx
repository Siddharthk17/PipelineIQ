"use client";

import React, { createContext, useContext, useEffect, useState, useCallback } from "react";
import { ApiError, getToken, setToken, clearToken, login as apiLogin, logout as apiLogout, getMe, AuthUser as ApiAuthUser } from "./api";

export interface AuthUser {
  id: string;
  email: string;
  username: string;
  role: "admin" | "viewer";
  isDemo: boolean;
}

export interface AuthContextType {
  user: AuthUser | null;
  token: string | null;
  login: (email: string, password: string) => Promise<void>;
  loginAsDemo: () => Promise<void>;
  logout: () => void;
  isLoading: boolean;
}

const AuthContext = createContext<AuthContextType | null>(null);

function toAuthUser(apiUser: ApiAuthUser, isDemo: boolean): AuthUser {
  return {
    id: apiUser.id,
    email: apiUser.email,
    username: apiUser.username,
    role: apiUser.role as "admin" | "viewer",
    isDemo,
  };
}

const DEMO_EMAIL = "demo@pipelineiq.app";
const DEMO_PASSWORD = "Demo1234!";

/** SameSite=None requires Secure, which only works over HTTPS.
 *  In local development (HTTP) the cookie would be silently rejected. */
const SECURE_COOKIE = typeof window !== "undefined" && window.location.protocol === "https:";

function setAuthCookie(value: string, maxAge: number) {
  const secure = SECURE_COOKIE ? "; Secure" : "";
  document.cookie = `piq_auth=${value}; path=/; max-age=${maxAge}; SameSite=Lax${secure}`;
}

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [user, setUser] = useState<AuthUser | null>(null);
  const [token, setTokenState] = useState<string | null>(() => getToken());
  const [isLoading, setIsLoading] = useState(() => Boolean(getToken()));

  useEffect(() => {
    if (!token) return;
    let cancelled = false;
    getMe()
      .then((me) => {
        if (cancelled) return;
        const isDemo = me.email === DEMO_EMAIL;
        setUser(toAuthUser(me, isDemo));
        setAuthCookie("1", 86400);
      })
      .catch((err) => {
        if (cancelled) return;
        if (err instanceof ApiError && err.status === 401) {
          clearToken();
          setTokenState(null);
          setAuthCookie("", 0);
        }
      })
      .finally(() => {
        if (!cancelled) setIsLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [token]);

  const login = useCallback(async (email: string, password: string) => {
    const res = await apiLogin(email, password);
    setToken(res.access_token);
    setTokenState(res.access_token);
    setAuthCookie("1", 86400);
    const me = await getMe();
    const isDemo = me.email === DEMO_EMAIL;
    setUser(toAuthUser(me, isDemo));
  }, []);

  const loginAsDemo = useCallback(async () => {
    await login(DEMO_EMAIL, DEMO_PASSWORD);
  }, [login]);

  const logout = useCallback(() => {
    clearToken();
    setTokenState(null);
    setUser(null);
    setAuthCookie("", 0);
    void apiLogout();
  }, []);

  return (
    <AuthContext.Provider value={{ user, token, login, loginAsDemo, logout, isLoading }}>
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth(): AuthContextType {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be used within AuthProvider");
  return ctx;
}
