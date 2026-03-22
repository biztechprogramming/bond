/**
 * Integration test for OAuth credential flow via pi-ai.
 *
 * Reads REAL credentials from ~/.claude/.credentials.json, refreshes if
 * expired, and makes an actual API call to Anthropic to prove the flow works.
 */

import { describe, it, expect } from "vitest";
import {
  readClaudeCredentials,
  isExpired,
  isOAuthToken,
  buildOAuthHeaders,
  getValidAccessToken,
  toPiAiCredentials,
  fromPiAiCredentials,
} from "./provider-oauth.js";

describe("provider-oauth", () => {
  it("reads credentials from ~/.claude/.credentials.json", async () => {
    const creds = await readClaudeCredentials();
    expect(creds.claudeAiOauth).toBeDefined();
    expect(creds.claudeAiOauth.accessToken).toBeTruthy();
    expect(creds.claudeAiOauth.refreshToken).toBeTruthy();
    expect(creds.claudeAiOauth.expiresAt).toBeGreaterThan(0);
  });

  it("detects OAuth tokens correctly", () => {
    expect(isOAuthToken("sk-ant-oat01-abc")).toBe(true);
    expect(isOAuthToken("sk-ant-api01-abc")).toBe(false);
    expect(isOAuthToken("some-random-key")).toBe(false);
  });

  it("builds correct OAuth headers", () => {
    const headers = buildOAuthHeaders();
    expect(headers["anthropic-beta"]).toContain("oauth-2025-04-20");
    expect(headers["user-agent"]).toContain("claude-cli");
    expect(headers["x-app"]).toBe("cli");
    expect(headers["anthropic-dangerous-direct-browser-access"]).toBe("true");
  });

  it("converts between credential formats", async () => {
    const creds = await readClaudeCredentials();
    const piCreds = toPiAiCredentials(creds);
    expect(piCreds.access).toBe(creds.claudeAiOauth.accessToken);
    expect(piCreds.refresh).toBe(creds.claudeAiOauth.refreshToken);
    expect(piCreds.expires).toBe(creds.claudeAiOauth.expiresAt);

    const roundTripped = fromPiAiCredentials(piCreds, creds);
    expect(roundTripped.claudeAiOauth.accessToken).toBe(creds.claudeAiOauth.accessToken);
  });

  it("gets a valid access token (refreshing if needed)", async () => {
    const { accessToken, wasRefreshed } = await getValidAccessToken();
    expect(accessToken).toBeTruthy();
    expect(isOAuthToken(accessToken)).toBe(true);
    console.log(`Token ${wasRefreshed ? "was refreshed" : "was still valid"}`);
  }, 30_000);

  it("makes a real API call to Anthropic with OAuth token + headers", async () => {
    const { accessToken } = await getValidAccessToken();
    const headers = buildOAuthHeaders();

    // OAuth tokens use Bearer auth (Authorization header), not x-api-key
    const response = await fetch("https://api.anthropic.com/v1/messages", {
      method: "POST",
      headers: {
        ...headers,
        "content-type": "application/json",
        "authorization": `Bearer ${accessToken}`,
        "anthropic-version": "2023-06-01",
      },
      body: JSON.stringify({
        // Use haiku — some models (sonnet-4) return 400 for OAuth/Max tier
        model: "claude-haiku-4-5-20251001",
        max_tokens: 32,
        messages: [{ role: "user", content: "Say hello in one word." }],
      }),
    });

    const body = await response.json();
    console.log(`API response status: ${response.status}`, JSON.stringify(body, null, 2));
    expect(response.status).toBe(200);
    expect(body.content).toBeDefined();
    expect(body.content.length).toBeGreaterThan(0);
  }, 60_000);
});
