"use client";

import React, { useEffect, useState } from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { useThemeStore } from "@/store/themeStore";

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      refetchOnWindowFocus: false,
      retry: 1,
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
      document.documentElement.setAttribute("data-theme", activeTheme);
      
      // Inject custom theme styles if active theme is a custom one
      const customTheme = customThemes.find(t => t.name === activeTheme);
      if (customTheme) {
        let styleEl = document.getElementById(`theme-${activeTheme}`);
        if (!styleEl) {
          styleEl = document.createElement("style");
          styleEl.id = `theme-${activeTheme}`;
          document.head.appendChild(styleEl);
        }
        const cssVars = Object.entries(customTheme.variables).map(([k, v]) => `${k}: ${v};`).join("\n  ");
        styleEl.innerHTML = `[data-theme="${activeTheme}"] {\n  ${cssVars}\n}`;
      }
    }
  }, [activeTheme, mounted, customThemes]);

  return (
    <QueryClientProvider client={queryClient}>
      {mounted ? children : null}
    </QueryClientProvider>
  );
}
