"use client";

import { useEffect } from "react";
import { connectToSpacetimeDB } from "@/lib/spacetimedb-client";
import { STDB_WS } from "@/lib/config";

/**
 * Initializes the SpacetimeDB WebSocket connection once at the root layout level.
 * Every page inherits this connection — no need to call connectToSpacetimeDB per-page.
 */
export default function SpacetimeDBProvider({ children }: { children: React.ReactNode }) {
  useEffect(() => {
    connectToSpacetimeDB(STDB_WS).catch((err) => {
      console.error("[SpacetimeDBProvider] Connection failed:", err);
    });
  }, []);

  return <>{children}</>;
}
