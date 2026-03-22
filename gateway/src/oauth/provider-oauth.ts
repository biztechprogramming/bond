/**
 * OAuth credential management for Anthropic (Claude Max).
 *
 * Wraps @mariozechner/pi-ai's OAuth utilities to:
 * - Detect OAuth tokens (sk-ant-oat)
 * - Refresh expired tokens via pi-ai
 * - Build the extra HTTP headers required by the Anthropic OAuth API
 */

import {
  refreshAnthropicToken,
  anthropicOAuthProvider,
  type OAuthCredentials,
} from "@mariozechner/pi-ai/oauth";
import { readFile, writeFile } from "node:fs/promises";
import { join } from "node:path";
import { homedir } from "node:os";

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
    "user-agent": "claude-cli/2.1.81",
    "x-app": "cli",
    "anthropic-dangerous-direct-browser-access": "true",
  };
}

/** Read the Claude CLI credentials file. */
export async function readClaudeCredentials(): Promise<ClaudeCredentials> {
  const path = join(homedir(), ".claude", ".credentials.json");
  const raw = await readFile(path, "utf-8");
  return JSON.parse(raw) as ClaudeCredentials;
}

/** Write updated credentials back to the Claude CLI credentials file. */
export async function writeClaudeCredentials(creds: ClaudeCredentials): Promise<void> {
  const path = join(homedir(), ".claude", ".credentials.json");
  await writeFile(path, JSON.stringify(creds, null, 2), "utf-8");
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
 * Reads from ~/.claude/.credentials.json, refreshes if expired,
 * writes updated credentials back, and returns the access token.
 */
export async function getValidAccessToken(): Promise<{
  accessToken: string;
  wasRefreshed: boolean;
}> {
  const creds = await readClaudeCredentials();

  if (!isExpired(creds)) {
    return { accessToken: creds.claudeAiOauth.accessToken, wasRefreshed: false };
  }

  // Refresh via pi-ai
  const refreshed = await refreshAnthropicToken(creds.claudeAiOauth.refreshToken);
  const updated = fromPiAiCredentials(refreshed, creds);
  await writeClaudeCredentials(updated);

  return { accessToken: refreshed.access, wasRefreshed: true };
}
