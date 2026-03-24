import { NextResponse } from "next/server";

const GATEWAY_PORT = process.env.NEXT_PUBLIC_GATEWAY_PORT || "18789";

export async function GET() {
  const gatewayUrl = `http://localhost:${GATEWAY_PORT}/api/v1/spacetimedb/token`;
  const stdbUrl = `${process.env.BOND_SPACETIMEDB_URL}/v1/identity/websocket-token`;

  let gatewayToken = null;
  let gatewayError = null;
  let stdbStatus = null;
  let stdbBody = null;

  // 1. Fetch token from gateway
  try {
    const res = await fetch(gatewayUrl, { cache: "no-store" });
    const data = await res.json();
    gatewayToken = data.token || null;
  } catch (err: any) {
    gatewayError = err.message;
  }

  // 2. Test that token against SpacetimeDB (server-side, no CORS)
  if (gatewayToken) {
    try {
      const res = await fetch(stdbUrl, {
        method: "POST",
        headers: { Authorization: `Bearer ${gatewayToken}` },
        cache: "no-store",
      });
      stdbStatus = res.status;
      stdbBody = await res.text();
    } catch (err: any) {
      stdbStatus = "error";
      stdbBody = err.message;
    }
  }

  // 3. Also read CLI token directly for comparison
  let cliToken = null;
  try {
    const fs = await import("fs");
    const toml = fs.readFileSync(
      process.env.HOME + "/.config/spacetime/cli.toml",
      "utf-8"
    );
    const match = toml.match(/spacetimedb_token\s*=\s*"([^"]+)"/);
    cliToken = match ? match[1] : null;
  } catch { }

  return NextResponse.json({
    gateway: {
      url: gatewayUrl,
      token_last20: gatewayToken ? gatewayToken.slice(-20) : null,
      token_length: gatewayToken?.length ?? 0,
      error: gatewayError,
    },
    cli: {
      token_last20: cliToken ? cliToken.slice(-20) : null,
      token_length: cliToken?.length ?? 0,
      tokens_match: gatewayToken === cliToken,
    },
    spacetimedb: {
      url: stdbUrl,
      status: stdbStatus,
      response: stdbBody ? stdbBody.substring(0, 200) : null,
      verdict:
        stdbStatus === 200
          ? "✅ Token accepted"
          : stdbStatus === 401
            ? "❌ Token rejected (401 — JWT keys rotated?)"
            : `⚠️ Unexpected: ${stdbStatus}`,
    },
  });
}
