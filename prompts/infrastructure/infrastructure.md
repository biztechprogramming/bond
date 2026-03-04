## Infrastructure

Principles for deployment, containers, CI/CD, and operational concerns:

- **Infrastructure as code** — All configuration should be version-controlled and reproducible.
- **Immutable deployments** — Build once, deploy the same artifact everywhere. Don't patch in production.
- **Least privilege** — Containers, services, and users get only the permissions they need.
- **Observability** — If you can't measure it, you can't debug it. Logs, metrics, and traces are not optional.
- **Fail safely** — Design for failure. Health checks, graceful shutdowns, and circuit breakers.
