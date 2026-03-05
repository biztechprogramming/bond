# React

## Core Principles
- **Functional Components**: Use functional components with Hooks. Avoid Class components.
- **Declarative UI**: Focus on describing *what* the UI should look like for a given state, not *how* to change it.
- **Immutability**: Never mutate state directly. Use setter functions from `useState` or `useReducer`.
- **Composition over Inheritance**: Use props and children to compose complex UIs from simple components.

## Patterns / Gotchas
- **React 19 Hooks**:
  - Use `useActionState` instead of the deprecated `useFormState` for form handling.
  - Leverage `useFormStatus` to access `data`, `method`, and `action` in addition to `pending`.
  - Use the `use()` hook to unwrap promises and context directly in render, reducing boilerplate.
- **Effect Management**:
  - `useEffect` is for synchronization with external systems, not for data fetching or state derivation.
  - Always provide a cleanup function to prevent memory leaks and race conditions.
  - Keep dependency arrays honest; use `useEvent` (or similar patterns) for stable event handlers.
- **Performance Tuning**:
  - Use `useMemo` and `useCallback` only when expensive calculations or reference stability are required for child optimization.
  - Key prop: Use unique, stable IDs for list items. Use `key` to force a component remount when its identity changes.
- **Ref Usage**: `useRef` is for imperative escapes (DOM access, timers). It does not trigger re-renders. Use `useState` if the UI needs to react to the change.
- **Strict Mode**: Expect and handle double-mounting in development. It's designed to surface side-effect bugs in your render logic and effects.
