# Infrastructure & DevOps

Core principles for managing deployment, containers, CI/CD, and operational concerns.

## Core Principles
- **Infrastructure as Code (IaC)**: All configuration must be version-controlled, declarative, and reproducible. No manual "click-ops".
- **Immutable Infrastructure**: Build once, deploy the same artifact everywhere. Never patch live systems; replace them.
- **Security by Design**:
    - **Least Privilege**: Services and users get only the minimum permissions required.
    - **Secret Management**: Never commit secrets to version control. Use environment variables or dedicated secret managers.
    - **Network Isolation**: Use private networks and security groups to limit the blast radius.
- **Observability**:
    - **Structured Logging**: Use JSON format for logs to enable easy parsing and searching.
    - **Health Checks**: Implement liveness and readiness probes for every service.
    - **Telemetry**: If it's important, it must be measured (Latency, Traffic, Errors, Saturation).
- **Resilience**:
    - **Design for Failure**: Assume everything will fail. Use circuit breakers, retries with exponential backoff, and graceful degradation.
    - **Statelessness**: Keep application layers stateless to allow easy scaling and recovery.

## Deployment & CI/CD
- **Automated Testing**: No code reaches production without passing automated unit, integration, and linting checks.
- **Atomic Deploys**: Ensure deployments are "all or nothing" to avoid inconsistent states.
- **Blue/Green or Canary**: Use deployment strategies that allow for easy rollback and minimize user impact during updates.
- **Environment Parity**: Keep Development, Staging, and Production as identical as possible to catch environment-specific bugs early.

## Operational Excellence
- **Documentation**: Infrastructure is only as good as its documentation. Document the "why", not just the "how".
- **Automated Backups**: Regular, automated, and tested backups for all persistent data.
- **Drift Detection**: Regularly check that the live infrastructure matches the defined IaC.
