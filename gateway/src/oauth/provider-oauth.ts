/**
 * OAuth credential management for Anthropic (Claude Max).
 *
 * Supports all three platforms:
 *   macOS  — system Keychain via /usr/bin/security (service: "Claude Code-credentials")
 *   Linux  — ~/.claude/.credentials.json
 *   Windows — ~/.claude/.credentials.json
 *
 * Claude Code (v2.1.100+) stores credentials as hex-encoded JSON in the macOS
 * Keychain after `claude auth login`. Older installs may store raw JSON.
 * We detect the encoding on read and preserve it on write.
 *
 * Keychain service name derived from Meridian (github.com/rynfar/meridian):
 * https://github.com/rynfar/meridian/blob/main/src/proxy/tokenRefresh.ts
 */

import {
  refreshAnthropicToken,
  anthropicOAuthProvider,
  type OAuthCredentials,
} from "@mariozechner/pi-ai/oauth";
import { execFile as execFileCb } from "node:child_process";
import { existsSync, readFileSync, writeFileSync, mkdirSync } from "node:fs";
import { readFile, writeFile } from "node:fs/promises";
import { join, dirname } from "node:path";
import { homedir, platform, userInfo } from "node:os";
import { promisify } from "node:util";

const execFile = promisify(execFileCb);

const KEYCHAIN_SERVICE = "Claude Code-credentials";
const CREDENTIALS_FILE = join(homedir(), ".claude", ".credentials.json");

/** Claude CLI credentials file format (differs from pi-ai's OAuthCredentials). */
export interface ClaudeCredentials {
  claudeAiOauth: {
    accessToken: string;
    refreshToken: string;
    expiresAt: number; // epoch ms
    scopes?: string[];
    subscriptionType?: string;
    rateLimitTier?: string;
  };
  [key: string]: unknown;
}

/** Convert Claude CLI credentials → pi-ai OAuthCredentials. */
export function toPiAiCredentials(creds: ClaudeCredentials): OAuthCredentials {
  const o = creds.claudeAiOauth;
  return {
    access: o.accessToken,
    refresh: o.refreshToken,
    expires: o.expiresAt,
  };
}

/** Convert pi-ai OAuthCredentials → Claude CLI credentials (preserving extra fields). */
export function fromPiAiCredentials(
  piCreds: OAuthCredentials,
  original: ClaudeCredentials,
): ClaudeCredentials {
  return {
    ...original,
    claudeAiOauth: {
      ...original.claudeAiOauth,
      accessToken: piCreds.access,
      refreshToken: piCreds.refresh,
      expiresAt: piCreds.expires,
    },
  };
}

/** Detect an OAuth token by its prefix. */
export function isOAuthToken(key: string): boolean {
  return key.startsWith("sk-ant-oat");
}

/**
 * Build the extra HTTP headers required to use an OAuth token with
 * the Anthropic Messages API.
 */
export function buildOAuthHeaders(): Record<string, string> {
  return {
    "anthropic-beta": "claude-code-20250219,oauth-2025-04-20",
    "user-agent": "claude-cli/2.1.148",
    "x-app": "cli",
    "anthropic-dangerous-direct-browser-access": "true",
  };
}

// ---------------------------------------------------------------------------
// Platform-specific credential storage
// ---------------------------------------------------------------------------

/**
 * Parse a raw Keychain value as credentials.
 * Claude Code (v2.1.100+) stores hex-encoded JSON; older installs use raw JSON.
 * Returns the parsed credentials and whether the value was hex-encoded.
 */
function parseKeychainValue(raw: string): { creds: ClaudeCredentials; wasHex: boolean } | null {
  const trimmed = raw.trim();
  // Try raw JSON first
  try {
    return { creds: JSON.parse(trimmed) as ClaudeCredentials, wasHex: false };
  } catch {}
  // Try hex-decoded JSON (Claude Code's format after `claude auth login`)
  try {
    const decoded = Buffer.from(trimmed, "hex").toString("utf-8");
    return { creds: JSON.parse(decoded) as ClaudeCredentials, wasHex: true };
  } catch {}
  return null;
}

