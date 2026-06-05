"use client";

import React, { useEffect, useState } from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { useThemeStore } from "@/store/themeStore";
import { AuthProvider } from "@/lib/auth-context";
import { ErrorBoundary } from "@/components/ErrorBoundary";
import { buildThemeCss, sanitizeThemeName } from "@/lib/theme-safety";

export function shouldRetryQuery(failureCount: number, error: unknown): boolean {
  const status =
    typeof error === "object" && error !== null && "status" in error
      ? (error as { status?: number }).status
      : undefined;
  if (typeof status === "number" && status >= 400 && status < 500) {
    return false;
  }
  return failureCount < 3;
}

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      refetchOnWindowFocus: false,
      retry: shouldRetryQuery,
    },
    mutations: {
      retry: false,
    },
  },
});

export function Providers({ children }: { children: React.ReactNode }) {
  const { activeTheme, customThemes } = useThemeStore();
  const [mounted, setMounted] = useState(false);

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setMounted(true);
  }, []);

  useEffect(() => {
    if (mounted) {
      // Inject custom theme styles if active theme is a custom one
      const customTheme = customThemes.find(t => t.name === activeTheme);
      document.documentElement.setAttribute(
        "data-theme",
        customTheme ? sanitizeThemeName(activeTheme) : activeTheme,
      );
      if (customTheme) {
        const safeThemeName = sanitizeThemeName(activeTheme);
        let styleEl = document.getElementById(`theme-${safeThemeName}`);
        if (!styleEl) {
          styleEl = document.createElement("style");
          styleEl.id = `theme-${safeThemeName}`;
          document.head.appendChild(styleEl);
        }
        styleEl.textContent = buildThemeCss(safeThemeName, customTheme.variables);
      }
    }
  }, [activeTheme, mounted, customThemes]);

  return (
    <ErrorBoundary>
      <QueryClientProvider client={queryClient}>
        <AuthProvider>
          {mounted ? children : null}
        </AuthProvider>
      </QueryClientProvider>
    </ErrorBoundary>
  );
}
