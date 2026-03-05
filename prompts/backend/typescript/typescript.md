# TypeScript (Backend)

Best practices for Node.js and TypeScript backend development.

## Language Features
- **Strict Mode**: Always enable `strict: true` in `tsconfig.json`.
- **Type Safety**: Avoid `any`. Use `unknown` for external data and narrow types with guards.
- **Interfaces vs Types**: Use `interface` for public API definitions and `type` for unions, intersections, and aliases.
- **Utility Types**: Leverage `Pick`, `Omit`, `Partial`, and `Record` for clean type transformations.

## Node.js & Frameworks
- **Async/Await**: Use `async/await` instead of raw promises or callbacks. Handle rejections.
- **ES Modules**: Prefer ESM (`import/export`) over CommonJS (`require`).
- **Validation**: Use `Zod` or `io-ts` for runtime schema validation of environment variables and API inputs.
- **Error Handling**: Throw custom Error classes. Use a global error handler to format responses.

## Patterns
- **Dependency Injection**: Use a DI container (Inversify, NestJS) or simple constructor injection.
- **Environment Variables**: Validate `process.env` at startup. Never hardcode secrets.
- **Graceful Shutdown**: Listen for `SIGTERM` and `SIGINT` to close DB connections and finish pending requests.

## Performance
- **Event Loop**: Never block the event loop with heavy CPU tasks. Use Worker Threads if necessary.
- **Streams**: Use streams for processing large files or data sets to keep memory usage low.
- **Memory Management**: Be cautious with global variables and closures that can cause memory leaks.
