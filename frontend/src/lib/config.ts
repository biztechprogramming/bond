/**
 * Shared frontend configuration.
 *
 * Gateway and backend ports are defined here once.
 * Override via environment variables in .env.local if needed:
 *   NEXT_PUBLIC_GATEWAY_PORT=18789
 *   NEXT_PUBLIC_BACKEND_PORT=18790
 */

const GATEWAY_PORT = process.env.NEXT_PUBLIC_GATEWAY_PORT || "18789";
const BACKEND_PORT = process.env.NEXT_PUBLIC_BACKEND_PORT || "18790";

/**
 * Use the browser's hostname so connections work from any device on the LAN
 * (e.g. phone, tablet). Falls back to "localhost" at build time or in
 * non-browser environments (server-side API routes).
 */
const HOST =
  typeof window !== "undefined" ? window.location.hostname : "localhost";

export const GATEWAY_HTTP = `http://${HOST}:${GATEWAY_PORT}`;
export const GATEWAY_WS = `ws://${HOST}:${GATEWAY_PORT}`;
export const GATEWAY_API = `${GATEWAY_HTTP}/api/v1`;
export const BACKEND_API = `http://${HOST}:${BACKEND_PORT}/api/v1`;

/**
 * SpacetimeDB WebSocket endpoint.
 * Connect directly to SpacetimeDB — the Next.js rewrite proxies HTTP fine
 * but doesn't reliably upgrade WebSocket from non-localhost origins.
 * Token is fetched separately via /api/stdb-ws-token (same-origin).
 */
const STDB_PORT = process.env.NEXT_PUBLIC_STDB_PORT || "18787";
const STDB_HOST = process.env.NEXT_PUBLIC_STDB_HOST || HOST;
export const STDB_WS = `ws://${STDB_HOST}:${STDB_PORT}`;

/**
 * Cached Bond API key — fetched once from the same-origin server route.
 */
let _cachedApiKey: string | null = null;

export async function getBondApiKey(): Promise<string> {
  if (_cachedApiKey) return _cachedApiKey;
  try {
    const res = await fetch("/api/bond-key");
    if (res.ok) {
      const data = await res.json();
      _cachedApiKey = data.key;
      return _cachedApiKey!;
    }
  } catch { /* fall through */ }
  return "";
}

/**
 * Build headers with Bearer auth for gateway/backend HTTP calls.
 */
export async function authHeaders(extra?: Record<string, string>): Promise<Record<string, string>> {
  const key = await getBondApiKey();
  const headers: Record<string, string> = { ...extra };
  if (key) headers["Authorization"] = `Bearer ${key}`;
  return headers;
}

/**
 * Authenticated fetch — automatically injects the Bond API key as a Bearer token.
 * Drop-in replacement for `fetch()` in frontend code that calls gateway or backend.
 */
export async function apiFetch(input: RequestInfo | URL, init?: RequestInit): Promise<Response> {
  const key = await getBondApiKey();
  const headers = new Headers(init?.headers);
  if (key && !headers.has("Authorization")) {
    headers.set("Authorization", `Bearer ${key}`);
  }
  return fetch(input, { ...init, headers });
}
