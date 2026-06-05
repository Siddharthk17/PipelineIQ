"use client";

import React, { createContext, useContext, useEffect, useState, useCallback } from "react";
import { ApiError, clearToken, login as apiLogin, logout as apiLogout, getMe, AuthUser as ApiAuthUser } from "./api";

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

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [user, setUser] = useState<AuthUser | null>(null);
  const [token, setTokenState] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    getMe()
      .then((me) => {
        if (cancelled) return;
        const isDemo = me.email === DEMO_EMAIL;
        setUser(toAuthUser(me, isDemo));
      })
      .catch((err) => {
        if (cancelled) return;
        if (err instanceof ApiError && err.status === 401) {
          clearToken();
          setTokenState(null);
        }
      })
      .finally(() => {
        if (!cancelled) setIsLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const login = useCallback(async (email: string, password: string) => {
    const res = await apiLogin(email, password);
    setTokenState(res.access_token);
    const isDemo = res.user.email === DEMO_EMAIL;
    setUser(toAuthUser(res.user, isDemo));
  }, []);

  const loginAsDemo = useCallback(async () => {
    await login(DEMO_EMAIL, DEMO_PASSWORD);
  }, [login]);

  const logout = useCallback(() => {
    clearToken();
    setTokenState(null);
    setUser(null);
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
