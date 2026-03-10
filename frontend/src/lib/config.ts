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
