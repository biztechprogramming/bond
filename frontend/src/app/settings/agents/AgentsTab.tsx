import React, { useEffect, useState, useCallback, useRef } from "react";
import DirBrowser from "@/components/shared/DirBrowser";
import { BACKEND_API , apiFetch } from "@/lib/config";
import { useAvailableModels, useSpacetimeDB } from "@/hooks/useSpacetimeDB";
import { getAgents as getAgentRows, getAgentChannels, getAgentMounts, getConnection } from "@/lib/spacetimedb-client";

function generateId(): string {
  return crypto.randomUUID().replace(/-/g, '');
}

interface WorkspaceMount {
  id?: string;
  host_path: string;
  mount_name: string;
  container_path: string;
  readonly: boolean;
}

interface ChannelConfig {
  id?: string;
  channel: string;
  enabled: boolean;
  sandbox_override: string | null;
}

interface Agent {
  id: string;
  name: string;
  display_name: string;
  system_prompt: string;
  model: string;
  utility_model: string;
  sandbox_image: string | null;
  tools: string[];
  max_iterations: number;
  auto_rag: boolean;
  auto_rag_limit: number;
  is_default: boolean;
  is_active: boolean;
  workspace_mounts: WorkspaceMount[];
  channels: ChannelConfig[];
}

// Fallbacks if the API is unreachable
const DEFAULT_MODELS = [
  "anthropic/claude-sonnet-4-20250514",
  "anthropic/claude-opus-4-6",
];

const ALL_CHANNELS = ["webchat", "signal", "telegram", "discord", "whatsapp", "email", "slack"];

/** Map SpacetimeDB camelCase rows to the Agent interface used by the UI */
function mapAgentRows(agentRows: import("@/lib/spacetimedb-client").AgentRow[]): Agent[] {
  return agentRows.map((a) => {
    const channels = getAgentChannels(a.id);
    const mounts = getAgentMounts(a.id);
    let tools: string[] = [];
    try { tools = JSON.parse(a.tools || "[]"); } catch { tools = []; }
    return {
      id: a.id,
      name: a.name,
      display_name: a.displayName,
      system_prompt: a.systemPrompt,
      model: a.model,
      utility_model: a.utilityModel,
      sandbox_image: a.sandboxImage || null,
      tools,
      max_iterations: a.maxIterations,
      auto_rag: (a as any).autoRag ?? true,
      auto_rag_limit: (a as any).autoRagLimit ?? 5,
      is_default: a.isDefault,
      is_active: a.isActive,
      workspace_mounts: mounts.map((m) => ({
        id: m.id,
        host_path: m.hostPath,
        mount_name: m.mountName,
        container_path: m.containerPath,
        readonly: m.readonly,
      })),
      channels: channels.map((c) => ({
        id: c.id,
        channel: c.channel,
        enabled: c.enabled,
        sandbox_override: c.sandboxOverride || null,
      })),
    };
  });
}

interface AgentDatabase {
  id: string;
  database_id: string;
  database_name: string;
  driver: string;
  access_tier: string;
  status: string;
  assigned_at: string;
}

interface AvailableDb {
  id: string;
  name: string;
  driver: string;
}

const DRIVER_BADGE: Record<string, { label: string; bg: string; fg: string }> = {
  postgresql: { label: "PG", bg: "#1a2a4a", fg: "#6c9fff" },
  postgres: { label: "PG", bg: "#1a2a4a", fg: "#6c9fff" },
  mysql: { label: "MY", bg: "#2a2a1a", fg: "#ffaa44" },
  mariadb: { label: "MA", bg: "#2a2a1a", fg: "#ffaa44" },
  sqlite: { label: "SQ", bg: "#1a2a2a", fg: "#44ccaa" },
  libsql: { label: "LS", bg: "#1a2a2a", fg: "#44ccaa" },
  mssql: { label: "MS", bg: "#2a1a2a", fg: "#cc88ff" },
  cockroachdb: { label: "CR", bg: "#1a2a2a", fg: "#44ccaa" },
};

function driverBadge(driver: string) {
  const key = driver.toLowerCase().replace(/\s+/g, "");
  return DRIVER_BADGE[key] ?? { label: driver.slice(0, 2).toUpperCase(), bg: "#2a2a3e", fg: "#8888a0" };
}

function DriverIcon({ driver, size = 38 }: { driver: string; size?: number }) {
  const b = driverBadge(driver);
  return (
    <div style={{
      width: size, height: size, borderRadius: 8, display: "flex", alignItems: "center", justifyContent: "center",
      fontSize: size * 0.18, fontWeight: 700, letterSpacing: "0.03em", flexShrink: 0,
      backgroundColor: b.bg, color: b.fg,
    }}>
      {b.label}
    </div>
  );
}

function StatusDot({ status }: { status: string }) {
  const color = status === "active" ? "#6cffa0" : status === "pending" ? "#ffaa44" : "#ff6c8a";
  return (
    <span style={{
      width: 7, height: 7, borderRadius: "50%", flexShrink: 0, display: "inline-block",
      backgroundColor: color, boxShadow: `0 0 6px ${color}`,
    }} />
  );
}

