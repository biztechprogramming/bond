# 052 — Architecture-Aware Code Generation

**Status:** Draft  
**Created:** 2026-03-19  
**Author:** Bond Agent  

## Problem

When coding agents work on a project, they have no awareness of the project's architectural patterns. This leads to violations like:

- Business logic placed directly in UI components (Blazor `.razor` files, React components)
- Data access calls made from controllers or UI instead of through a service/repository layer
- Cross-service database access in microservice systems
- God classes that mix concerns across layers

The current prompt set mentions SOLID and "read before write" but provides no concrete architectural rules. Agents need explicit, actionable constraints tied to the architecture they're working in.

## Design

### Approach: Single Detecting Prompt

A single Tier 2 prompt (`architecture/architecture.md`) loads during the `implementing` phase. It contains:

1. **A detection mandate** — the agent must examine the project structure before writing code
2. **Detection heuristics** — directory/file patterns that map to known architectures
3. **Per-architecture rules** — concrete constraints for each pattern (~15 lines each)
4. **Microservices rules** — cross-service constraints that apply orthogonally to within-service patterns

### Why Not Separate Files Per Architecture?

The rules for each architecture are short (10–20 lines of actionable constraints). The total for all architectures fits comfortably in ~150 lines (~2K tokens). Building a detection/selection pipeline to avoid loading an extra 1.5K tokens of irrelevant rules is not worth the complexity.

If the prompt grows beyond ~300 lines in the future, it can be split into separate files with Tier 3 utterance matching. That's a straightforward refactor — but we shouldn't build it preemptively.

### Alternatives Considered

| Approach | Complexity | Config Required | Works on Unknown Repos |
|----------|-----------|-----------------|----------------------|
| **Classifier subsystem + config files** | High — new subsystem, new file format, tree-walk for subdirectory overrides | Yes — config file in every repo | No |
| **AGENTS.md tags + manifest `project-tag`** | Medium — new manifest syntax, parser changes | Yes — AGENTS.md section in every repo | No |
| **Single detecting prompt (chosen)** | Low — one markdown file, one manifest entry | No — optional AGENTS.md hint | Yes |

### Architecture Taxonomy

The prompt covers two orthogonal dimensions:

**Within-service patterns** (how code is organized inside a single deployable unit):
- Clean Architecture
- Layered / N-Tier
- Vertical Slice
- CQRS (often paired with MediatR)
- MVVM (Blazor, WPF, MAUI)
- Minimal API / Endpoint-oriented

**System-level patterns** (how services relate to each other):
- Microservices
- Modular Monolith

These compose: a microservice can internally use Clean Architecture + CQRS. The prompt handles both dimensions.

### Detection Mechanism

The agent is instructed to examine the project before writing code. Detection uses directory structure heuristics:

| Pattern | Signals |
|---------|---------|
| Clean Architecture | `Domain/`, `Application/`, `Infrastructure/`, `Presentation/` or `WebUI/` |
| Layered / N-Tier | `DAL/` or `DataAccess/`, `BLL/` or `BusinessLogic/`, `Controllers/` without domain layer |
| Vertical Slice | `Features/{Name}/` containing both commands and handlers together |
| CQRS | `Commands/`, `Queries/`, `Handlers/` as separate concerns |
| MVVM | `ViewModels/`, `Views/`, INotifyPropertyChanged usage |
| Minimal API | `Endpoints/`, `Program.cs` with `app.Map*` patterns, no controllers |
| Microservices | Multiple `*.sln` or service directories, docker-compose with multiple services, API gateway |
| Modular Monolith | `Modules/{Name}/` each with internal layers, shared kernel |

### Explicit Override

If detection is ambiguous or the team wants to be explicit, they add to their project's `AGENTS.md`:

```markdown
## Architecture
This project uses Clean Architecture with CQRS.
Individual services follow the Microservices pattern.
```

The agent checks for this section first and skips detection if found.

### Manifest Integration

One entry added to `manifest.yaml`:

```yaml
architecture/architecture.md:
  tier: 2
  phase: implementing
```

No new manifest features, no new syntax.

## File Changes

| File | Change |
|------|--------|
| `prompts/architecture/architecture.md` | **New** — the architecture-aware prompt |
| `prompts/manifest.yaml` | Add one Tier 2 entry |

## Token Budget

Estimated prompt size: ~2,200 tokens. Loads only during `implementing` phase. Comparable to existing Tier 2 prompts like `engineering.md` + `code-quality.md` combined.

## Future Evolution

- **If rules grow too long:** Split into per-architecture files with Tier 3 utterance matching. The content is already organized by section headers, making this a mechanical refactor.
- **If detection proves unreliable:** Add a lightweight pre-step that runs `find` on the project and caches the result, rather than relying on the agent's judgment.
- **Language-specific addenda:** If C#-specific or Python-specific architecture rules diverge significantly, add leaf fragments under `architecture/csharp/` or `architecture/python/` with Tier 3 utterances. The parent prompt remains universal.
