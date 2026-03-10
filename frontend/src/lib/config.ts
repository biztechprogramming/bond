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

export const GATEWAY_HTTP = `http://localhost:${GATEWAY_PORT}`;
export const GATEWAY_WS = `ws://localhost:${GATEWAY_PORT}`;
export const GATEWAY_API = `${GATEWAY_HTTP}/api/v1`;
export const BACKEND_API = `http://localhost:${BACKEND_PORT}/api/v1`;
