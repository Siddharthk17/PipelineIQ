import { NextResponse } from "next/server";

export function GET() {
  const svgIcon =
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64"><rect width="64" height="64" rx="12" fill="#111827"/><path d="M14 18h36v8H14zm0 12h24v8H14zm0 12h16v8H14z" fill="#38bdf8"/></svg>';

  return new NextResponse(svgIcon, {
    status: 200,
    headers: {
      "Cache-Control": "public, max-age=86400",
      "Content-Type": "image/svg+xml",
    },
  });
}
