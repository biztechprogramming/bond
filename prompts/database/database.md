# Database Design & Architecture

## Core Principles
- **Schema-First**: Always define a clear schema before writing data. Use migrations for all changes.
- **Normalization**: Start with 3NF (Third Normal Form) to minimize redundancy. Denormalize only when performance testing proves it necessary.
- **ACID Compliance**: Ensure transactions are used for all multi-step operations to maintain data integrity.
- **Data Integrity**: Use foreign keys, unique constraints, and check constraints at the database level, not just the application level.

## Design Best Practices
- **Naming Conventions**: Use `snake_case` for tables and columns. Use singular for table names (e.g., `user` instead of `users`).
- **Primary Keys**: Prefer UUIDs for distributed systems or `BIGSERIAL`/`IDENTITY` for single-node systems. Avoid natural keys unless they are immutable and unique.
- **Timestamps**: Every table should have `created_at` and `updated_at` (using `TIMESTAMPTZ` where possible).
- **Soft Deletes**: Use a `deleted_at` timestamp instead of a boolean `is_deleted` to preserve history and metadata.

## Querying & Performance
- **N+1 Avoidance**: Use Joins or Eager Loading. Never execute queries inside loops.
- **Indexing**: 
    - Index all Foreign Keys.
    - Index columns used in `WHERE`, `ORDER BY`, and `GROUP BY` clauses.
    - Monitor for unused indexes (they slow down writes).
- **Pagination**: Use keyset pagination (cursor-based) for large datasets instead of `OFFSET`/`LIMIT`.
- **Explain Plan**: Always check `EXPLAIN ANALYZE` for complex queries to identify bottlenecks.

## Security
- **SQL Injection**: Always use parameterized queries or a trusted ORM. Never concatenate strings for SQL.
- **Principle of Least Privilege**: The application user should only have permissions for necessary tables/operations (DML), not schema changes (DDL).
- **Sensitive Data**: Encrypt PII (Personally Identifiable Information) at rest. Never store passwords in plain text (use Argon2 or bcrypt).

## Migration Strategy
- **Version Control**: Keep all migrations in the repository.
- **Idempotency**: Migrations should be repeatable or check for existence before applying.
- **Zero-Downtime**: Avoid blocking operations (like adding a column with a default value to a large table) on production databases.
