"use client";

import { useEffect } from "react";
import { useThemeStore } from "@/store/themeStore";

export function useTheme() {
  const { activeTheme, setTheme, customThemes } = useThemeStore();

  useEffect(() => {
    document.documentElement.setAttribute("data-theme", activeTheme);

    const customTheme = customThemes.find((t) => t.name === activeTheme);
    if (customTheme) {
      let styleEl = document.getElementById(`theme-${activeTheme}`);
      if (!styleEl) {
        styleEl = document.createElement("style");
        styleEl.id = `theme-${activeTheme}`;
        document.head.appendChild(styleEl);
      }
      const cssVars = Object.entries(customTheme.variables)
        .map(([k, v]) => `${k}: ${v};`)
        .join("\n  ");
      styleEl.innerHTML = `[data-theme="${activeTheme}"] {\n  ${cssVars}\n}`;
    }
  }, [activeTheme, customThemes]);

  return { activeTheme, setTheme };
}
