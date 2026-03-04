# React Hooks

## When this applies
Writing or debugging React hooks.

## Patterns / Gotchas
- Hook call order MUST be identical every render — no hooks inside if/else, loops, or after early returns
- `useEffect` with empty deps `[]` runs once on mount — but in Strict Mode it runs twice (mount, unmount, remount)
- `useEffect` dependency array: objects/arrays are compared by reference — `{a: 1}` !== `{a: 1}`. Memoize with `useMemo` or extract primitives
- `useState` setter with callback: `setState(prev => prev + 1)` — required when new state depends on old state; `setState(count + 1)` can be stale in closures
- `useLayoutEffect`: runs synchronously after DOM mutations, before paint — use for measuring DOM (getBoundingClientRect) or preventing visual flicker
- `useTransition`: wraps state updates as non-urgent — `startTransition(() => setState(val))` keeps UI responsive during expensive re-renders
- `useDeferredValue`: defers a value — `const deferredQuery = useDeferredValue(query)` creates a "stale" version that updates later. Different from debouncing: it's priority-based, not time-based
- Custom hooks MUST start with `use` — not just convention, React's linter uses this to enforce hook rules
- `useImperativeHandle`: exposes methods to parent via ref — `useImperativeHandle(ref, () => ({ focus: () => ... }))`. Requires `forwardRef` or React 19's ref prop
- `useSyncExternalStore`: mandatory for subscribing to external stores (Redux, Zustand internals) — prevents tearing during concurrent rendering
- `useId`: generates stable unique IDs for accessibility attributes — DO NOT use for list keys (it's the same across server/client render)
