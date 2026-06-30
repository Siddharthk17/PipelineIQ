import { NextResponse } from "next/server";
import type { NextRequest } from "next/server";

function base64UrlToJson(value: string): Record<string, unknown> | null {
  try {
    const normalized = value.replace(/-/g, "+").replace(/_/g, "/");
    const padded = normalized.padEnd(Math.ceil(normalized.length / 4) * 4, "=");
    const binary = atob(padded);
    const bytes = new Uint8Array(binary.length);
    for (let i = 0; i < binary.length; i += 1) {
      bytes[i] = binary.charCodeAt(i);
    }
    return JSON.parse(new TextDecoder().decode(bytes));
  } catch {
    return null;
  }
}

function hasUnexpiredToken(token: string | undefined): boolean {
  if (!token) return false;
  const parts = token.split(".");
  if (parts.length !== 3) return false;
  const payload = base64UrlToJson(parts[1]);
  const exp = typeof payload?.exp === "number" ? payload.exp : 0;
  return exp * 1000 > Date.now();
}

export function middleware(request: NextRequest) {
  const token = request.cookies.get("pipelineiq_token")?.value;
  if (!hasUnexpiredToken(token)) {
    return NextResponse.redirect(new URL("/login", request.url));
  }
  return NextResponse.next();
}

export const config = {
  matcher: [
    "/",
    "/dashboard/:path*",
    "/pipelines/:path*",
    "/storage/:path*",
    "/runs/:path*",
    "/files/:path*",
    "/catalog/:path*",
    "/templates/:path*",
    "/schedules/:path*",
    "/wasm-modules/:path*",
  ],
};
