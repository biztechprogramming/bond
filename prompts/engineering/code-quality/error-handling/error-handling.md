## Error Handling in Code

Errors are not embarrassments to hide. They are **critical diagnostic information** for users and operators. Every swallowed error is a future 3 AM mystery. Every vague error message is wasted debugging time.

### The Cardinal Rule

**Never swallow errors.** No empty catch blocks. No `catch (Exception) { }`. No `try { ... } catch { return null; }`. No `.catch(() => {})`. If you catch an exception, you must do at least one of:
1. Log it with full context
2. Transform it into a meaningful error for the caller
3. Re-throw it (wrapped with additional context if appropriate)

### User-Facing Errors

Users must always know when something went wrong. Silent failures are the worst UX.

- **Always surface errors to the user.** A loading spinner that spins forever because an API call failed silently is unacceptable. Show an error state.
- **Error messages must be actionable.** "Something went wrong" is useless. "Could not save the inspection — the server is unreachable. Check your connection and try again." tells the user what happened and what to do.
- **Include what they were trying to do.** "Failed to save defect count for Pallet #3" is better than "Save failed."
- **Don't expose stack traces or internal details to end users.** They need human-readable messages, not `NullReferenceException at Line 247`.
- **Distinguish between retryable and non-retryable errors.** If the user can fix it (bad input, network issue), tell them. If it's a system error (bug, corrupt data), tell them to contact support and give them a correlation ID.

### Logging Errors

Logs are for the people who get paged at 3 AM. Give them everything.

- **Log the full exception** — message, stack trace, inner exceptions. Never log just the message and discard the stack.
- **Log context** — what operation was being performed, for which entity/user, with what parameters (sanitize secrets).
- **Use structured logging.** Key-value pairs, not string concatenation. `logger.LogError(ex, "Failed to save defect {DefectId} for pallet {PalletId}", defectId, palletId)` — not `logger.LogError("Error: " + ex.Message)`.
- **Use correlation IDs.** Every request gets a unique ID that flows through all layers and services. When a user reports "I got error XYZ," operators can trace the entire request path.
- **Log at the right level:**
  - `Error` — something failed and a user/operation was impacted
  - `Warning` — something unexpected happened but the operation continued (retries, fallbacks)
  - `Info` — significant business events (inspection completed, user logged in)
  - `Debug` — diagnostic detail for development

### Exception Design

- **Use specific exception types.** Don't throw `Exception` or `Error`. Create domain exceptions (`InspectionNotFoundException`, `PalletLimitExceededException`) that carry context.
- **Include relevant data in exceptions.** An exception should carry enough information to diagnose the problem without reproducing it: entity IDs, operation name, expected vs. actual state.
- **Throw early, catch late.** Validate inputs at the boundary (controller, API endpoint, UI handler). Let exceptions propagate up to a handler that knows how to deal with them. Don't catch at every layer.
- **Use global exception handlers** for unhandled exceptions — ASP.NET middleware, Blazor `ErrorBoundary`, React error boundaries, `window.onerror`. These are the safety net, not the primary strategy.
- **Never use exceptions for control flow.** Exceptions are for exceptional conditions. Use return types (`Result<T>`, status codes, null checks) for expected outcomes like "item not found."

### Anti-Patterns (Never Do These)

```csharp
// ❌ Swallowed — error vanishes into the void
try { await SaveAsync(); } catch { }

// ❌ Logged but user sees nothing — silent failure
try { await SaveAsync(); } catch (Exception ex) { logger.LogError(ex, "oops"); }

// ❌ Original stack trace destroyed
catch (Exception ex) { throw new Exception("Save failed"); }

// ❌ Vague message with no context
catch (Exception ex) { throw new Exception("An error occurred"); }

// ❌ Only the message logged, stack trace discarded
catch (Exception ex) { logger.LogError("Error: " + ex.Message); }
```

### Correct Patterns

```csharp
// ✅ Logged with context, user notified, original exception preserved
try
{
    await palletService.SaveDefectAsync(palletId, defectId, count);
}
catch (Exception ex)
{
    logger.LogError(ex, "Failed to save defect {DefectId} for pallet {PalletId}", defectId, palletId);
    throw new PalletOperationException($"Could not save defect for pallet {palletId}", ex);
}

// ✅ Global handler maps to user-friendly response
app.UseExceptionHandler(handler => handler.Run(async context =>
{
    var ex = context.Features.Get<IExceptionHandlerFeature>()?.Error;
    var correlationId = context.TraceIdentifier;
    logger.LogError(ex, "Unhandled exception. CorrelationId: {CorrelationId}", correlationId);
    await context.Response.WriteAsJsonAsync(new
    {
        error = "An unexpected error occurred.",
        correlationId = correlationId,
        message = "Please contact support with this reference ID."
    });
}));

// ✅ UI error boundary — user always sees something
<ErrorBoundary>
    <ChildContent>@Body</ChildContent>
    <ErrorContent>
        <p>Something went wrong. Please refresh or contact support.</p>
    </ErrorContent>
</ErrorBoundary>
```

### The Checklist

Before finishing any code that can fail, verify:

1. Can the user tell that an error occurred? (No silent failures)
2. Does the error message help the user understand what happened? (No "Something went wrong")
3. Can an operator find the root cause from the logs alone? (Full exception, context, correlation ID)
4. Is the original exception preserved? (Inner exception or `throw;`, not `throw ex;`)
5. Are secrets/PII excluded from both user messages and logs?
