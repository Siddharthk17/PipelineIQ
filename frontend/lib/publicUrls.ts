// Public-URL helpers for proxied services (Jaeger, Yjs, ...).
//
// Browsers must reach these through the page's own host (e.g.
// https://demo.example.com/jaeger), never through a hardcoded localhost
// that only exists on the developer's machine or inside Docker.
// Explicit NEXT_PUBLIC_* env values always win; localhost dev keeps direct
// ports; anything else resolves to the same-origin proxied subpath.

function isLocalHost(host: string): boolean {
  return host === "localhost" || host === "127.0.0.1" || host === "[::1]";
}

function sameOriginSubpath(publicPath: string, ws: boolean): string | null {
  if (typeof window === "undefined") return null;
  const host = window.location.hostname;
  if (!host || isLocalHost(host)) return null;
  const proto = window.location.protocol === "https:"
    ? (ws ? "wss:" : "https:")
    : (ws ? "ws:" : "http:");
  return `${proto}//${window.location.host}${publicPath}`;
}

export function getJaegerUiUrl(): string {
  return (
    process.env.NEXT_PUBLIC_JAEGER_UI_URL ||
    sameOriginSubpath("/jaeger", false) ||
    "http://localhost:16686"
  );
}

export function getYjsServerUrl(): string {
  return (
    process.env.NEXT_PUBLIC_YJS_URL ||
    sameOriginSubpath("/yjs", true) ||
    "ws://localhost:1234"
  );
}
