import { NextResponse } from "next/server";
import { readFileSync } from "fs";
import { homedir } from "os";
import { join } from "path";

/**
 * Server-side endpoint that returns the Bond API key.
 * The key is read from BOND_API_KEY env var or ~/.bond/data/.gateway_key.
 * This keeps the key out of client bundles while letting the browser fetch it.
 */
export async function GET() {
  let key = process.env.BOND_API_KEY || "";
  if (!key) {
    try {
      key = readFileSync(join(homedir(), ".bond", "data", ".gateway_key"), "utf-8").trim();
    } catch {
      return NextResponse.json({ error: "API key not found" }, { status: 500 });
    }
  }
  return NextResponse.json({ key });
}
