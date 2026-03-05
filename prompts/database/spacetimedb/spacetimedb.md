# SpacetimeDB Best Practices

## Architecture Principles
- **Reducer-Centric**: All state mutations must happen within Reducers. Reducers are atomic transactions.
- **Deterministic Logic**: Reducers must be deterministic. Avoid using random numbers, external API calls, or non-deterministic time logic inside a Reducer.
- **Relational Model**: Treat SpacetimeDB as a relational database. Use tables and indices efficiently.

## Development Workflow
- **Schema Evolution**: Use the CLI to publish modules. Be aware that changing table structures requires careful management of existing data.
- **Bindings**: Always regenerate client-side bindings after updating and publishing a module to ensure type safety.
- **Testing**: Test Reducers in isolation. Since they are the only way to modify state, ensuring their correctness is paramount.

## Performance & Scaling
- **Indices**: Define indices on columns used in filters within Reducers or client-side queries.
- **Transaction Scope**: Keep Reducers small and focused. Long-running Reducers block other operations on the same module.
- **Data Locality**: Design your schema to minimize the number of table lookups required within a single Reducer call.

## Security & Identity
- **Identity-Based Access**: Use the caller's `Identity` within Reducers to implement row-level security or permission checks.
- **Private Tables**: Keep tables private unless they absolutely need to be readable by all clients. Use Reducers as the controlled interface for data access.
- **Validation**: Validate all input arguments at the start of a Reducer. Do not trust client-provided data.