// Track per-read whether the Keychain value was hex-encoded so we preserve the
// same format on write. Module-level is safe because only one refresh runs at
// a time (getValidAccessToken is not called concurrently in normal operation).
let _keychainWasHex = false;

/** Read credentials from the macOS Keychain. */
async function readFromKeychain(): Promise<ClaudeCredentials | null> {
  try {
    const { stdout } = await execFile(
      "/usr/bin/security",
      ["find-generic-password", "-s", KEYCHAIN_SERVICE, "-a", userInfo().username, "-w"],
      { timeout: 5000 },
    );
    const parsed = parseKeychainValue(stdout);
    if (!parsed) return null;
    _keychainWasHex = parsed.wasHex;
    return parsed.creds;
  } catch {
    return null;
  }
}

/** Write credentials to the macOS Keychain, preserving the original encoding. */
async function writeToKeychain(creds: ClaudeCredentials): Promise<void> {
  // MUST be compact JSON — Claude Code cannot parse pretty-printed credentials.
  const json = JSON.stringify(creds);
  const value = _keychainWasHex ? Buffer.from(json).toString("hex") : json;
  await execFile(
    "/usr/bin/security",
    ["add-generic-password", "-U", "-s", KEYCHAIN_SERVICE, "-a", userInfo().username, "-w", value],
    { timeout: 5000 },
  );
}

/** Read credentials from the file-based store (~/.claude/.credentials.json). */
async function readFromFile(): Promise<ClaudeCredentials | null> {
  try {
    if (!existsSync(CREDENTIALS_FILE)) return null;
    return JSON.parse(readFileSync(CREDENTIALS_FILE, "utf-8")) as ClaudeCredentials;
  } catch {
    return null;
  }
}

/** Write credentials to the file-based store. */
async function writeToFile(creds: ClaudeCredentials): Promise<void> {
  mkdirSync(dirname(CREDENTIALS_FILE), { recursive: true });
  // Compact JSON — preserves Claude Code compatibility.
  writeFileSync(CREDENTIALS_FILE, JSON.stringify(creds), "utf-8");
}

/** Read Claude credentials from the platform-appropriate store. */
export async function readClaudeCredentials(): Promise<ClaudeCredentials | null> {
  if (platform() === "darwin") {
    // macOS: prefer Keychain, fall back to file (covers older Claude Code versions)
    const keychainCreds = await readFromKeychain();
    if (keychainCreds) return keychainCreds;
  }
  return readFromFile();
}

/** Write updated credentials back to the platform-appropriate store. */
export async function writeClaudeCredentials(creds: ClaudeCredentials): Promise<void> {
  if (platform() === "darwin") {
    try {
      await writeToKeychain(creds);
      return;
    } catch {
      // Fall through to file if Keychain write fails
    }
  }
  await writeToFile(creds);
}

/** Check whether the stored token is expired (with 60s buffer). */
export function isExpired(creds: ClaudeCredentials): boolean {
  return creds.claudeAiOauth.expiresAt < Date.now() + 60_000;
}

/**
 * Refresh an expired OAuth credential via pi-ai.
 * Returns updated OAuthCredentials in pi-ai format.
 */
export async function refreshOAuthCredential(
  refreshToken: string,
): Promise<OAuthCredentials> {
  return refreshAnthropicToken(refreshToken);
}

/**
 * Get a valid access token, refreshing if necessary.
 *
 * Reads from the platform credential store (macOS Keychain or
 * ~/.claude/.credentials.json), refreshes if expired, writes updated
 * credentials back, and returns the access token.
 *
 * Returns null if no credentials are found.
 */
export async function getValidAccessToken(): Promise<{
  accessToken: string;
  wasRefreshed: boolean;
} | null> {
  const creds = await readClaudeCredentials();
  if (!creds) return null;

  if (!isExpired(creds)) {
    return { accessToken: creds.claudeAiOauth.accessToken, wasRefreshed: false };
  }

  // Refresh via pi-ai
  const refreshed = await refreshAnthropicToken(creds.claudeAiOauth.refreshToken);
  const updated = fromPiAiCredentials(refreshed, creds);
  await writeClaudeCredentials(updated);

  return { accessToken: refreshed.access, wasRefreshed: true };
}

// Re-export for consumers that import anthropicOAuthProvider
export { anthropicOAuthProvider };
