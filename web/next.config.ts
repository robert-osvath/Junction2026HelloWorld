import type { NextConfig } from "next";

const API = (process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000").replace(/\/$/, "");

// The Milk Run game (public/game) calls the API with relative paths, as when FastAPI serves it.
// Proxy exactly those paths to the API so it runs in LIVE mode here too (server-side, so no CORS).
// Only the exact "/orders" is proxied: "/orders/[id]" is the inbox's own page.
const GAME_API_PATHS = ["/health", "/catalog/search", "/offers", "/jury/evaluate", "/orders", "/audit", "/audit/verify"];

const nextConfig: NextConfig = {
  cacheComponents: true,
  partialPrefetching: true,
  async redirects() {
    // the game loads audio/... relative to its own URL, so it must be opened as /game/index.html
    return [{ source: "/game", destination: "/game/index.html", permanent: false }];
  },
  async rewrites() {
    return GAME_API_PATHS.map((p) => ({ source: p, destination: `${API}${p}` }));
  },
};

export default nextConfig;
