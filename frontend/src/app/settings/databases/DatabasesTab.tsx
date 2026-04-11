import React, { useState, useEffect, useCallback } from "react";
import { BACKEND_API, apiFetch } from "@/lib/config";

function generateId(): string {
  return crypto.randomUUID().replace(/-/g, "").slice(0, 16);
}

interface DatabaseConnection {
  id: string;
  name: string;
  driver: string;
  description: string | null;
  status: string;
  agent_count: number;
  created_at: string;
  updated_at: string;
}

interface DatabaseForm {
  name: string;
  driver: string;
  description: string;
  dsn: string;
  host: string;
  port: string;
  database: string;
  username: string;
  password: string;
  useRawDsn: boolean;
}

const DRIVERS = [
  { value: "postgres", label: "PostgreSQL" },
  { value: "mysql", label: "MySQL" },
  { value: "mariadb", label: "MariaDB" },
  { value: "mssql", label: "MS SQL Server" },
  { value: "oracle", label: "Oracle" },
  { value: "snowflake", label: "Snowflake" },
  { value: "sqlite", label: "SQLite" },
];

const DEFAULT_PORTS: Record<string, string> = {
  postgres: "5432",
  mysql: "3306",
  mariadb: "3306",
  mssql: "1433",
  oracle: "1521",
  snowflake: "",
  sqlite: "",
};

function buildDsn(form: DatabaseForm): string {
  if (form.useRawDsn) return form.dsn;
  const d = form.driver;
  if (d === "sqlite") return form.database;
  const userPart = form.username ? `${form.username}${form.password ? `:${form.password}` : ""}@` : "";
  const hostPart = `${form.host || "localhost"}:${form.port || DEFAULT_PORTS[d] || ""}`;
  return `${d}://${userPart}${hostPart}/${form.database}`;
}

function StatusDot({ status }: { status: string }) {
  const colors: Record<string, string> = {
    active: "#22c55e",
    error: "#ef4444",
    disconnected: "#eab308",
  };
  return (
    <span
      style={{
        width: "8px",
        height: "8px",
        borderRadius: "50%",
        backgroundColor: colors[status] || "#6b7280",
        display: "inline-block",
        boxShadow: status === "active" ? `0 0 6px ${colors[status]}` : "none",
      }}
    />
  );
}

const emptyForm = (): DatabaseForm => ({
  name: "",
  driver: "postgres",
  description: "",
  dsn: "",
  host: "",
  port: "5432",
  database: "",
  username: "",
  password: "",
  useRawDsn: false,
});

