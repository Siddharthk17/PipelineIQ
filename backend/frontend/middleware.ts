import { NextResponse } from "next/server";
import type { NextRequest } from "next/server";

const PROTECTED_ROUTES = new Set(["/", "/dashboard", "/pipelines/new"]);

export function middleware(request: NextRequest) {
  const { pathname } = request.nextUrl;
  const hasAuth = Boolean(request.cookies.get("pipelineiq_token") || request.cookies.get("piq_auth"));

  if (PROTECTED_ROUTES.has(pathname) && !hasAuth) {
    return NextResponse.redirect(new URL("/login", request.url));
  }

  return NextResponse.next();
}

export const config = {
  matcher: ["/", "/dashboard", "/pipelines/new"],
};
