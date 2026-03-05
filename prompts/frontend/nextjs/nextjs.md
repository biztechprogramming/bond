# Next.js

## Core Principles
- **Server-First**: Leverage Server Components to reduce client-side JavaScript and improve SEO/performance.
- **Streaming & Suspense**: Use `loading.tsx` and manual `<Suspense>` boundaries to stream UI parts as they become ready.
- **Edge Compatibility**: Write middleware and route handlers to be compatible with the Edge Runtime where possible.
- **Convention over Configuration**: Follow Next.js file-system routing and special file conventions (`layout`, `page`, `error`, `not-found`).

## Patterns / Gotchas
- **App Router Conventions**:
  - `layout.tsx`: Persists state and does not re-render on navigation. Use for shared UI (nav, footer).
  - `template.tsx`: Similar to layout but creates a new instance on every navigation. Use when you need to trigger entrance animations or reset state.
  - `error.tsx`: Must be a Client Component. Use to catch unexpected runtime errors.
  - `not-found.tsx`: Use `notFound()` from `next/navigation` to trigger this UI for missing resources.
- **Server Actions**:
  - Use `'use server'` for form submissions and data mutations.
  - Handle errors gracefully and use `revalidatePath` or `revalidateTag` to update the cache after mutations.
  - Use `useOptimistic` for immediate UI feedback during server actions.
- **Data Fetching & Caching**:
  - Next.js 15: `cookies()` and `headers()` are async. Always `await` them.
  - Use `cache()` from `react` to memoize data fetching within a single request.
  - Prefer `fetch` with `next: { tags: [...] }` for fine-grained cache invalidation.
- **Routing & Metadata**:
  - Use `generateMetadata` for dynamic SEO tags.
  - Utilize Parallel Routes (`@slot`) and Intercepting Routes (`(..)`) for complex UI patterns like modals and dashboards.
  - `useRouter`, `usePathname`, and `useSearchParams` are client-only hooks.
