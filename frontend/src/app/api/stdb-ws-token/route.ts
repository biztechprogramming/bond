import { NextRequest, NextResponse } from "next/server";
import { readFileSync } from "fs";

/**
 * Proxy for SpacetimeDB's /v1/identity/websocket-token endpoint.
 * The browser can't call SpacetimeDB directly due to CORS.
 * This same-origin endpoint reads the CLI token and exchanges it server-side.
 */
export async function POST(req: NextRequest) {
  // Read the CLI token
  let cliToken: string | null = null;
  try {
    const toml = readFileSync(
      process.env.HOME + "/.config/spacetime/cli.toml",
      "utf-8"
    );
    const match = toml.match(/spacetimedb_token\s*=\s*"([^"]+)"/);
    cliToken = match ? match[1] : null;
  } catch { }

  if (!cliToken) {
    return NextResponse.json({ error: "No CLI token found" }, { status: 500 });
  }

  // Exchange it with SpacetimeDB server-side (no CORS issue)
  const stdbUrl = process.env.BOND_SPACETIMEDB_URL;
  const stdbRes = await fetch(`${stdbUrl}/v1/identity/websocket-token`, {
    method: "POST",
    headers: { Authorization: `Bearer ${cliToken}` },
    cache: "no-store",
  });

  const body = await stdbRes.text();
  return new NextResponse(body, {
    status: stdbRes.status,
    headers: { "Content-Type": "application/json" },
  });
}
