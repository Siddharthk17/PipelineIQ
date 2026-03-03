import { create } from "zustand";
import { persist } from "zustand/middleware";

export interface CustomTheme {
  name: string;
  author: string;
  variables: Record<string, string>;
}

interface ThemeState {
  activeTheme: string;
  customThemes: CustomTheme[];
  setTheme: (theme: string) => void;
  addCustomTheme: (theme: CustomTheme) => void;
  removeCustomTheme: (themeName: string) => void;
}

export const useThemeStore = create<ThemeState>()(
  persist(
    (set) => ({
      activeTheme: "pipelineiq-dark",
      customThemes: [],
      setTheme: (theme) => set({ activeTheme: theme }),
      addCustomTheme: (theme) =>
        set((state) => ({
          customThemes: [...state.customThemes.filter((t) => t.name !== theme.name), theme],
        })),
      removeCustomTheme: (themeName) =>
        set((state) => ({
          customThemes: state.customThemes.filter((t) => t.name !== themeName),
        })),
    }),
    { name: "pipelineiq-theme" }
  )
);