export default function DatabasesTab() {
  const [databases, setDatabases] = useState<DatabaseConnection[]>([]);
  const [editing, setEditing] = useState<DatabaseForm | null>(null);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [isNew, setIsNew] = useState(false);
  const [msg, setMsg] = useState("");
  const [testing, setTesting] = useState(false);
  const [testResult, setTestResult] = useState<{ success: boolean; error?: string } | null>(null);
  const [confirmDelete, setConfirmDelete] = useState<string | null>(null);

  const fetchDatabases = useCallback(async () => {
    try {
      const res = await apiFetch(`${BACKEND_API}/databases`);
      if (res.ok) setDatabases(await res.json());
    } catch {}
  }, []);

  useEffect(() => {
    fetchDatabases();
  }, [fetchDatabases]);

  const startCreate = () => {
    setEditing(emptyForm());
    setEditingId(null);
    setIsNew(true);
    setMsg("");
    setTestResult(null);
  };

  const startEdit = (db: DatabaseConnection) => {
    setEditing({
      name: db.name,
      driver: db.driver,
      description: db.description || "",
      dsn: "",
      host: "",
      port: DEFAULT_PORTS[db.driver] || "",
      database: "",
      username: "",
      password: "",
      useRawDsn: true,
    });
    setEditingId(db.id);
    setIsNew(false);
    setMsg("");
    setTestResult(null);
  };

  const handleSave = async () => {
    if (!editing) return;
    if (!editing.name.trim()) {
      setMsg("Name is required.");
      return;
    }

    const dsn = buildDsn(editing);
    if (!dsn) {
      setMsg("Connection details are required.");
      return;
    }

    try {
      if (isNew) {
        const res = await apiFetch(`${BACKEND_API}/databases`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            name: editing.name,
            driver: editing.driver,
            dsn,
            description: editing.description || null,
          }),
        });
        if (!res.ok) {
          const data = await res.json();
          setMsg(`Error: ${data.detail || "Failed to create"}`);
          return;
        }
      } else if (editingId) {
        const body: Record<string, string | null> = {};
        if (editing.description !== undefined) body.description = editing.description || null;
        if (dsn) body.dsn = dsn;
        const res = await apiFetch(`${BACKEND_API}/databases/${editingId}`, {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(body),
        });
        if (!res.ok) {
          const data = await res.json();
          setMsg(`Error: ${data.detail || "Failed to update"}`);
          return;
        }
      }
      setMsg("Saved.");
      setEditing(null);
      setEditingId(null);
      await fetchDatabases();
    } catch (e: any) {
      setMsg(`Error: ${e.message}`);
    }
  };

  const handleDelete = async (id: string) => {
    try {
      const res = await apiFetch(`${BACKEND_API}/databases/${id}`, { method: "DELETE" });
      if (res.ok) {
        setMsg("Deleted.");
        setConfirmDelete(null);
        await fetchDatabases();
      } else {
        setMsg("Failed to delete.");
      }
    } catch {
      setMsg("Failed to delete.");
    }
  };

  const handleTest = async (id: string) => {
    setTesting(true);
    setTestResult(null);
    try {
      const res = await apiFetch(`${BACKEND_API}/databases/${id}/test`, { method: "POST" });
      const data = await res.json();
      setTestResult(data);
    } catch (e: any) {
      setTestResult({ success: false, error: e.message });
    } finally {
      setTesting(false);
    }
  };

  return (
    <div style={{ display: "flex", flexDirection: "column", height: "100%", minHeight: 0 }}>
      <div style={styles.topBar}>
        <h2 style={{ fontSize: "1.1rem", fontWeight: 600, color: "#6c8aff", margin: 0 }}>
          {editing ? (isNew ? "Add Database" : `Editing: ${editing.name}`) : "Database Connections"}
        </h2>
        <div style={{ display: "flex", gap: "8px" }}>
          {editing ? (
            <>
              <button style={styles.button} onClick={handleSave}>Save</button>
              <button style={styles.secondaryButton} onClick={() => { setEditing(null); setMsg(""); setTestResult(null); }}>Cancel</button>
            </>
          ) : (
            <button style={styles.button} onClick={startCreate}>+ Add Database</button>
          )}
        </div>
      </div>

      {msg && <div style={styles.msg}>{msg}</div>}

      <div style={{ flex: 1, overflowY: "auto" }}>
        {!editing ? (
          <>
            <div style={styles.cardGrid}>
              {databases.map((db) => (
                <div key={db.id} style={styles.card}>
                  <div style={styles.cardHeader}>
                    <span style={styles.cardName}>{db.name}</span>
                    <StatusDot status={db.status} />
                  </div>
                  <div style={styles.cardMeta}>Driver: {db.driver}</div>
                  {db.description && <div style={styles.cardMeta}>{db.description}</div>}
                  <div style={styles.cardMeta}>Agents: {db.agent_count}</div>
                  <div style={{ display: "flex", gap: "8px", marginTop: "12px" }}>
                    <button style={styles.smallButton} onClick={() => startEdit(db)}>Edit</button>
                    <button style={styles.smallButton} onClick={() => handleTest(db.id)} disabled={testing}>
                      {testing ? "Testing..." : "Test"}
                    </button>
                    {confirmDelete === db.id ? (
                      <>
                        <span style={{ color: "#ff6c8a", fontSize: "0.8rem", alignSelf: "center" }}>Delete?</span>
                        <button style={styles.dangerSmall} onClick={() => handleDelete(db.id)}>Yes</button>
                        <button style={styles.smallButton} onClick={() => setConfirmDelete(null)}>No</button>
                      </>
                    ) : (
                      <button style={styles.dangerSmall} onClick={() => setConfirmDelete(db.id)}>Delete</button>
                    )}
                  </div>
                </div>
              ))}
            </div>
            {databases.length === 0 && (
              <div style={{ color: "#8888a0", fontSize: "0.9rem", padding: "20px 0" }}>
                No database connections configured. Click &quot;+ Add Database&quot; to get started.
              </div>
            )}

            {testResult && (
              <div style={{
                marginTop: "12px",
                padding: "12px",
                backgroundColor: testResult.success ? "#1a3a1a" : "#3a1a1a",
                borderRadius: "8px",
                color: testResult.success ? "#4ade80" : "#ff6c8a",
                fontSize: "0.85rem",
              }}>
                {testResult.success ? "Connection successful" : `Connection failed: ${testResult.error || "Unknown error"}`}
              </div>
            )}
          </>
        ) : (
          <div style={styles.form}>
            <div style={styles.field}>
              <label style={styles.label}>Name</label>
              <input
                style={styles.input}
                value={editing.name}
                onChange={(e) => setEditing({ ...editing, name: e.target.value })}
                placeholder="my_database"
                disabled={!isNew}
              />
              <div style={{ fontSize: "0.75rem", color: "#5a5a6e", marginTop: "2px" }}>
                Lowercase letters, numbers, underscores. Must start with a letter.
              </div>
            </div>

            <div style={styles.field}>
              <label style={styles.label}>Driver</label>
              <select
                style={styles.select}
                value={editing.driver}
                onChange={(e) => setEditing({
                  ...editing,
                  driver: e.target.value,
                  port: DEFAULT_PORTS[e.target.value] || "",
                })}
                disabled={!isNew}
              >
                {DRIVERS.map((d) => (
                  <option key={d.value} value={d.value}>{d.label}</option>
                ))}
              </select>
            </div>

            <div style={{ ...styles.field, gridColumn: "1 / -1" }}>
              <label style={styles.label}>Description</label>
              <input
                style={styles.input}
                value={editing.description}
                onChange={(e) => setEditing({ ...editing, description: e.target.value })}
                placeholder="Optional description"
              />
            </div>

            <div style={{ ...styles.field, gridColumn: "1 / -1" }}>
              <label style={styles.checkboxLabel}>
                <input
                  type="checkbox"
                  checked={editing.useRawDsn}
                  onChange={(e) => setEditing({ ...editing, useRawDsn: e.target.checked })}
                  style={{ accentColor: "#6c8aff" }}
                />
                Use raw DSN
              </label>
            </div>

            {editing.useRawDsn ? (
              <div style={{ ...styles.field, gridColumn: "1 / -1" }}>
                <label style={styles.label}>DSN</label>
                <input
                  type="password"
                  style={styles.input}
                  value={editing.dsn}
                  onChange={(e) => setEditing({ ...editing, dsn: e.target.value })}
                  placeholder="postgres://user:pass@host:5432/dbname"
                />
              </div>
            ) : (
              <>
                <div style={styles.field}>
                  <label style={styles.label}>Host</label>
                  <input
                    style={styles.input}
                    value={editing.host}
                    onChange={(e) => setEditing({ ...editing, host: e.target.value })}
                    placeholder="localhost"
                  />
                </div>
                <div style={styles.field}>
                  <label style={styles.label}>Port</label>
                  <input
                    style={styles.input}
                    value={editing.port}
                    onChange={(e) => setEditing({ ...editing, port: e.target.value })}
                    placeholder={DEFAULT_PORTS[editing.driver] || ""}
                  />
                </div>
                <div style={styles.field}>
                  <label style={styles.label}>Database</label>
                  <input
                    style={styles.input}
                    value={editing.database}
                    onChange={(e) => setEditing({ ...editing, database: e.target.value })}
                    placeholder="mydb"
                  />
                </div>
                <div style={styles.field}>
                  <label style={styles.label}>Username</label>
                  <input
                    style={styles.input}
                    value={editing.username}
                    onChange={(e) => setEditing({ ...editing, username: e.target.value })}
                    placeholder="postgres"
                  />
                </div>
                <div style={{ ...styles.field, gridColumn: "1 / -1" }}>
                  <label style={styles.label}>Password</label>
                  <input
                    type="password"
                    style={styles.input}
                    value={editing.password}
                    onChange={(e) => setEditing({ ...editing, password: e.target.value })}
                    placeholder="••••••••"
                  />
                </div>
              </>
            )}
          </div>
        )}
      </div>
    </div>
  );
}

