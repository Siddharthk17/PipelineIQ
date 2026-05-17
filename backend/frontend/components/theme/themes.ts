export const BUILT_IN_THEMES = [
  { id: "catppuccin-mocha", name: "Catppuccin Mocha" },
  { id: "tokyo-night", name: "Tokyo Night" },
  { id: "gruvbox-dark", name: "Gruvbox Dark" },
  { id: "nord", name: "Nord" },
  { id: "rose-pine", name: "Rosé Pine" },
  { id: "pipelineiq-dark", name: "PipelineIQ Dark" },
  { id: "pipelineiq-light", name: "PipelineIQ Light" },
] as const;

export type ThemeId = (typeof BUILT_IN_THEMES)[number]["id"];
