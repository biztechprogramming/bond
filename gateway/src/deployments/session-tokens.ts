/**
 * User session tokens — separate from agent broker tokens.
 *
 * Two signing keys, two token types:
 *   - User session token: signed with .session_secret, accepted by Promotion API
 *   - Agent broker token: signed with .broker_secret, accepted by /broker/exec and /broker/deploy
 *
 * The Promotion API REJECTS agent broker tokens. This prevents agents from
 * self-promoting scripts.
 */

import crypto from "node:crypto";
import fs from "node:fs";
import path from "node:path";

let SESSION_SECRET: Buffer | null = null;
let sessionDataDir: string = "";

export interface UserSessionPayload {
  user_id: string;
  role: string;
  iat: number;
  exp: number;
  type: "user_session"; // discriminator to distinguish from agent tokens
}

export function initSessionTokens(bondDataDir: string): void {
  sessionDataDir = bondDataDir;
  SESSION_SECRET = null;
}

function getSessionSecret(): Buffer {
  if (SESSION_SECRET) return SESSION_SECRET;
  const secretPath = path.join(sessionDataDir, ".session_secret");
  if (fs.existsSync(secretPath)) {
    SESSION_SECRET = fs.readFileSync(secretPath);
  } else {
    fs.mkdirSync(sessionDataDir, { recursive: true });
    SESSION_SECRET = crypto.randomBytes(32);
    fs.writeFileSync(secretPath, SESSION_SECRET, { mode: 0o600 });
  }
  return SESSION_SECRET;
}

export function issueSessionToken(userId: string, role = "user", ttlSeconds = 86400): string {
  const payload: UserSessionPayload = {
    user_id: userId,
    role,
    iat: Math.floor(Date.now() / 1000),
    exp: Math.floor(Date.now() / 1000) + ttlSeconds,
    type: "user_session",
  };
  const data = Buffer.from(JSON.stringify(payload));
  const sig = crypto.createHmac("sha256", getSessionSecret()).update(data).digest();
  return data.toString("base64url") + "." + sig.toString("base64url");
}

export function validateSessionToken(token: string): UserSessionPayload | null {
  const parts = token.split(".");
  if (parts.length !== 2) return null;
  const [dataStr, sigStr] = parts;
  if (!dataStr || !sigStr) return null;

  const data = Buffer.from(dataStr, "base64url");
  const expected = crypto.createHmac("sha256", getSessionSecret()).update(data).digest();
  const actual = Buffer.from(sigStr, "base64url");

  if (expected.length !== actual.length) return null;
  if (!crypto.timingSafeEqual(expected, actual)) return null;

  try {
    const payload: UserSessionPayload = JSON.parse(data.toString());
    if (payload.exp < Math.floor(Date.now() / 1000)) return null;
    if (payload.type !== "user_session") return null;
    return payload;
  } catch {
    return null;
  }
}

/**
 * Extract caller identity from request.
 * For Phase 1: accepts session tokens, or returns a default identity if no auth.
 * ALWAYS rejects agent broker tokens (tokens whose payload has `sub` not `user_id`).
 */
export function extractUserIdentity(authHeader: string | undefined): {
  user_id: string;
  role: string;
  authenticated: boolean;
} | null {
  if (!authHeader || !authHeader.startsWith("Bearer ")) {
    // No auth — allow in Phase 1 (open system) with default identity
    return { user_id: "user", role: "owner", authenticated: false };
  }

  const token = authHeader.slice(7);

  // Try to decode to check type WITHOUT verifying signature first
  const parts = token.split(".");
  if (parts.length === 2 && parts[0]) {
    try {
      const raw = JSON.parse(Buffer.from(parts[0], "base64url").toString());
      // Agent broker tokens have 'sub' field — reject them
      if ("sub" in raw && !("user_id" in raw)) {
        return null; // agent token — rejected by Promotion API
      }
    } catch {
      // malformed — fall through to session token validation
    }
  }

  // Try session token
  const payload = validateSessionToken(token);
  if (payload) {
    return { user_id: payload.user_id, role: payload.role, authenticated: true };
  }

  // Invalid token
  return null;
}
