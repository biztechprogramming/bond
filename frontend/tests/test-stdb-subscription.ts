/**
 * Test script that replicates what the browser does when loading the settings page.
 * 
 * Mimics: SpacetimeDBProvider → connectToSpacetimeDB → subscribe to ALL tables → getAgents()
 * 
 * Run: cd frontend && npx tsx tests/test-stdb-subscription.ts
 */

import { DbConnection } from '../src/lib/spacetimedb/index.js';

const STDB_URI = 'ws://localhost:18787';
const MODULE = 'bond-core-v2';
const TOKEN_URL = 'http://localhost:18788/api/stdb-ws-token';

// The exact subscription list from spacetimedb-client.ts
const SUBSCRIPTION_QUERIES = [
  "SELECT * FROM conversations",
  "SELECT * FROM conversation_messages",
  "SELECT * FROM work_plans",
  "SELECT * FROM work_items",
  "SELECT * FROM agents",
  "SELECT * FROM agent_channels",
  "SELECT * FROM agent_workspace_mounts",
  "SELECT * FROM llm_models",
  "SELECT * FROM providers",
  "SELECT * FROM provider_aliases",
  "SELECT * FROM settings",
  "SELECT * FROM provider_api_keys",
  "SELECT * FROM prompt_fragments",
  "SELECT * FROM prompt_templates",
  "SELECT * FROM prompt_fragment_versions",
  "SELECT * FROM prompt_template_versions",
  "SELECT * FROM agent_prompt_fragments",
  "SELECT * FROM resources",
  "SELECT * FROM components",
  "SELECT * FROM environments",
  "SELECT * FROM component_resources",
  "SELECT * FROM alerts",
  "SELECT * FROM alert_rules",
  "SELECT * FROM resource_environments",
];

async function main() {
  console.log("=== SpacetimeDB Subscription Test ===\n");

  // Step 1: Fetch token (like browser does)
  console.log("1. Fetching STDB token from", TOKEN_URL);
  let token: string | null = null;
  try {
    const res = await fetch(TOKEN_URL, { method: 'POST' });
    if (res.ok) {
      const data = await res.json() as { token?: string };
      token = data.token || null;
      console.log(`   ✓ Got token (length: ${token?.length})\n`);
    } else {
      console.log(`   ✗ Token request failed: ${res.status}\n`);
    }
  } catch (err) {
    console.log(`   ⚠ Token fetch skipped: ${(err as Error).message}\n`);
  }

  // Step 2: Connect (exactly like connectToSpacetimeDB)
  console.log("2. Connecting to SpacetimeDB at", STDB_URI);
  
  const startTime = Date.now();

  const conn = await new Promise<DbConnection>((resolve, reject) => {
    let builder = DbConnection.builder()
      .withUri(STDB_URI)
      .withDatabaseName(MODULE);

    if (token) {
      builder = builder.withToken(token);
    }

    const conn = builder
      .onConnect((ctx: any, identity: any) => {
        const elapsed = Date.now() - startTime;
        console.log(`   ✓ Connected in ${elapsed}ms, identity: ${identity.toHexString()}\n`);

        // Step 3: Subscribe to ALL tables (exactly like the frontend)
        console.log("3. Subscribing to", SUBSCRIPTION_QUERIES.length, "tables...");

        ctx.subscriptionBuilder()
          .onApplied(() => {
            const elapsed2 = Date.now() - startTime;
            console.log(`   ✓ Subscription applied in ${elapsed2}ms\n`);

            // Step 4: Count rows in key tables
            console.log("4. Reading table data:");
            const tables = [
              { name: "agents", iter: () => ctx.db.agents.iter() },
              { name: "agent_channels", iter: () => ctx.db.agent_channels.iter() },
              { name: "agent_workspace_mounts", iter: () => ctx.db.agent_workspace_mounts.iter() },
              { name: "conversations", iter: () => ctx.db.conversations.iter() },
              { name: "llm_models", iter: () => ctx.db.llm_models.iter() },
              { name: "providers", iter: () => ctx.db.providers.iter() },
              { name: "settings", iter: () => ctx.db.settings.iter() },
              { name: "work_plans", iter: () => ctx.db.workPlans?.iter() },
              { name: "work_items", iter: () => ctx.db.workItems?.iter() },
              { name: "resources", iter: () => ctx.db.resources.iter() },
              { name: "components", iter: () => ctx.db.components.iter() },
              { name: "environments", iter: () => ctx.db.environments.iter() },
              { name: "alerts", iter: () => ctx.db.alerts.iter() },
              { name: "resource_environments", iter: () => ctx.db.resource_environments.iter() },
            ];

            let failed = false;
            for (const t of tables) {
              try {
                const iter = t.iter();
                if (!iter) {
                  console.log(`   ⚠ ${t.name}: table accessor undefined (binding mismatch)`);
                  continue;
                }
                const rows = [...iter];
                const symbol = rows.length > 0 ? "✓" : "○";
                console.log(`   ${symbol} ${t.name}: ${rows.length} rows`);
                
                // Print agent details
                if (t.name === "agents" && rows.length > 0) {
                  for (const row of rows) {
                    const r = row as any;
                    console.log(`     - ${r.name} (${r.displayName || r.display_name}) default=${r.isDefault ?? r.is_default}`);
                  }
                }

                // Flag if critical tables are unexpectedly large
                if (t.name === "llm_models" && rows.length > 500) {
                  console.log(`   ⚠ WARNING: ${rows.length} models is excessive — likely duplicates. Run deduplicate_models reducer.`);
                  failed = true;
                }
              } catch (err) {
                console.log(`   ✗ ${t.name}: ERROR — ${(err as Error).message}`);
              }
            }

            // Verify agents are present
            try {
              const agents = [...ctx.db.agents.iter()];
              if (agents.length === 0) {
                console.error("\n=== TEST FAILED — no agents found ===");
                failed = true;
              }
            } catch (err) {
              console.error("\n=== TEST FAILED — could not read agents:", (err as Error).message, "===");
              failed = true;
            }

            if (failed) {
              console.error("\n=== TEST FAILED ===");
              process.exit(1);
            } else {
              console.log("\n=== TEST PASSED ===");
            }
            resolve(conn);
          })
          .onError((errCtx: any) => {
            const elapsed2 = Date.now() - startTime;
            console.error(`   ✗ Subscription ERROR after ${elapsed2}ms:`, errCtx?.event);
            console.error("\n=== TEST FAILED — subscription error ===");
            reject(new Error("Subscription failed"));
          })
          .subscribe(SUBSCRIPTION_QUERIES);
      })
      .onConnectError((_ctx: any, err: any) => {
        console.error(`   ✗ Connection failed:`, err);
        reject(err);
      })
      .build();
  });

  // Cleanup
  setTimeout(() => process.exit(0), 500);
}

// Timeout
setTimeout(() => {
  console.error("\n=== TEST FAILED — timed out after 15s ===");
  console.error("The subscription never completed. This matches the browser behavior.");
  process.exit(1);
}, 15000);

main().catch((err) => {
  console.error("Fatal:", err.message);
  process.exit(1);
});
