# Backend Development

General principles and patterns for backend services.

## Core Principles
- **API-First Design**: Define contracts (OpenAPI, Protobuf) before implementation.
- **Statelessness**: Design services to be horizontally scalable. Store state in databases or caches.
- **Idempotency**: Ensure operations (especially POST/PUT) can be retried without side effects. Use idempotency keys.
- **Layered Architecture**: Keep concerns separated (Controllers/Handlers -> Services/Domain -> Repository/Data).
- **Fail Fast**: Validate inputs early. Return clear, actionable error messages with appropriate HTTP status codes.

## Security
- **Least Privilege**: Services should only have the permissions they need.
- **Input Validation**: Never trust client input. Use schema validation (Pydantic, Zod, FluentValidation).
- **Sensitive Data**: Never log PII, secrets, or credentials. Use masked logging for sensitive fields.
- **Secure Defaults**: Use HTTPS, secure cookies, and modern TLS versions.

## Observability
- **Structured Logging**: Log in JSON format with consistent fields (trace_id, user_id, action).
- **Metrics**: Track RED patterns (Requests, Errors, Duration).
- **Tracing**: Pass correlation IDs across service boundaries.
- **Health Checks**: Implement `/healthz` and `/readyz` endpoints.

## Performance
- **Connection Pooling**: Reuse database and HTTP client connections.
- **Caching**: Use Redis/Memcached for expensive computations or frequent reads. Use TTLs.
- **Pagination**: Always paginate list endpoints. Use cursor-based pagination for large datasets.
- **Async Processing**: Use background jobs (Celery, Hangfire, BullMQ) for long-running tasks.
