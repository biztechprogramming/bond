## MediatR-Style Pipeline Architecture

When implementing request/response handling, use a **mediator pattern with pipeline behaviors** for cross-cutting concerns. Each behavior wraps the next in a chain, like middleware.

### Pipeline Behavior Order (outer to inner)
1. **LoggingBehavior** — logs request entry/exit, sanitizes sensitive fields
2. **PerformanceBehavior** — measures execution time, warns on slow requests
3. **AuthenticationBehavior** — validates identity (tokens, API keys, sessions)
4. **AuthorizationBehavior** — checks permissions/roles/policies for the request
5. **ValidationBehavior** — validates the request payload (FluentValidation / schema)
6. **ErrorHandlingBehavior** — catches exceptions, maps to appropriate responses
7. **Handler** — actual business logic

### Structure
```
src/
  Application/
    Common/
      Behaviors/
        LoggingBehavior.cs
        PerformanceBehavior.cs
        AuthenticationBehavior.cs
        AuthorizationBehavior.cs
        ValidationBehavior.cs
        ErrorHandlingBehavior.cs
      Interfaces/
        IRequest<TResponse>
        IRequestHandler<TRequest, TResponse>
        IPipelineBehavior<TRequest, TResponse>
    Features/
      {Feature}/
        Commands/
        Queries/
        Handlers/
```

### Implementation Guidelines
- Each behavior implements IPipelineBehavior<TRequest, TResponse> with a Handle(request, next) method
- Behaviors call await next() to pass to the next behavior in the pipeline
- Register behaviors in DI container in the correct order
- Each behavior should be independently testable — inject dependencies, mock the next delegate
- Use correlation IDs throughout — generate at the edge, propagate through all behaviors
- Sensitive data (passwords, tokens, SSNs) must NEVER appear in logs at any environment level
