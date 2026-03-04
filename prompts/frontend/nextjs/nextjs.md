# Next.js

## When this applies
Working with Bond's Next.js frontend (App Router).

## Patterns / Gotchas
- App Router is the default since Next.js 13 — Bond uses App Router exclusively, NOT Pages Router
- `layout.tsx` does NOT re-render on navigation — state in layouts persists across page transitions. Don't put page-specific state in layouts
- `page.tsx` is the only file that makes a route publicly accessible — `layout.tsx`, `loading.tsx`, `error.tsx` are conventions, not routes
- `loading.tsx` wraps the page in Suspense automatically — equivalent to `<Suspense fallback={<Loading/>}><Page/></Suspense>`
- `error.tsx` must be a Client Component (`'use client'`) — error boundaries require state
- `not-found.tsx`: triggered by `notFound()` function, not 404 status — must be explicitly called in server components
- Server Actions: `'use server'` at top of function or file — these are POST endpoints under the hood; they can be called from client components
- `revalidatePath('/')` and `revalidateTag('tag')` — only work in Server Actions or Route Handlers, not in components
- Parallel routes: `@modal/page.tsx` alongside `page.tsx` — for modals that work with browser back/forward
- Intercepting routes: `(..)photo/[id]` — intercepts navigation to show modal, direct URL access shows full page
- Metadata: `export const metadata` or `export function generateMetadata()` — only in `layout.tsx` or `page.tsx`, NOT in client components
- `cookies()` and `headers()` are async in Next.js 15 — `const cookieStore = await cookies()` — breaking change from 14
