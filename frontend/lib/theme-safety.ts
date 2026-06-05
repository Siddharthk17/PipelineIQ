const THEME_NAME_RE = /[^A-Za-z0-9_-]+/g;
const CSS_VAR_RE = /^--[A-Za-z0-9_-]+$/;
const BLOCKED_VALUE_RE = /[;{}<>]|url\s*\(|@import|expression\s*\(|javascript:/i;

export function sanitizeThemeName(name: string): string {
  const normalized = name.trim().replace(THEME_NAME_RE, "-").replace(/^-+|-+$/g, "");
  return normalized.slice(0, 64) || "custom-theme";
}

export function sanitizeThemeVariables(variables: Record<string, string>): Record<string, string> {
  const safe: Record<string, string> = {};
  for (const [key, rawValue] of Object.entries(variables)) {
    const value = String(rawValue).trim();
    if (!CSS_VAR_RE.test(key)) continue;
    if (!value || value.length > 200 || BLOCKED_VALUE_RE.test(value)) continue;
    safe[key] = value;
  }
  return safe;
}

export function buildThemeCss(themeName: string, variables: Record<string, string>): string {
  const safeName = sanitizeThemeName(themeName);
  const safeVars = sanitizeThemeVariables(variables);
  const cssVars = Object.entries(safeVars)
    .map(([key, value]) => `${key}: ${value};`)
    .join("\n  ");
  return `[data-theme="${safeName}"] {\n  ${cssVars}\n}`;
}
