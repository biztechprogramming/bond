# C# & .NET

Principles for modern .NET backend development.

## Architecture & Design
- **Clean Architecture**: Separate Domain, Application, and Infrastructure layers.
- **MediatR**: Use the Mediator pattern to decouple request handling from controllers.
- **FluentValidation**: Use for complex business validation rules outside of DTOs.
- **Entity Framework Core**: Use migrations, avoid `LazyLoading`, and use `AsNoTracking()` for read-only queries.

## Coding Standards
- **C# 10/11/12+**: Use Records for DTOs, File-scoped namespaces, and Primary Constructors.
- **Nullable Reference Types**: Enable and respect `<Nullable>enable</Nullable>`.
- **LINQ**: Use for collection manipulation, but avoid overly complex single-line queries.
- **Naming**: Follow standard .NET naming conventions (PascalCase for classes/methods, camelCase for parameters).

## Performance & Safety
- **Async/Await**: Use `ValueTask` for hot paths. Always use `ConfigureAwait(false)` in library code (but not in App code).
- **Span<T> & Memory<T>**: Use for high-performance buffer management and string slicing.
- **Logging**: Use `ILogger<T>` with message templates (structured logging). Avoid string interpolation in log calls.
- **Configuration**: Use the `IOptions<T>` pattern for strongly-typed configuration.

## Testing
- **xUnit/nUnit**: Use for unit and integration tests.
- **FluentAssertions**: Use for readable and expressive test assertions.
- **Moq/NSubstitute**: Use for mocking dependencies in unit tests.
- **TestContainers**: Use for integration testing against real databases/services in Docker.
