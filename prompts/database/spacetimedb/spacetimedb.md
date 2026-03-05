# SpacetimeDB

## Core Architecture
- **Transactionality**: Every reducer call is a single transaction. If it fails, all state changes are rolled back.
- **Relational Model**: Use tables and indices just like a traditional DB, but mutations happen via Reducers.
- **Server-Side Logic**: Reducers are the only way to mutate state. Business logic belongs in the SpacetimeDB module.

## Bond Integration
- **Gateway Pattern**: The Bond gateway is the sole authorized writer. Workers and agents should read but not attempt to call reducers directly unless authorized.
- **Bindings**: After any `spacetime publish`, you MUST run the bindings generation and copy step:
  ```bash
  spacetime generate bond-core-v2 --lang typescript --out-dir ./bindings
  cp -r ./bindings/. ~/bond/frontend/src/lib/spacetimedb/
  ```
- **HTTP vs WS**: Use the HTTP API for Node.js services for better reliability. Use the WS SDK for frontend real-time updates.

## Best Practices
- **Positional Arguments**: Reducer arguments in HTTP calls are positional JSON arrays: `[arg1, arg2]`.
- **Identity Auth**: Use the built-in `Identity` system. Do not re-implement custom JWT logic unless necessary.
- **Table Visibility**: Keep tables private unless they explicitly need to be read by clients.
- **Determinism**: Reducers must be deterministic. Avoid using external APIs or random numbers inside a reducer (use `ctx.timestamp` for time).

## Development Workflow
- **Module Updates**: Always version your module publishes.
- **SDK Usage**: Ensure you are using the modern `spacetimedb` package, not the deprecated `@clockworklabs/spacetimedb-sdk`.
- **Error Handling**: Reducers should return meaningful errors. Catch these in the caller to provide user feedback.
