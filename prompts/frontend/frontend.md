# Frontend

## When this applies
Working on Bond's Next.js frontend (port 18788).

## Patterns / Gotchas
- Bond frontend is Next.js with App Router — NOT Pages Router
- Minimize `'use client'` directives — default to Server Components
- `useEffect` for data fetching is an antipattern in Next.js App Router — use server components or `use()` hook
- Hydration mismatches: server HTML and client first render MUST match — `Date.now()`, `Math.random()`, or `window` access in render causes mismatch
- Bundle size: every `'use client'` component and its imports go to the client bundle — keep client components small and leaf-level
- Image optimization: always use `next/image` not `<img>` — it handles lazy loading, responsive sizing, and WebP conversion
