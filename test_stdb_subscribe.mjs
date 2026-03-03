import WebSocket from "ws";
globalThis.WebSocket = WebSocket;

const { DbConnection } = await import("./gateway/src/spacetimedb/index.js");

console.log("Connecting to SpacetimeDB...");

const conn = DbConnection.builder()
  .withUri("ws://localhost:18787")
  .withDatabaseName("bond-core")
  .onConnect((ctx, identity, token) => {
    console.log("Connected! Identity:", identity.toHexString());
    console.log("Token:", token ? "present" : "none");

    conn.subscriptionBuilder()
      .onApplied(() => {
        const convs = [...conn.db.conversations.iter()];
        console.log("Subscription applied! Conversations:", convs.length);
        convs.forEach(c => console.log("  -", c.id, c.title));
        process.exit(0);
      })
      .onError((ctx) => {
        console.error("Subscription error:", ctx?.event?.toString?.() || ctx?.event);
        process.exit(1);
      })
      .subscribe("SELECT * FROM conversations");
  })
  .onConnectError((ctx, err) => {
    console.error("Connection error:", err);
    process.exit(1);
  })
  .build();

setTimeout(() => {
  console.log("Timeout - no response after 10s");
  process.exit(1);
}, 10000);
