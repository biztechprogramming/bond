# App Router

## When this applies
Working with Next.js App Router routing, data fetching, and caching.

## Patterns / Gotchas
- `fetch()` in Server Components: Next.js extends fetch with caching — `fetch(url, { cache: 'force-cache' })` is default in Next 14, `{ cache: 'no-store' }` is default in Next 15
- `revalidate`: `fetch(url, { next: { revalidate: 60 } })` — revalidates after 60 seconds. `0` means no cache (same as no-store)
- Route segment config: `export const dynamic = 'force-dynamic'` opts entire route out of static generation — use sparingly
- `generateStaticParams()` replaces `getStaticPaths` — must return array of param objects, runs at build time
- Streaming: Server Components stream by default — `loading.tsx` or `<Suspense>` defines streaming boundaries
- Route Groups: `(group-name)/page.tsx` — parentheses in folder name group routes without affecting URL path
- Private folders: `_components/` (underscore prefix) excluded from routing — use for colocated components
- `redirect()` throws internally (uses throw) — code after `redirect()` never executes; don't put it in try/catch
- `useRouter()` from `next/navigation` (App Router) NOT `next/router` (Pages Router) — wrong import silently fails
- `usePathname()`, `useSearchParams()`: Client Component only — reading URL in Server Components uses the `params` and `searchParams` props
- Dynamic routes `[slug]` vs catch-all `[...slug]` vs optional catch-all `[[...slug]]` — optional catch-all also matches the parent route (no slug)
- Parallel data fetching: use multiple `async` components in the same layout to fetch in parallel — sequential fetches are a waterfall
