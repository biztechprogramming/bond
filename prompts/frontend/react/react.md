# React

## When this applies
Writing React components in Bond's frontend.

## Patterns / Gotchas
- React 19: `useFormState` is DEPRECATED — use `useActionState` instead (different import path too)
- `useFormStatus` in React 19 has new properties: `data`, `method`, `action` — not just `pending`
- `use()` hook (React 19): can unwrap promises and context in render — replaces many `useEffect` + `useState` patterns for data fetching
- `key` prop on component forces complete remount — use for resetting form state, not just list rendering
- `useEffect` cleanup: return function runs BEFORE the next effect, not on unmount only — common source of race conditions with async operations
- `useRef` does NOT trigger re-render on change — if you need reactive ref behavior, use `useState` or `useSyncExternalStore`
- `useMemo`/`useCallback` are hints, not guarantees — React can discard memoized values under memory pressure (React 19+)
- `Suspense` boundaries: nested Suspense shows closest ancestor's fallback — test with intentional delays to verify boundary placement
- Strict Mode in dev: components mount, unmount, remount — this is intentional to catch effect bugs. If your code breaks, your effects have side effects
- Event handlers: `onClick={handleClick}` not `onClick={handleClick()}` — the second calls the function immediately during render
- `children` prop: `React.Children.count()` handles fragments correctly, `Array.isArray(children)` does NOT
- State updates are batched in React 18+ — even in `setTimeout`, promises, and native event handlers (not just React events like in 17)
