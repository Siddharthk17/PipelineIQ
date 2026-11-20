import { NextResponse } from "next/server";
import type { NextRequest } from "next/server";

const JWT_SECRET = process.env.AUTH_JWT_SECRET || process.env.SECRET_KEY || "";

function base64UrlToBytes(value: string): Uint8Array {
  const normalized = value.replace(/-/g, "+").replace(/_/g, "/");
  const padded = normalized.padEnd(Math.ceil(normalized.length / 4) * 4, "=");
  const binary = atob(padded);
  const bytes = new Uint8Array(binary.length);
  for (let i = 0; i < binary.length; i += 1) {
    bytes[i] = binary.charCodeAt(i);
  }
  return bytes;
}

function timingSafeEqual(a: Uint8Array, b: Uint8Array): boolean {
  if (a.length !== b.length) return false;
  let diff = 0;
  for (let i = 0; i < a.length; i += 1) {
    diff |= a[i] ^ b[i];
  }
  return diff === 0;
}

async function verifyJwt(token: string): Promise<boolean> {
  if (!JWT_SECRET) return false;
  const parts = token.split(".");
  if (parts.length !== 3) return false;

  // M-136: the edge can only verify authenticity + expiry, not Redis
  // revocation or the DB-backed role. Revoked-token and role checks are
  // enforced fail-closed by the API (backend/auth.get_current_user); the
  // edge additionally requires the claims the API depends on (sub for
  // identity, jti for revocation, role for admin gates) so visibly
  // malformed tokens never reach a page shell.
  let payload: { exp?: number; sub?: unknown; jti?: unknown; role?: unknown; alg?: string };
  try {
    const header = JSON.parse(new TextDecoder().decode(base64UrlToBytes(parts[0])));
    if (header.alg !== "HS256") return false;
    payload = JSON.parse(new TextDecoder().decode(base64UrlToBytes(parts[1])));
  } catch {
    return false;
  }
  if (!payload.exp || payload.exp * 1000 <= Date.now()) return false;
  if (typeof payload.sub !== "string" || payload.sub.length === 0) return false;
  if (typeof payload.jti !== "string" || payload.jti.length === 0) return false;
  if (typeof payload.role !== "string" || payload.role.length === 0) return false;

  const key = await crypto.subtle.importKey(
    "raw",
    new TextEncoder().encode(JWT_SECRET),
    { name: "HMAC", hash: "SHA-256" },
    false,
    ["sign"],
  );
  const expected = new Uint8Array(
    await crypto.subtle.sign(
      "HMAC",
      key,
      new TextEncoder().encode(`${parts[0]}.${parts[1]}`),
    ),
  );
  return timingSafeEqual(expected, base64UrlToBytes(parts[2]));
}

export async function middleware(request: NextRequest) {
  const token = request.cookies.get("pipelineiq_token")?.value;
  if (!token || !(await verifyJwt(token))) {
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
