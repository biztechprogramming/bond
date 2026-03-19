## Architecture-Aware Implementation

Before writing or modifying code in any project, identify the architectural pattern in use. **Do not write code until you understand the architecture.**

### Step 1: Check for Explicit Declaration

Look for an `## Architecture` section in the project's `AGENTS.md`. If found, use it and skip detection.

### Step 2: Detect from Structure

If no explicit declaration exists, examine the directory structure:

| You See | Pattern |
|---------|---------|
| `Domain/`, `Application/`, `Infrastructure/`, `Presentation/` | Clean Architecture |
| `DAL/` or `DataAccess/`, `BLL/` or `BusinessLogic/`, `Controllers/` | Layered (N-Tier) |
| `Features/{Name}/` with commands + handlers together | Vertical Slice |
| `Commands/`, `Queries/`, `Handlers/` as separate directories | CQRS |
| `ViewModels/`, `Views/`, data binding patterns | MVVM |
| `Endpoints/`, `Program.cs` with `app.Map*`, no controllers | Minimal API |
| Multiple service directories, docker-compose with multiple services | Microservices |
| `Modules/{Name}/` each with internal layers | Modular Monolith |

If multiple patterns are present (e.g., Clean Architecture + CQRS), follow all applicable rules.

### Step 3: Follow the Rules

---

#### Clean Architecture

Layer order (inner → outer): **Domain → Application → Infrastructure → Presentation/UI**

- **Domain** contains entities, value objects, domain events, and interfaces. It has ZERO dependencies on other layers or external packages.
- **Application** contains use cases, service interfaces, DTOs, and validation. It depends only on Domain.
- **Infrastructure** implements interfaces defined in Application (repositories, external services, persistence). It depends on Application and Domain.
- **Presentation/UI** (controllers, Razor pages, Blazor components, API endpoints) depends on Application. It calls application services — never repositories or domain logic directly.
- **Dependency direction is strictly inward.** Outer layers depend on inner layers, never the reverse.
- Never put business logic in controllers, Razor/Blazor components, or API endpoints. They are thin — they receive input, call an application service, and return the result.
- Never put data access logic in the Application layer. Define interfaces there; implement them in Infrastructure.

#### Layered (N-Tier)

- **Presentation** → **Business Logic** → **Data Access**. Each layer talks only to the layer directly below it.
- UI components call business logic services. Business logic services call data access. No skipping layers.
- Business rules live in the business logic layer, not in controllers or data access.
- Data access is abstracted behind interfaces or repository classes.

#### Vertical Slice

- Each feature is a self-contained slice: request → handler → response, with its own validation, data access, and mapping.
- Slices do not share internal implementation. Cross-cutting concerns use pipeline behaviors (e.g., MediatR behaviors), not shared base classes.
- Do not create shared "service" classes that multiple slices depend on. If two slices need similar logic, each gets its own copy until a clear abstraction emerges.
- A slice may bypass traditional layers — a handler can call the database directly. But slices must not call each other.

#### CQRS

- **Commands** change state and return nothing (or an ID/status). **Queries** read state and are side-effect-free. Never mix the two.
- Command handlers contain business logic and persist changes. Query handlers are optimized for reading and may use denormalized views or raw SQL.
- Commands and queries are separate classes — do not combine them into a "service" that does both.
- When used with MediatR, follow the pipeline behavior order for cross-cutting concerns (see MediatR prompt if available).

#### MVVM

- **Views** (XAML, Razor) contain only layout and data binding. Zero business logic. Zero data access.
- **ViewModels** expose data and commands to the view. They call services for business logic and data access. They do not directly use `DbContext`, `HttpClient`, or similar infrastructure.
- **Models** represent the data. They may contain simple validation but no infrastructure concerns.
- ViewModels are testable without a UI. If you can't unit test a ViewModel without rendering a view, the separation is wrong.

#### Minimal API

- Endpoints are thin — parse the request, call a service or handler, return the response.
- Business logic lives in injectable services, not in endpoint lambdas.
- Group related endpoints using `MapGroup` or extension methods. Keep `Program.cs` as a composition root, not a dumping ground.

---

### System-Level Patterns

These apply **in addition to** the within-service patterns above.

#### Microservices

- **Each service owns its data.** Never access another service's database directly — not even for reads. Use APIs or events.
- **API contracts are sacred.** Do not add required fields to existing endpoints without versioning. Do not remove fields. Think of every API as a public contract.
- **Prefer async communication** (message queues, events) over synchronous HTTP calls between services. Synchronous chains create cascading failures.
- **No distributed transactions.** Use sagas or compensating actions for multi-service operations.
- **Each service is independently deployable.** If changing service A requires simultaneously deploying service B, you have a distributed monolith, not microservices.
- **Shared libraries are limited to cross-cutting concerns** (logging, auth, serialization). Never share domain models between services.

#### Modular Monolith

- **Modules communicate through well-defined interfaces** (public APIs of each module), not by reaching into each other's internals.
- **Each module owns its data** — separate schemas, separate DbContexts if using EF. No cross-module joins.
- **Module internals are not accessible outside the module.** Use `internal` access modifiers (C#) or package-private (Java) for implementation classes.
- The shared kernel (if any) contains only truly universal concepts — do not let it become a dumping ground.

---

### Universal Rules (Apply Always)

Regardless of architecture:

- **UI components are thin.** They handle user interaction and display. They do not contain business logic, data access, or complex state management.
- **Business logic is testable in isolation.** If you need a database, HTTP server, or UI framework to test business rules, the architecture is wrong.
- **Follow existing patterns.** If the codebase has 20 services following a pattern and you're adding the 21st, follow the same pattern even if you'd prefer a different one.
- **When in doubt, ask.** If you cannot determine the architecture or the right layer for your code, say so. Do not guess and dump everything in the UI.
