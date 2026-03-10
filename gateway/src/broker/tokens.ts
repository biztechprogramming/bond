/**
 * Permission Broker — HMAC token issue/validate.
 */

import crypto from "node:crypto";
import fs from "node:fs";
import path from "node:path";
import type { AgentTokenPayload } from "./types.js";

let BROKER_SECRET: Buffer | null = null;
let dataDir: string = "";

export function initTokens(bondDataDir: string): void {
  dataDir = bondDataDir;
  BROKER_SECRET = null; // reset cached secret
}

export function getSecret(): Buffer {
  if (BROKER_SECRET) return BROKER_SECRET;
  const secretPath = path.join(dataDir, ".broker_secret");
  if (fs.existsSync(secretPath)) {
    BROKER_SECRET = fs.readFileSync(secretPath);
  } else {
    fs.mkdirSync(dataDir, { recursive: true });
    BROKER_SECRET = crypto.randomBytes(32);
    fs.writeFileSync(secretPath, BROKER_SECRET, { mode: 0o600 });
  }
  return BROKER_SECRET;
}

export function issueToken(agentId: string, sessionId: string, ttlSeconds = 3600): string {
  const payload: AgentTokenPayload = {
    sub: agentId,
    sid: sessionId,
    iat: Math.floor(Date.now() / 1000),
    exp: Math.floor(Date.now() / 1000) + ttlSeconds,
  };
  const data = Buffer.from(JSON.stringify(payload));
  const sig = crypto.createHmac("sha256", getSecret()).update(data).digest();
  return data.toString("base64url") + "." + sig.toString("base64url");
}

export function validateToken(token: string): AgentTokenPayload | null {
  const parts = token.split(".");
  if (parts.length !== 2) return null;
  const [dataStr, sigStr] = parts;
  if (!dataStr || !sigStr) return null;

  const data = Buffer.from(dataStr, "base64url");
  const expected = crypto.createHmac("sha256", getSecret()).update(data).digest();
  const actual = Buffer.from(sigStr, "base64url");

  if (expected.length !== actual.length) return null;
  if (!crypto.timingSafeEqual(expected, actual)) return null;

  try {
    const payload: AgentTokenPayload = JSON.parse(data.toString());
    if (payload.exp < Math.floor(Date.now() / 1000)) return null;
    return payload;
  } catch {
    return null;
  }
}
