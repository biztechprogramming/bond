## C#

Principles for C# and .NET development:

- **Use modern C# features** — Records, pattern matching, nullable reference types, top-level statements where appropriate.
- **Dependency injection everywhere** — Register services in the DI container. Avoid `new` for services with dependencies.
- **Async all the way** — Use async/await for I/O operations. Don't block on async code with .Result or .Wait().
- **Strong typing** — Use domain types over primitives. A `CustomerId` type is better than a `string`.
- **Configuration via IOptions** — Bind config sections to strongly-typed classes. Don't read config values as raw strings.
