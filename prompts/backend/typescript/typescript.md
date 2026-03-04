# TypeScript

## When this applies
Writing TypeScript code in Bond's gateway or frontend.

## Patterns / Gotchas
- `satisfies` operator (TS 4.9+): validates type without widening — `const config = {...} satisfies Config` keeps literal types while checking against Config
- `const` assertions: `as const` makes arrays readonly tuples and object literals readonly — required for discriminated unions from literal values
- `enum` alternatives: use `const map = { A: 'a', B: 'b' } as const; type Key = keyof typeof map` — enums emit runtime code, const objects don't
- `interface` vs `type`: interfaces support declaration merging (extending across files), types don't — use interfaces for public APIs, types for unions/intersections
- `Record<string, T>` allows ANY string key — use `Map<string, T>` if you need runtime key tracking or non-string keys
- `Partial<T>` makes ALL properties optional, including nested objects — for deep partial, use a custom utility type
- `unknown` vs `any`: `unknown` requires type narrowing before use — always prefer `unknown` for untyped external data
- Template literal types: `` type Route = `/api/${string}` `` — enforces string patterns at compile time
- `import type` (TS 3.8+): ensures import is erased at runtime — mandatory for type-only imports to avoid circular dependency issues
- Strict null checks: `x?.y?.z ?? default` — nullish coalescing (`??`) checks `null|undefined` only, NOT `""` or `0` (unlike `||`)
- `Promise<void>` vs `Promise<undefined>`: `void` means return value is ignored, `undefined` means it must be explicitly `undefined` — use `void` for fire-and-forget
