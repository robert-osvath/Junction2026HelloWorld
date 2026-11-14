import { NextResponse, type NextRequest } from "next/server";

// UX only: sends visitors without the demo cookie to /unlock. The API enforces the gate itself.
export function proxy(req: NextRequest) {
  if (req.cookies.has("aimo_demo")) return NextResponse.next();
  const { pathname, search } = req.nextUrl;
  const url = req.nextUrl.clone();
  url.pathname = "/unlock";
  url.search = `?next=${encodeURIComponent(pathname + search)}`;
  return NextResponse.redirect(url);
}

// Skip: unlock page, game, Next internals, favicon, proxied API paths, and static files (except .html).
export const config = {
  matcher: [
    "/((?!unlock|game|_next|favicon\\.ico|auth|experience|orders|catalog/search|health|offers|jury/evaluate|substitutes|audit|planning)(?!.*\\.(?!html$)[^/]+$).*)",
  ],
};
