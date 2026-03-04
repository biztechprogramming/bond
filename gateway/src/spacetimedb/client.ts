/**
 * Shared SpacetimeDB HTTP client helpers.
 * Use these instead of duplicating callReducer/sqlQuery in each router.
 */

/**
 * Call a SpacetimeDB reducer via the HTTP API.
 * Args are positional and serialized as a JSON array.
 * Use encodeOption() for Option<T> parameters.
 */
export async function callReducer(
  baseUrl: string,
  module: string,
  reducer: string,
  args: unknown[]
): Promise<void> {
  const url = `${baseUrl}/v1/database/${module}/call/${reducer}`;
  const res = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(args, (_key, value) =>
      typeof value === "bigint" ? Number(value) : value
    ),
  });
  if (!res.ok) {
    const body = await res.text();
    throw new Error(`SpacetimeDB ${reducer} failed (${res.status}): ${body}`);
  }
}

/**
 * Run a SQL query against a SpacetimeDB module.
 * Returns rows as plain objects keyed by column name.
 */
export async function sqlQuery(
  baseUrl: string,
  module: string,
  sql: string
): Promise<any[]> {
  const url = `${baseUrl}/v1/database/${module}/sql`;
  const res = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: sql,
  });
  if (!res.ok) {
    const body = await res.text();
    throw new Error(`SpacetimeDB SQL failed (${res.status}): ${body}`);
  }
  const data = await res.json();
  if (!data || !Array.isArray(data) || data.length === 0) return [];
  const resultSet = data[0];
  if (!resultSet.rows || !resultSet.schema) return [];
  const columns = resultSet.schema.elements.map((e: any) => e.name?.some || e.name);
  return resultSet.rows.map((row: any[]) => {
    const obj: any = {};
    columns.forEach((col: string, i: number) => {
      obj[col] = row[i];
    });
    return obj;
  });
}

/**
 * Encode a value as a SpacetimeDB Option<string> sum type.
 * SpacetimeDB's HTTP JSON API expects {"some": value} or {"none": []}
 * — NOT a plain string or null.
 */
export const encodeOption = (val: string | null | undefined): { some: string } | { none: [] } =>
  (val !== null && val !== undefined) ? { some: val } : { none: [] as [] };
