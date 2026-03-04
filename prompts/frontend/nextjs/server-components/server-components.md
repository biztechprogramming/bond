# Server Components

## When this applies
Working with React Server Components in Next.js.

## Patterns / Gotchas
- Server Components are the DEFAULT in App Router — no directive needed. Only add `'use client'` when you NEED client features
- Server Components CANNOT use: `useState`, `useEffect`, `useRef`, `onClick`, `onChange`, or any browser API
- Server Components CAN: directly `await` async operations, access databases, read files, use secrets — none of this reaches the client
- Passing Server → Client: only serializable props (no functions, classes, or Dates). Use `.toISOString()` for dates
- Client Component importing Server Component: not directly possible — pass Server Component as `children` prop instead
- `'use client'` marks the BOUNDARY, not just that file — everything imported by a `'use client'` file becomes client code
- Server-only: `import 'server-only'` — throws build error if accidentally imported in client component. Use for DB queries, secrets
- Third-party components: most npm packages need `'use client'` wrapper because they use hooks internally — create thin client wrapper components
- Composition pattern: keep client components small/leaf-level, pass server-rendered content as `children`:
  ```tsx
  <ClientDrawer>          {/* 'use client' — handles open/close state */}
    <ServerContent />     {/* Server Component — fetches data, renders heavy content */}
  </ClientDrawer>
  ```
- `async` Server Components: just make the component `async function` and `await` — no `useEffect` or state needed
- Forms: use Server Actions (`'use server'`) for form submissions — no API route needed, progressive enhancement built-in
- Serialization: `Map`, `Set`, `Date`, `RegExp` are NOT serializable across the server/client boundary
