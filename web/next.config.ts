import type { NextConfig } from "next";

const API = (process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000").replace(/\/$/, "");

// Browser code (inbox, cockpit /vimo, game) calls the API with same-origin relative paths, so the
// HttpOnly aimo_demo cookie lives on the web domain. Proxy exactly those paths to the API server-side.
// "/orders" and "/orders/:path*" are API-only: the inbox pages live under /approvals.
const API_PATHS = ["/auth/:path*", "/experience/:path*", "/orders", "/orders/:path*", "/catalog/search", "/health", "/offers", "/jury/evaluate", "/substitutes", "/audit", "/audit/verify", "/planning/:path*"];

const nextConfig: NextConfig = {
  cacheComponents: true,
  partialPrefetching: true,
  async redirects() {
    // the game and cockpit load audio/... relative to its own URL, so it must be opened as /game/index.html
    return [
      { source: "/game", destination: "/game/index.html", permanent: false },
      { source: "/vimo", destination: "/vimo/index.html", permanent: false },
    ];
  },
  async rewrites() {
    return API_PATHS.map((p) => ({ source: p, destination: `${API}${p}` }));
  },
};

export default nextConfig;