const styles: Record<string, React.CSSProperties> = {
  topBar: {
    display: "flex", justifyContent: "space-between", alignItems: "center",
    padding: "12px 0", borderBottomWidth: "1px", borderBottomStyle: "solid", borderBottomColor: "#1e1e2e", marginBottom: "16px", flexShrink: 0,
  },
  msg: {
    padding: "8px 24px",
    fontSize: "0.85rem",
    color: "#6cffa0",
    backgroundColor: "#12121a",
  },
  cardGrid: {
    display: "grid",
    gridTemplateColumns: "repeat(auto-fill, minmax(280px, 1fr))",
    gap: "16px",
  },
  card: {
    backgroundColor: "#12121a",
    borderRadius: "12px",
    padding: "20px",
    borderWidth: "1px", borderStyle: "solid", borderColor: "#1e1e2e",
  },
  cardHeader: { display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "8px" },
  cardName: { fontSize: "1.1rem", fontWeight: 600, color: "#e0e0e8" },
  cardMeta: { fontSize: "0.8rem", color: "#8888a0", marginTop: "4px" },
  form: {
    backgroundColor: "#12121a",
    borderRadius: "12px",
    padding: "24px",
    borderWidth: "1px", borderStyle: "solid", borderColor: "#1e1e2e",
    display: "grid",
    gridTemplateColumns: "1fr 1fr",
    gap: "16px",
  },
  field: { flex: 1 },
  label: {
    display: "block",
    fontSize: "0.85rem",
    color: "#8888a0",
    marginBottom: "6px",
    fontWeight: 500,
  },
  input: {
    width: "100%",
    backgroundColor: "#1e1e2e",
    borderWidth: "1px", borderStyle: "solid", borderColor: "#2a2a3e",
    borderRadius: "8px",
    padding: "10px 12px",
    color: "#e0e0e8",
    fontSize: "0.95rem",
    outline: "none",
    boxSizing: "border-box" as const,
  },
  select: {
    width: "100%",
    backgroundColor: "#1e1e2e",
    borderWidth: "1px", borderStyle: "solid", borderColor: "#2a2a3e",
    borderRadius: "8px",
    padding: "10px 12px",
    color: "#e0e0e8",
    fontSize: "0.95rem",
    outline: "none",
  },
  button: {
    backgroundColor: "#6c8aff",
    color: "#fff",
    borderWidth: 0, borderStyle: "none", borderColor: "transparent",
    borderRadius: "8px",
    padding: "10px 20px",
    fontSize: "0.9rem",
    fontWeight: 600,
    cursor: "pointer",
  },
  secondaryButton: {
    backgroundColor: "#2a2a3e",
    color: "#e0e0e8",
    borderWidth: "1px", borderStyle: "solid", borderColor: "#3a3a4e",
    borderRadius: "8px",
    padding: "10px 20px",
    fontSize: "0.9rem",
    cursor: "pointer",
  },
  smallButton: {
    backgroundColor: "#2a2a3e",
    color: "#6c8aff",
    borderWidth: 0, borderStyle: "none", borderColor: "transparent",
    borderRadius: "4px",
    padding: "4px 8px",
    fontSize: "0.75rem",
    cursor: "pointer",
  },
  dangerSmall: {
    backgroundColor: "#3a1a1a",
    color: "#ff6c8a",
    borderWidth: 0, borderStyle: "none", borderColor: "transparent",
    borderRadius: "4px",
    padding: "4px 8px",
    fontSize: "0.8rem",
    cursor: "pointer",
  },
  checkboxLabel: {
    display: "flex",
    alignItems: "center",
    gap: "6px",
    color: "#e0e0e8",
    fontSize: "0.85rem",
    cursor: "pointer",
  },
};
