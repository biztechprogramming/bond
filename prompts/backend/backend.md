## Backend

Principles for server-side development, APIs, and data handling:

- **API contracts first** — Define the interface before implementing the logic. Consumers shouldn't care about internals.
- **Validate at the boundary** — Check all input at the API edge. Trust nothing from outside your service.
- **Consistent error handling** — Use structured error responses with codes, messages, and correlation IDs.
- **Idempotency** — Write operations should be safe to retry. Use idempotency keys for mutations.
- **Separate reads from writes** — Query endpoints should have no side effects. Mutations should return minimal data.
- **Log with context** — Include request IDs, user IDs, and operation names in every log line.
