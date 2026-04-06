import { NextResponse } from "next/server";
import type { NextRequest } from "next/server";

export function middleware(request: NextRequest) {
  // Check for the JWT token cookie (set as HttpOnly by backend on login)
  const hasAuth = request.cookies.get("pipelineiq_token");

  if (request.nextUrl.pathname === "/" && !hasAuth) {
    return NextResponse.redirect(new URL("/login", request.url));
  }

  return NextResponse.next();
}

export const config = {
  matcher: ["/"],
};
