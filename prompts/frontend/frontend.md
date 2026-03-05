# Frontend

## Core Principles
- **User-Centric Performance**: Prioritize Core Web Vitals (LCP, FID, CLS). Use progressive enhancement and graceful degradation.
- **Accessibility (a11y)**: Build with WCAG 2.1 AA standards in mind. Use semantic HTML, ARIA labels, and ensure keyboard navigability.
- **State Management**: Prefer local state and URL state (search params) over global state. Use React Context sparingly for truly global data (auth, theme).
- **Component Architecture**: Follow the "Atomic Design" or "Feature-based" structure. Keep components small, focused, and reusable.

## When this applies
Working on Bond's Next.js frontend (port 18788).

## Patterns / Gotchas
- **App Router First**: Bond uses Next.js App Router exclusively. Default to Server Components; use `'use client'` only when necessary (interactivity, browser APIs).
- **Data Fetching**: Use Server Components for data fetching. Avoid `useEffect` for initial loads. Leverage `fetch` with Next.js caching and revalidation tags.
- **Hydration Safety**: Ensure server and client renders match. Avoid using `window`, `localStorage`, or non-deterministic values (`Math.random()`, `Date.now()`) during the initial render.
- **Bundle Optimization**: Keep the client-side bundle lean. Use dynamic imports (`next/dynamic`) for heavy components that aren't immediately visible.
- **Image Optimization**: Always use `next/image`. Provide `width` and `height` or use `fill` with `sizes` to prevent Layout Shift.
- **Type Safety**: Use TypeScript for all frontend code. Define interfaces for component props and API responses. Use `Zod` for runtime validation of external data.