function TierPill({ tier, onClick }: { tier: string; onClick?: () => void }) {
  const isRo = tier === "read_only";
  return (
    <span
      onClick={onClick}
      style={{
        fontSize: "0.72rem", fontWeight: 600, padding: "3px 10px", borderRadius: 10, whiteSpace: "nowrap",
        cursor: onClick ? "pointer" : "default", transition: "all 0.15s",
        background: isRo ? "rgba(108,138,255,0.12)" : "rgba(255,170,68,0.12)",
        color: isRo ? "#6c8aff" : "#ffaa44",
        border: `1px solid ${isRo ? "rgba(108,138,255,0.25)" : "rgba(255,170,68,0.25)"}`,
      }}
    >
      {isRo ? "Read Only" : "Full Control"}
    </span>
  );
}

function AgentDatabasesSection({ agentId }: { agentId: string }) {
  const [dbs, setDbs] = useState<AgentDatabase[]>([]);
  const [availableDbs, setAvailableDbs] = useState<AvailableDb[]>([]);
  const [showAddPopover, setShowAddPopover] = useState(false);
  const [selectedDbId, setSelectedDbId] = useState("");
  const [accessTier, setAccessTier] = useState("read_only");
  const [search, setSearch] = useState("");
  const [sectionMsg, setSectionMsg] = useState("");
  const [hoveredCard, setHoveredCard] = useState<string | null>(null);
  const [tierDropdown, setTierDropdown] = useState<string | null>(null);

  const fetchDbs = useCallback(async () => {
    try {
      const res = await apiFetch(`${BACKEND_API}/agents/${agentId}/databases`);
      if (res.ok) setDbs(await res.json());
    } catch {}
  }, [agentId]);

  const fetchAvailable = useCallback(async () => {
    try {
      const res = await apiFetch(`${BACKEND_API}/databases`);
      if (res.ok) setAvailableDbs(await res.json());
    } catch {}
  }, []);

  useEffect(() => { fetchDbs(); fetchAvailable(); }, [fetchDbs, fetchAvailable]);

  const assignedIds = new Set(dbs.map((d) => d.database_id));

  const handleAdd = async () => {
    if (!selectedDbId) return;
    try {
      const res = await apiFetch(`${BACKEND_API}/agents/${agentId}/databases`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ database_id: selectedDbId, access_tier: accessTier }),
      });
      if (res.ok) {
        setSectionMsg("Database assigned.");
        setShowAddPopover(false);
        setSelectedDbId("");
        setAccessTier("read_only");
        setSearch("");
        await fetchDbs();
      } else {
        const data = await res.json();
        setSectionMsg(`Error: ${data.detail || "Failed"}`);
      }
    } catch (e: any) {
      setSectionMsg(`Error: ${e.message}`);
    }
  };

  const handleRemove = async (dbId: string) => {
    try {
      const res = await apiFetch(`${BACKEND_API}/agents/${agentId}/databases/${dbId}`, { method: "DELETE" });
      if (res.ok) {
        setSectionMsg("Database removed.");
        await fetchDbs();
      }
    } catch { setSectionMsg("Failed to remove."); }
  };

  const handleTierChange = async (dbId: string, newTier: string) => {
    setTierDropdown(null);
    try {
      const res = await apiFetch(`${BACKEND_API}/agents/${agentId}/databases/${dbId}`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ access_tier: newTier }),
      });
      if (res.ok) await fetchDbs();
      else setSectionMsg("Failed to update tier.");
    } catch { setSectionMsg("Failed to update tier."); }
  };

  const filteredAvailable = availableDbs.filter((db) =>
    db.name.toLowerCase().includes(search.toLowerCase()) ||
    db.driver.toLowerCase().includes(search.toLowerCase())
  );

  const openAdd = () => {
    setShowAddPopover(true);
    setSelectedDbId("");
    setAccessTier("read_only");
    setSearch("");
    fetchAvailable();
  };

  // Clear messages after a delay
  useEffect(() => {
    if (!sectionMsg) return;
    const t = setTimeout(() => setSectionMsg(""), 3000);
    return () => clearTimeout(t);
  }, [sectionMsg]);

  return (
    <div style={{ gridColumn: "1 / -1" }}>
      {/* Section Header */}
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 16 }}>
        <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
          <span style={{ fontSize: "0.95rem", fontWeight: 600, color: "#8888a0", letterSpacing: "0.02em" }}>Databases</span>
          <span style={{ fontSize: "0.8rem", color: "#5a5a6e", background: "#1e1e2e", padding: "2px 8px", borderRadius: 10 }}>{dbs.length}</span>
        </div>
        {dbs.length > 0 && (
          <button onClick={openAdd} style={{
            display: "flex", alignItems: "center", gap: 6,
            background: "rgba(108,138,255,0.12)", color: "#6c8aff",
            border: "1px solid transparent", borderRadius: 6, padding: "6px 14px",
            fontSize: "0.82rem", fontWeight: 600, cursor: "pointer", transition: "all 0.15s",
          }}>
            <span style={{ fontSize: "1rem", lineHeight: 1 }}>+</span> Add Database
          </button>
        )}
      </div>

      {/* Toast message */}
      {sectionMsg && (
        <div style={{ fontSize: "0.8rem", color: sectionMsg.startsWith("Error") ? "#ff6c8a" : "#6cffa0", marginBottom: 12 }}>
          {sectionMsg}
        </div>
      )}

      {/* Empty State */}
      {dbs.length === 0 && (
        <div style={{
          textAlign: "center", padding: "40px 20px",
          border: "1px dashed #2a2a3e", borderRadius: 10, background: "#12121a",
        }}>
          <div style={{ fontSize: "2rem", marginBottom: 12, opacity: 0.4 }}>&#x1f5c4;&#xfe0f;</div>
          <p style={{ color: "#5a5a6e", fontSize: "0.85rem", marginBottom: 16, lineHeight: 1.5 }}>
            No databases assigned to this agent yet.<br />
            Give this agent access to query and manage your databases.
          </p>
          <button onClick={openAdd} style={{
            display: "inline-flex", alignItems: "center", gap: 6,
            background: "#6c8aff", color: "#fff", border: "none", borderRadius: 6,
            padding: "9px 20px", fontSize: "0.85rem", fontWeight: 600, cursor: "pointer",
          }}>
            <span>+</span> Add Database
          </button>
        </div>
      )}

      {/* Assigned Database Cards */}
      <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
        {dbs.map((db) => (
          <div
            key={db.id}
            onMouseEnter={() => setHoveredCard(db.id)}
            onMouseLeave={() => { setHoveredCard(null); setTierDropdown(null); }}
            style={{
              display: "flex", alignItems: "center", gap: 14,
              background: "#12121a", border: `1px solid ${hoveredCard === db.id ? "#3a3a4e" : "#2a2a3e"}`,
              borderRadius: 10, padding: "14px 16px", transition: "border-color 0.15s",
            }}
          >
            <DriverIcon driver={db.driver} />
            <div style={{ flex: 1, minWidth: 0 }}>
              <div style={{ fontSize: "0.9rem", fontWeight: 600, color: "#e0e0e8", display: "flex", alignItems: "center", gap: 8, marginBottom: 3 }}>
                <StatusDot status={db.status} />
                {db.database_name}
              </div>
              <div style={{ fontSize: "0.78rem", color: "#5a5a6e" }}>{db.driver}</div>
            </div>
            {/* Tier pill with inline toggle */}
            <div style={{ position: "relative" }}>
              <TierPill tier={db.access_tier} onClick={() => setTierDropdown(tierDropdown === db.id ? null : db.id)} />
              {tierDropdown === db.id && (
                <div style={{
                  position: "absolute", top: "calc(100% + 6px)", right: 0, zIndex: 10,
                  background: "#1e1e2e", border: "1px solid #2a2a3e", borderRadius: 6,
                  padding: 4, minWidth: 140, boxShadow: "0 8px 24px rgba(0,0,0,0.4)",
                }}>
                  {(["read_only", "full_control"] as const).map((t) => (
                    <button key={t} onClick={() => handleTierChange(db.database_id, t)} style={{
                      display: "block", width: "100%", padding: "7px 12px", background: db.access_tier === t ? "#2a2a3e" : "none",
                      border: "none", color: "#e0e0e8", fontSize: "0.8rem", textAlign: "left" as const,
                      cursor: "pointer", borderRadius: 4,
                    }}>
                      {t === "read_only" ? "Read Only" : "Full Control"}{db.access_tier === t ? " \u2713" : ""}
                      <span style={{ display: "block", fontSize: "0.7rem", color: "#5a5a6e", marginTop: 1 }}>
                        {t === "read_only" ? "Query & describe only" : "CRUD + raw SQL"}
                      </span>
                    </button>
                  ))}
                </div>
              )}
            </div>
            {/* Remove button */}
            <button
              onClick={() => handleRemove(db.database_id)}
              title="Remove"
              style={{
                background: "none", border: "none", color: "#5a5a6e", cursor: "pointer",
                padding: 6, borderRadius: 6, fontSize: "1rem", lineHeight: 1, flexShrink: 0,
                opacity: hoveredCard === db.id ? 1 : 0, transition: "all 0.15s",
              }}
            >
              &#x2715;
            </button>
          </div>
        ))}
      </div>

      {/* Add Database Popover/Modal */}
      {showAddPopover && (
        <div
          onClick={(e) => { if (e.target === e.currentTarget) setShowAddPopover(false); }}
          style={{
            position: "fixed", inset: 0, background: "rgba(0,0,0,0.5)",
            display: "flex", alignItems: "center", justifyContent: "center", zIndex: 100,
          }}
        >
          <div style={{
            background: "#12121a", border: "1px solid #2a2a3e", borderRadius: 14,
            width: 480, maxHeight: "80vh", overflow: "hidden",
            boxShadow: "0 20px 60px rgba(0,0,0,0.5)", display: "flex", flexDirection: "column",
          }}>
            {/* Header */}
            <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", padding: "18px 20px 14px", borderBottom: "1px solid #2a2a3e" }}>
              <span style={{ fontSize: "1rem", fontWeight: 600, color: "#e0e0e8" }}>Add Database</span>
              <button onClick={() => setShowAddPopover(false)} style={{
                background: "none", border: "none", color: "#5a5a6e", fontSize: "1.2rem", cursor: "pointer", padding: 4, borderRadius: 4,
              }}>&#x2715;</button>
            </div>

            {/* Search */}
            <div style={{ padding: "12px 20px", borderBottom: "1px solid #2a2a3e" }}>
              <input
                type="text"
                placeholder="Search databases\u2026"
                value={search}
                onChange={(e) => setSearch(e.target.value)}
                autoFocus
                style={{
                  width: "100%", background: "#1e1e2e", border: "1px solid #2a2a3e", borderRadius: 8,
                  padding: "10px 14px", color: "#e0e0e8", fontSize: "0.88rem", outline: "none",
                }}
              />
            </div>

            {/* Database List */}
            <div style={{ flex: 1, overflowY: "auto", padding: 8 }}>
              {filteredAvailable.length === 0 && (
                <div style={{ padding: "20px 14px", textAlign: "center", color: "#5a5a6e", fontSize: "0.85rem" }}>
                  No databases found.
                </div>
              )}
              {filteredAvailable.map((db) => {
                const isAssigned = assignedIds.has(db.id);
                const isSelected = selectedDbId === db.id;
                return (
                  <div
                    key={db.id}
                    onClick={() => { if (!isAssigned) setSelectedDbId(isSelected ? "" : db.id); }}
                    style={{
                      display: "flex", alignItems: "center", gap: 12, padding: "12px 14px", borderRadius: 8,
                      cursor: isAssigned ? "default" : "pointer", transition: "background 0.1s",
                      opacity: isAssigned ? 0.4 : 1,
                      background: isSelected ? "rgba(108,138,255,0.12)" : "transparent",
                      border: `1px solid ${isSelected ? "rgba(108,138,255,0.3)" : "transparent"}`,
                    }}
                  >
                    <DriverIcon driver={db.driver} size={34} />
                    <div style={{ flex: 1, minWidth: 0 }}>
                      <div style={{ fontSize: "0.88rem", fontWeight: 600, color: "#e0e0e8" }}>{db.name}</div>
                      <div style={{ fontSize: "0.76rem", color: "#5a5a6e", marginTop: 2 }}>
                        {db.driver}{isAssigned ? " \u00b7 Already assigned" : ""}
                      </div>
                    </div>
                    {/* Radio-style check */}
                    <div style={{
                      width: 20, height: 20, borderRadius: "50%", flexShrink: 0, display: "flex",
                      alignItems: "center", justifyContent: "center", transition: "all 0.15s",
                      border: `2px solid ${isSelected ? "#6c8aff" : isAssigned ? "#5a5a6e" : "#2a2a3e"}`,
                      background: isSelected ? "#6c8aff" : "transparent",
                    }}>
                      {isSelected && <span style={{ color: "#fff", fontSize: "0.7rem", fontWeight: 700 }}>{"\u2713"}</span>}
                      {isAssigned && !isSelected && <span style={{ fontSize: "0.65rem", color: "#5a5a6e" }}>&mdash;</span>}
                    </div>
                  </div>
                );
              })}
            </div>

            {/* Access Tier Selector */}
            <div style={{ padding: "14px 20px", borderTop: "1px solid #2a2a3e" }}>
              <label style={{ display: "block", fontSize: "0.78rem", color: "#8888a0", marginBottom: 8, fontWeight: 500 }}>Access Level</label>
              <div style={{ display: "flex", gap: 8 }}>
                {([
                  { value: "read_only", icon: "\ud83d\udd12", label: "Read Only", desc: "Query & describe tables", activeBg: "rgba(108,138,255,0.12)", activeBorder: "rgba(108,138,255,0.4)", activeColor: "#6c8aff" },
                  { value: "full_control", icon: "\u26a1", label: "Full Control", desc: "CRUD + raw SQL", activeBg: "rgba(255,170,68,0.12)", activeBorder: "rgba(255,170,68,0.4)", activeColor: "#ffaa44" },
                ] as const).map((opt) => {
                  const active = accessTier === opt.value;
                  return (
                    <div
                      key={opt.value}
                      onClick={() => setAccessTier(opt.value)}
                      style={{
                        flex: 1, padding: "10px 14px", borderRadius: 8, cursor: "pointer", textAlign: "center",
                        transition: "all 0.15s", fontSize: "0.82rem", fontWeight: 600,
                        background: active ? opt.activeBg : "#1e1e2e",
                        border: `1px solid ${active ? opt.activeBorder : "#2a2a3e"}`,
                        color: active ? opt.activeColor : "#8888a0",
                      }}
                    >
                      <span style={{ display: "block" }}>{opt.icon} {opt.label}</span>
                      <span style={{ display: "block", fontSize: "0.7rem", fontWeight: 400, opacity: 0.7, marginTop: 2 }}>{opt.desc}</span>
                    </div>
                  );
                })}
              </div>
            </div>

            {/* Footer */}
            <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", padding: "14px 20px", borderTop: "1px solid #2a2a3e" }}>
              <span
                onClick={() => { setShowAddPopover(false); /* User should navigate to Databases tab */ }}
                style={{ color: "#6c8aff", fontSize: "0.82rem", cursor: "pointer", transition: "opacity 0.15s" }}
              >
                + Create new database
              </span>
              <button
                onClick={handleAdd}
                disabled={!selectedDbId}
                style={{
                  background: "#6c8aff", color: "#fff", border: "none", borderRadius: 8,
                  padding: "9px 24px", fontSize: "0.85rem", fontWeight: 600, cursor: selectedDbId ? "pointer" : "not-allowed",
                  opacity: selectedDbId ? 1 : 0.4, transition: "background 0.15s",
                }}
              >
                Assign Database
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

export default function AgentsTab() {
  // Live subscriptions from SpacetimeDB — auto-updates on any table change
  const agents = useSpacetimeDB(() => mapAgentRows(getAgentRows()));
  const liveModels = useAvailableModels();
  const availableModels = liveModels.length > 0 ? liveModels : DEFAULT_MODELS.map(id => ({ id, name: id }));
  const [sandboxImages, setSandboxImages] = useState<string[]>([]);
  const [editing, setEditing] = useState<Agent | null>(null);
  const [isNew, setIsNew] = useState(false);
  const [msg, setMsg] = useState("");
  const [browsingMountIndex, setBrowsingMountIndex] = useState<number | null>(null);
  const [showContainerWarning, setShowContainerWarning] = useState(false);
  // Store the original agent snapshot when editing begins, to detect container-affecting changes
  const originalAgentRef = useRef<Agent | null>(null);

  // Sandbox images still fetched via REST (no STDB table for these)
  useEffect(() => {
    apiFetch(`${BACKEND_API}/agents/sandbox-images`).then(r => r.ok ? r.json() : []).then(setSandboxImages).catch(() => {});
  }, []);

  const newAgent = (): Agent => ({
    id: "",
    name: "",
    display_name: "",
    system_prompt: "",
    model: availableModels.length > 0 ? availableModels[0].id : DEFAULT_MODELS[0],
    utility_model: availableModels.length > 0 ? availableModels[0].id : DEFAULT_MODELS[0],
    sandbox_image: null,
    tools: [],
    max_iterations: 25,
    auto_rag: true,
    auto_rag_limit: 5,
    is_default: false,
    is_active: true,
    workspace_mounts: [],
    channels: [{ channel: "webchat", enabled: true, sandbox_override: null }],
  });

  const startCreate = () => {
    setEditing({ ...newAgent() });
    setIsNew(true);
    setMsg("");
  };

  // loadAgentFragments and toggleAgentFragment removed (Doc 027 Phase 1)

  const startEdit = (agent: Agent) => {
    setEditing({ ...agent });
    originalAgentRef.current = { ...agent };
    setIsNew(false);
    setMsg("");
  };

  /** Check if any fields that trigger container recreation have changed */
  const hasContainerAffectingChanges = (): boolean => {
    if (!editing || !originalAgentRef.current) return false;
    const orig = originalAgentRef.current;
    if (editing.model !== orig.model) return true;
    if (editing.utility_model !== orig.utility_model) return true;
    if (editing.sandbox_image !== orig.sandbox_image) return true;
    // Compare mounts
    const origMounts = JSON.stringify(
      (orig.workspace_mounts || []).map(m => ({ hp: m.host_path, mn: m.mount_name, cp: m.container_path, ro: m.readonly })).sort((a, b) => a.hp.localeCompare(b.hp))
    );
    const editMounts = JSON.stringify(
      (editing.workspace_mounts || []).map(m => ({ hp: m.host_path, mn: m.mount_name, cp: m.container_path, ro: m.readonly })).sort((a, b) => a.hp.localeCompare(b.hp))
    );
    if (origMounts !== editMounts) return true;
    return false;
  };

  /** Pre-save check: if container is running and settings affect it, show warning */
  const handleSave = async () => {
    if (!editing) return;
    // New agents never have running containers
    if (isNew) { await doSave(); return; }
    // Only check if container-affecting fields changed
    if (!hasContainerAffectingChanges()) { await doSave(); return; }
    // Check if container is running
    try {
      const res = await apiFetch(`${BACKEND_API}/agents/${editing.id}/container-status`);
      if (res.ok) {
        const data = await res.json();
        if (data.running) {
          setShowContainerWarning(true);
          return; // Don't save yet — wait for user confirmation
        }
      }
    } catch { /* Can't reach backend — save anyway */ }
    await doSave();
  };

  const doSave = async () => {
    if (!editing) return;
    setMsg("");
    setShowContainerWarning(false);
    try {
      const conn = getConnection();
      if (!conn) { setMsg("Not connected to database"); return; }

      const agentId = isNew ? generateId() : editing.id;
      const toolsJson = JSON.stringify(editing.tools || []);

      if (isNew) {
        conn.reducers.addAgent({
          id: agentId,
          name: editing.name,
          displayName: editing.display_name,
          systemPrompt: editing.system_prompt,
          model: editing.model,
          utilityModel: editing.utility_model,
          tools: toolsJson,
          sandboxImage: editing.sandbox_image || "",
          maxIterations: editing.max_iterations,
          isActive: true,
          isDefault: false,
        });
      } else {
        conn.reducers.updateAgent({
          id: agentId,
          name: editing.name,
          displayName: editing.display_name,
          systemPrompt: editing.system_prompt,
          model: editing.model,
          utilityModel: editing.utility_model,
          tools: toolsJson,
          sandboxImage: editing.sandbox_image || "",
          maxIterations: editing.max_iterations,
          isActive: editing.is_active,
          isDefault: editing.is_default,
        });
      }

      // Replace mounts: delete all, then add new ones
      conn.reducers.deleteAgentMountsForAgent({ agentId });
      for (const mount of editing.workspace_mounts || []) {
        conn.reducers.addAgentMount({
          id: generateId(),
          agentId,
          hostPath: mount.host_path,
          mountName: mount.mount_name,
          containerPath: mount.container_path || `/workspace/${mount.mount_name}`,
          readonly: mount.readonly,
        });
      }

      // Replace channels: delete all, then add new ones
      conn.reducers.deleteAgentChannelsForAgent({ agentId });
      for (const ch of editing.channels || []) {
        conn.reducers.addAgentChannel({
          id: generateId(),
          agentId,
          channel: ch.channel,
          sandboxOverride: ch.sandbox_override || "",
          enabled: ch.enabled,
        });
      }

      setMsg("Saved successfully.");
      setEditing(null);
    } catch (err: any) {
      setMsg(`Error: ${err.message || "Save failed"}`);
    }
  };

  const deleteAgent = async (id: string) => {
    try {
      const conn = getConnection();
      if (!conn) { setMsg("Not connected to database"); return; }
      conn.reducers.deleteAgent({ id });
      setMsg("Deleted.");
      setEditing(null);
    } catch {
      setMsg("Failed to delete.");
    }
  };

  const setDefault = async (id: string) => {
    try {
      const conn = getConnection();
      if (!conn) { setMsg("Not connected to database"); return; }
      conn.reducers.setDefaultAgent({ id });
      setMsg("Default updated.");
    } catch {
      setMsg("Failed to set default.");
    }
  };

  const toggleChannel = (channel: string) => {
    if (!editing) return;
    const existing = editing.channels.find((c) => c.channel === channel);
    if (existing) {
      setEditing({
        ...editing,
        channels: editing.channels.filter((c) => c.channel !== channel),
      });
    } else {
      setEditing({
        ...editing,
        channels: [...editing.channels, { channel, enabled: true, sandbox_override: null }],
      });
    }
  };

  const addMount = () => {
    if (!editing) return;
    setEditing({
      ...editing,
      workspace_mounts: [
        ...editing.workspace_mounts,
        { host_path: "", mount_name: "", container_path: "", readonly: false },
      ],
    });
  };

  const removeMount = (index: number) => {
    if (!editing) return;
    setEditing({
      ...editing,
      workspace_mounts: editing.workspace_mounts.filter((_, i) => i !== index),
    });
  };

  const updateMount = (index: number, field: keyof WorkspaceMount, value: string | boolean) => {
    if (!editing) return;
    const mounts = [...editing.workspace_mounts];
    const updated = { ...mounts[index], [field]: value };
    // Auto-update container_path when mount_name changes (if container_path wasn't manually set)
    if (field === "mount_name" && typeof value === "string") {
      const oldDefault = `/workspace/${mounts[index].mount_name}`;
      if (!mounts[index].container_path || mounts[index].container_path === oldDefault) {
        updated.container_path = `/workspace/${value}`;
      }
    }
    mounts[index] = updated;
    setEditing({ ...editing, workspace_mounts: mounts });
  };

  return (
    <div style={{ display: "flex", flexDirection: "column", height: "100%", minHeight: 0 }}>
      {/* Top bar: contextual — list vs edit mode */}
      <div style={styles.topBar}>
        <h2 style={{ fontSize: "1.1rem", fontWeight: 600, color: "#6c8aff", margin: 0 }}>
          {editing ? (isNew ? "New Agent" : `Editing: ${editing.display_name || editing.name}`) : "Agent Management"}
        </h2>
        <div style={{ display: "flex", gap: "8px" }}>
          {editing ? (
            <>
              <button style={styles.button} onClick={handleSave}>Save</button>
              <button style={styles.secondaryButton} onClick={() => { setEditing(null); setMsg(""); setShowContainerWarning(false); }}>Cancel</button>
            </>
          ) : (
            <button style={styles.button} onClick={startCreate}>+ New Agent</button>
          )}
        </div>
      </div>

      {msg && <div style={styles.msg}>{msg}</div>}

      <div style={{ flex: 1, overflowY: "auto" }}>
        {!editing ? (
          <div style={styles.cardGrid}>
            {agents.map((agent) => (
              <div key={agent.id} style={styles.card} onClick={() => startEdit(agent)}>
                <div style={styles.cardHeader}>
                  <span style={styles.cardName}>{agent.display_name}</span>
                  {agent.is_default && <span style={styles.badge}>Default</span>}
                </div>
                <div style={styles.cardMeta}>{agent.model}</div>
                <div style={styles.cardMeta}>
                  Channels: {agent.channels?.map((c) => c.channel).join(", ") || "none"}
                </div>
              </div>
            ))}
          </div>
        ) : (
          <div style={styles.form}>
            <div style={styles.field}>
              <label style={styles.label}>Name (slug)</label>
              <input
                style={styles.input}
                value={editing.name}
                onChange={(e) => setEditing({ ...editing, name: e.target.value })}
                placeholder="my-agent"
              />
            </div>
            <div style={styles.field}>
              <label style={styles.label}>Display Name</label>
              <input
                style={styles.input}
                value={editing.display_name}
                onChange={(e) => setEditing({ ...editing, display_name: e.target.value })}
                placeholder="My Agent"
              />
            </div>

            <div style={styles.field}>
              <label style={styles.label}>Model</label>
              <select
                style={styles.select}
                value={editing.model}
                onChange={(e) => setEditing({ ...editing, model: e.target.value })}
              >
                {(availableModels.length > 0 ? availableModels.filter((m, i, arr) => arr.findIndex(x => x.id === m.id) === i) : DEFAULT_MODELS.map(id => ({ id, name: id }))).map((m) => (
                  <option key={m.id} value={m.id}>{m.name}</option>
                ))}
                {editing.model && !availableModels.find(m => m.id === editing.model) && (
                  <option key={editing.model} value={editing.model}>{editing.model}</option>
                )}
              </select>
            </div>
            <div style={styles.field}>
              <label style={styles.label}>Utility Model</label>
              <select
                style={styles.select}
                value={editing.utility_model}
                onChange={(e) => setEditing({ ...editing, utility_model: e.target.value })}
              >
                {(availableModels.length > 0 ? availableModels.filter((m, i, arr) => arr.findIndex(x => x.id === m.id) === i) : DEFAULT_MODELS.map(id => ({ id, name: id }))).map((m) => (
                  <option key={m.id} value={m.id}>{m.name}</option>
                ))}
                {editing.utility_model && !availableModels.find(m => m.id === editing.utility_model) && (
                  <option key={editing.utility_model} value={editing.utility_model}>{editing.utility_model}</option>
                )}
              </select>
              <div style={{ fontSize: "0.75rem", color: "#5a5a6e", marginTop: "2px" }}>
                Selects which prompt fragments to include each turn
              </div>
            </div>
            <div style={styles.field}>
              <label style={styles.label}>Sandbox Image</label>
              <select
                style={styles.select}
                value={editing.sandbox_image || ""}
                onChange={(e) => setEditing({ ...editing, sandbox_image: e.target.value || null })}
              >
                <option value="">None (host execution)</option>
                {sandboxImages.map((img) => (
                  <option key={img} value={img}>{img}</option>
                ))}
              </select>
            </div>

            <div style={styles.field}>
              <label style={styles.label}>Max Iterations</label>
              <input
                type="number"
                style={styles.input}
                value={editing.max_iterations}
                onChange={(e) => setEditing({ ...editing, max_iterations: parseInt(e.target.value) || 25 })}
              />
            </div>
            <div style={styles.field}>
              <label style={styles.label}>Auto-RAG</label>
              <div style={{ display: "flex", alignItems: "center", gap: "12px" }}>
                <label style={styles.checkboxLabel}>
                  <input
                    type="checkbox"
                    checked={editing.auto_rag}
                    onChange={(e) => setEditing({ ...editing, auto_rag: e.target.checked })}
                    style={styles.checkbox}
                  />
                  Enabled
                </label>
                {editing.auto_rag && (
                  <div style={{ display: "flex", alignItems: "center", gap: "6px" }}>
                    <span style={{ color: "#8888a0", fontSize: "0.85rem" }}>Limit:</span>
                    <input
                      type="number"
                      style={{ ...styles.input, width: "70px" }}
                      value={editing.auto_rag_limit}
                      onChange={(e) => setEditing({ ...editing, auto_rag_limit: parseInt(e.target.value) || 5 })}
                    />
                  </div>
                )}
              </div>
            </div>

            <div style={{ ...styles.field, ...styles.formFull }}>
              <label style={styles.label}>System Prompt</label>
              <textarea
                style={{ ...styles.input, minHeight: "100px", resize: "vertical" }}
                value={editing.system_prompt}
                onChange={(e) => setEditing({ ...editing, system_prompt: e.target.value })}
              />
            </div>

            <div style={styles.field}>
              <label style={styles.label}>Channels</label>
              <div style={styles.checkboxGrid}>
                {ALL_CHANNELS.map((ch) => (
                  <label key={ch} style={styles.checkboxLabel}>
                    <input
                      type="checkbox"
                      checked={editing.channels.some((c) => c.channel === ch)}
                      onChange={() => toggleChannel(ch)}
                      style={styles.checkbox}
                    />
                    {ch}
                  </label>
                ))}
              </div>
            </div>

            <div style={{ ...styles.field, ...styles.formFull }}>
              <label style={styles.label}>
                Workspace Mounts{" "}
                <button style={styles.smallButton} onClick={addMount}>+ Add</button>
              </label>
              {editing.workspace_mounts?.map((mount, i) => (
                <div key={i} style={{ marginBottom: "8px", display: "flex", flexDirection: "column", gap: "4px" }}>
                  <div style={styles.mountRow}>
                    <input
                      style={{ ...styles.input, flex: 1 }}
                      value={mount.host_path}
                      onChange={(e) => updateMount(i, "host_path", e.target.value)}
                      placeholder="Host path"
                    />
                    <button style={styles.smallButton} onClick={() => setBrowsingMountIndex(i)} title="Browse">📂</button>
                    <span style={{ color: "#8888a0", fontSize: "0.85rem" }}>→</span>
                    <input
                      style={{ ...styles.input, flex: 1 }}
                      value={mount.container_path || `/workspace/${mount.mount_name}`}
                      onChange={(e) => updateMount(i, "container_path", e.target.value)}
                      placeholder="Container path (e.g. /workspace/myproject)"
                    />
                    <label style={styles.checkboxLabel}>
                      <input type="checkbox" checked={mount.readonly} onChange={(e) => updateMount(i, "readonly", e.target.checked)} style={styles.checkbox} />
                      RO
                    </label>
                    <button style={styles.dangerSmall} onClick={() => removeMount(i)}>X</button>
                  </div>
                </div>
              ))}
            </div>

            {browsingMountIndex !== null && (
              <DirBrowser
                onSelect={(path) => {
                  const idx = browsingMountIndex;
                  const name = path.split("/").filter(Boolean).pop() || "";
                  setEditing((prev) => {
                    if (!prev) return prev;
                    const mounts = [...prev.workspace_mounts];
                    mounts[idx] = {
                      ...mounts[idx],
                      host_path: path,
                      mount_name: mounts[idx].mount_name || name,
                      container_path: mounts[idx].container_path || `/workspace/${mounts[idx].mount_name || name}`,
                    };
                    return { ...prev, workspace_mounts: mounts };
                  });
                  setBrowsingMountIndex(null);
                }}
                onClose={() => setBrowsingMountIndex(null)}
              />
            )}

            {/* Prompt fragment checkboxes removed (Doc 027 Phase 1).
                Fragments are now loaded automatically from disk via manifest.yaml.
                Tier 1 (always-on), Tier 2 (lifecycle), Tier 3 (context-dependent). */}

            {/* Database Assignments (Design Doc 107) */}
            {!isNew && (
              <AgentDatabasesSection agentId={editing.id} />
            )}

            {!isNew && !editing.is_default && (
              <div style={styles.buttonRow}>
                <button style={styles.secondaryButton} onClick={() => setDefault(editing.id)}>
                  Set Default
                </button>
                <button style={styles.dangerButton} onClick={() => deleteAgent(editing.id)}>
                  Delete
                </button>
              </div>
            )}
          </div>
        )}
      </div>

      {/* Container running warning modal */}
      {showContainerWarning && (
        <div style={styles.modalOverlay}>
          <div style={styles.modal}>
            <h3 style={{ color: "#ffcc44", margin: "0 0 12px 0", fontSize: "1.05rem" }}>
              ⚠️ Container is running
            </h3>
            <p style={{ color: "#c0c0d0", margin: "0 0 8px 0", fontSize: "0.9rem", lineHeight: "1.5" }}>
              This agent has a running sandbox container. Saving these changes will cause it to be
              <strong style={{ color: "#ff6c8a" }}> stopped and deleted</strong> when the next conversation
              starts, so it can restart with the new settings.
            </p>
            <p style={{ color: "#8888a0", margin: "0 0 20px 0", fontSize: "0.85rem" }}>
              If a conversation is in progress, it will finish first before the container is recycled.
            </p>
            <div style={{ display: "flex", gap: "12px", justifyContent: "flex-end" }}>
              <button
                style={styles.secondaryButton}
                onClick={() => setShowContainerWarning(false)}
              >
                Cancel
              </button>
              <button
                style={{ ...styles.button, backgroundColor: "#cc7a00" }}
                onClick={doSave}
              >
                Save Anyway
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

const styles: Record<string, React.CSSProperties> = {
  container: {
    display: "flex",
    flexDirection: "column",
    height: "100vh",
    maxWidth: "900px",
    margin: "0 auto",
  },
  header: {
    display: "flex",
    alignItems: "center",
    justifyContent: "space-between",
    padding: "16px 24px",
    borderBottomWidth: "1px", borderBottomStyle: "solid", borderBottomColor: "#1e1e2e",
  },
  topBar: {
    display: "flex", justifyContent: "space-between", alignItems: "center",
    padding: "12px 0", borderBottomWidth: "1px", borderBottomStyle: "solid", borderBottomColor: "#1e1e2e", marginBottom: "16px", flexShrink: 0,
  },
  backLink: { color: "#6c8aff", textDecoration: "none", fontSize: "0.9rem" },
  title: { fontSize: "1.5rem", fontWeight: 700, margin: 0 },
  content: { flex: 1, overflowY: "auto", padding: "24px" },
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
    cursor: "pointer",
    transition: "border-color 0.2s",
  },
  cardHeader: { display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "8px" },
  cardName: { fontSize: "1.1rem", fontWeight: 600, color: "#e0e0e8" },
  badge: {
    backgroundColor: "#6c8aff",
    color: "#fff",
    padding: "2px 8px",
    borderRadius: "4px",
    fontSize: "0.7rem",
    fontWeight: 600,
  },
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
  formFull: { gridColumn: "1 / -1" },
  formRow: { display: "flex", gap: "16px" },
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
  checkboxGrid: {
    display: "flex",
    flexWrap: "wrap" as const,
    gap: "8px 16px",
  },
  checkboxLabel: {
    display: "flex",
    alignItems: "center",
    gap: "6px",
    color: "#e0e0e8",
    fontSize: "0.85rem",
    cursor: "pointer",
  },
  checkbox: { accentColor: "#6c8aff" },
  mountRow: {
    display: "flex",
    gap: "8px",
    alignItems: "center",
    marginBottom: "8px",
  },
  buttonRow: { display: "flex", gap: "12px", marginTop: "8px" },
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
  dangerButton: {
    backgroundColor: "#3a1a1a",
    color: "#ff6c8a",
    borderWidth: "1px", borderStyle: "solid", borderColor: "#5a2a2a",
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
    padding: "2px 8px",
    fontSize: "0.75rem",
    cursor: "pointer",
    marginLeft: "8px",
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
  modalOverlay: {
    position: "fixed" as const,
    top: 0,
    left: 0,
    right: 0,
    bottom: 0,
    backgroundColor: "rgba(0, 0, 0, 0.6)",
    display: "flex",
    alignItems: "center",
    justifyContent: "center",
    zIndex: 1000,
  },
  modal: {
    backgroundColor: "#1a1a2e",
    borderWidth: "1px", borderStyle: "solid", borderColor: "#3a3a4e",
    borderRadius: "12px",
    padding: "24px",
    maxWidth: "480px",
    width: "90%",
    boxShadow: "0 8px 32px rgba(0, 0, 0, 0.4)",
  },
};
