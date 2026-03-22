import React, { useMemo, useState } from "react";
import { useResources, useEnvironments, useResourceEnvironments, callReducer } from "@/hooks/useSpacetimeDB";
import { GATEWAY_API } from "@/lib/config";

interface InfraMapProps {
  onAddServer: () => void;
}

function statusInfo(status: string): { symbol: string; color: string; label: string } {
  switch (status) {
    case "online": return { symbol: "\u25CF", color: "#6cffa0", label: "online" };
    case "degraded": return { symbol: "\u25D0", color: "#ffcc6c", label: "degraded" };
    case "offline": return { symbol: "\u25CB", color: "#ff6c8a", label: "offline" };
    default: return { symbol: "\u2298", color: "#5a5a6e", label: "unknown" };
  }
}

function relativeTime(ms: number): string {
  if (!ms || ms <= 0) return "never";
  const diff = Date.now() - ms;
  if (diff < 0) return "just now";
  const mins = Math.floor(diff / 60000);
  if (mins < 1) return "just now";
  if (mins < 60) return `${mins}m ago`;
  const hrs = Math.floor(mins / 60);
  if (hrs < 24) return `${hrs}h ago`;
  return `${Math.floor(hrs / 24)}d ago`;
}

export default function InfraMap({ onAddServer }: InfraMapProps) {
  const allResources = useResources();
  const allEnvironments = useEnvironments();
  const resourceEnvs = useResourceEnvironments();
  const [probingIds, setProbingIds] = useState<Set<string>>(new Set());

  const servers = useMemo(() =>
    allResources.filter(r => r.isActive).map(r => {
      const state = (() => { try { return JSON.parse(r.stateJson || "{}"); } catch { return {}; } })();
      return {
        id: r.id,
        name: r.name,
        displayName: r.displayName || r.name,
        resourceType: r.resourceType,
        status: (state.status || (r.lastProbedAt && Number(r.lastProbedAt) > 0 ? "online" : "unknown")) as string,
        lastProbedAt: Number(r.lastProbedAt) || 0,
      };
    }),
    [allResources]
  );

  const assignmentSet = useMemo(() => {
    const s = new Set<string>();
    for (const re of resourceEnvs) s.add(`${re.resourceId}::${re.environmentName}`);
    return s;
  }, [resourceEnvs]);

  const isAssigned = (resourceId: string, envName: string) =>
    assignmentSet.has(`${resourceId}::${envName}`);

  const toggleOne = (resourceId: string, envName: string) => {
    if (isAssigned(resourceId, envName)) {
      const row = resourceEnvs.find(re => re.resourceId === resourceId && re.environmentName === envName);
      if (row) callReducer(conn => conn.reducers.removeResourceEnvironment({ id: row.id }));
    } else {
      callReducer(conn => conn.reducers.addResourceEnvironment({
        id: crypto.randomUUID().replace(/-/g, ""),
        resourceId,
        environmentName: envName,
        createdAt: BigInt(Date.now()),
      }));
    }
  };

  const toggleAllForEnv = (envName: string) => {
    const allOn = servers.every(s => isAssigned(s.id, envName));
    for (const s of servers) {
      if (allOn) {
        const row = resourceEnvs.find(re => re.resourceId === s.id && re.environmentName === envName);
        if (row) callReducer(conn => conn.reducers.removeResourceEnvironment({ id: row.id }));
      } else if (!isAssigned(s.id, envName)) {
        callReducer(conn => conn.reducers.addResourceEnvironment({
          id: crypto.randomUUID().replace(/-/g, ""),
          resourceId: s.id,
          environmentName: envName,
          createdAt: BigInt(Date.now()),
        }));
      }
    }
  };

  const toggleAllForServer = (resourceId: string) => {
    const allOn = allEnvironments.every(e => isAssigned(resourceId, e.name));
    for (const e of allEnvironments) {
      if (allOn) {
        const row = resourceEnvs.find(re => re.resourceId === resourceId && re.environmentName === e.name);
        if (row) callReducer(conn => conn.reducers.removeResourceEnvironment({ id: row.id }));
      } else if (!isAssigned(resourceId, e.name)) {
        callReducer(conn => conn.reducers.addResourceEnvironment({
          id: crypto.randomUUID().replace(/-/g, ""),
          resourceId,
          environmentName: e.name,
          createdAt: BigInt(Date.now()),
        }));
      }
    }
  };

  const handleProbe = async (resourceId: string) => {
    setProbingIds(prev => new Set(prev).add(resourceId));
    try {
      await fetch(`${GATEWAY_API}/deployments/resources/${resourceId}/probe`, { method: "POST" });
    } catch { /* probe failed */ }
    setProbingIds(prev => { const next = new Set(prev); next.delete(resourceId); return next; });
  };

  const handleDelete = (resourceId: string) => {
    callReducer(conn => conn.reducers.updateDeploymentResource({
      id: resourceId,
      displayName: undefined,
      resourceType: undefined,
      environment: undefined,
      connectionJson: undefined,
      capabilitiesJson: undefined,
      stateJson: undefined,
      tagsJson: undefined,
      recommendationsJson: undefined,
      isActive: false,
      updatedAt: BigInt(Date.now()),
      lastProbedAt: undefined,
    }));
  };

  const envCount = allEnvironments.length;
  // Server col + env cols (evenly spaced) + all col + probe col + delete col
  const gridCols = `minmax(180px, 1.5fr) repeat(${envCount}, 1fr) 48px 44px 44px`;

  if (servers.length === 0) {
    return (
      <div style={s.emptyRoot}>
        <div style={{ color: "#5a5a6e", fontSize: "0.95rem", marginBottom: 12 }}>No servers connected. Add one to get started.</div>
        <button style={s.addBtn} onClick={onAddServer}>+ Add Server</button>
      </div>
    );
  }

  return (
    <div style={s.root}>
      <div style={s.header}>
        <h2 style={s.title}>Infrastructure Map</h2>
        <button style={s.addBtn} onClick={onAddServer}>+ Add Server</button>
      </div>

      <div style={s.tableWrap}>
        {/* Header row */}
        <div style={{ ...s.gridRow, gridTemplateColumns: gridCols, position: "sticky", top: 0, backgroundColor: "#0a0a12", zIndex: 1 }}>
          <div style={s.colHeaderServer}>Server</div>
          {allEnvironments.map(env => {
            const allOn = servers.every(srv => isAssigned(srv.id, env.name));
            return (
              <div key={env.name} style={s.colHeaderEnv}>
                <span style={s.envName}>{env.displayName || env.name}</span>
                <span
                  style={{ ...s.allIcon, color: allOn ? "#6cffa0" : "#6c8aff" }}
                  onClick={() => toggleAllForEnv(env.name)}
                  title={allOn ? `Remove all servers from ${env.displayName || env.name}` : `Add all servers to ${env.displayName || env.name}`}
                >
                  {allOn ? "⊖" : "⊕"}
                </span>
              </div>
            );
          })}
          <div style={s.colHeaderIcon} title="Toggle all environments">⊕</div>
          <div style={s.colHeaderIcon} title="Probe">⟳</div>
          <div style={s.colHeaderIcon} title="Delete">✕</div>
        </div>

        {/* Server rows */}
        {servers.map(srv => {
          const st = statusInfo(srv.status);
          const allEnvsOn = allEnvironments.every(e => isAssigned(srv.id, e.name));
          const isProbing = probingIds.has(srv.id);

          return (
            <div key={srv.id} style={{ ...s.gridRow, gridTemplateColumns: gridCols, borderBottomWidth: "1px", borderBottomStyle: "solid", borderBottomColor: "#1e1e2e" }}>
              {/* Server info */}
              <div style={s.serverCell}>
                <div style={s.serverTopRow}>
                  <span style={{ color: st.color, fontSize: "0.6rem", marginRight: 6 }}>{st.symbol}</span>
                  <span style={s.serverName}>{srv.displayName}</span>
                </div>
                <div style={s.serverSubRow}>
                  <span style={s.resourceType}>{srv.resourceType}</span>
                  <span style={s.sep}>·</span>
                  <span style={s.probeTime}>{relativeTime(srv.lastProbedAt)}</span>
                </div>
              </div>

              {/* Environment dots */}
              {allEnvironments.map(env => {
                const on = isAssigned(srv.id, env.name);
                return (
                  <div key={env.name} style={s.dotCell}>
                    <DotToggle on={on} onClick={() => toggleOne(srv.id, env.name)} />
                  </div>
                );
              })}

              {/* Toggle all envs for this server */}
              <div style={s.actionCell}>
                <span
                  style={{ ...s.actionIcon, color: allEnvsOn ? "#6cffa0" : "#6c8aff" }}
                  onClick={() => toggleAllForServer(srv.id)}
                  title={allEnvsOn ? "Remove from all environments" : "Add to all environments"}
                >
                  {allEnvsOn ? "⊖" : "⊕"}
                </span>
              </div>

              {/* Probe */}
              <div style={s.actionCell}>
                <span
                  style={{ ...s.actionIcon, color: isProbing ? "#3a3a4e" : "#8888a0", cursor: isProbing ? "default" : "pointer" }}
                  onClick={() => !isProbing && handleProbe(srv.id)}
                  title="Probe server"
                >
                  {isProbing ? "⏳" : "⟳"}
                </span>
              </div>

              {/* Delete */}
              <div style={s.actionCell}>
                <span
                  style={{ ...s.actionIcon, color: "#ff6c8a55" }}
                  onClick={() => handleDelete(srv.id)}
                  title="Remove server"
                  onMouseEnter={e => (e.currentTarget.style.color = "#ff6c8a")}
                  onMouseLeave={e => (e.currentTarget.style.color = "#ff6c8a55")}
                >
                  ✕
                </span>
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}

function DotToggle({ on, onClick }: { on: boolean; onClick: () => void }) {
  const [hovered, setHovered] = React.useState(false);
  return (
    <div
      onClick={onClick}
      onMouseEnter={() => setHovered(true)}
      onMouseLeave={() => setHovered(false)}
      style={{
        width: 26,
        height: 26,
        borderRadius: "50%",
        cursor: "pointer",
        backgroundColor: on ? "#6cffa0" : "#1a1a2e",
        border: `2px solid ${hovered ? "#6c8aff" : on ? "#6cffa0" : "#3a3a4e"}`,
        boxShadow: on ? "0 0 8px #6cffa033" : "none",
        transition: "all 0.15s ease",
        margin: "0 auto",
      }}
    />
  );
}

const s: Record<string, React.CSSProperties> = {
  root: { display: "flex", flexDirection: "column", gap: 16 },
  emptyRoot: {
    backgroundColor: "#1a1a2e",
    borderRadius: 8,
    borderWidth: "1px", borderStyle: "solid", borderColor: "#3a3a4e",
    padding: 32,
    textAlign: "center",
  },
  header: { display: "flex", justifyContent: "space-between", alignItems: "center" },
  title: { fontSize: "1.1rem", fontWeight: 600, color: "#6c8aff", margin: 0 },
  addBtn: {
    backgroundColor: "#2a2a3e",
    color: "#e0e0e8",
    borderWidth: "1px", borderStyle: "solid", borderColor: "#3a3a4e",
    borderRadius: 8,
    padding: "8px 16px",
    fontSize: "0.85rem",
    cursor: "pointer",
  },
  tableWrap: {
    overflowX: "auto",
  },
  gridRow: {
    display: "grid",
    alignItems: "center",
    minHeight: 56,
  },
  colHeaderServer: {
    padding: "10px 12px",
    color: "#5a5a6e",
    fontSize: "0.72rem",
    fontWeight: 600,
    textTransform: "uppercase" as const,
    letterSpacing: "0.5px",
  },
  colHeaderEnv: {
    display: "flex",
    flexDirection: "column",
    alignItems: "center",
    gap: 2,
    padding: "8px 4px",
  },
  colHeaderIcon: {
    textAlign: "center",
    color: "#5a5a6e",
    fontSize: "0.7rem",
    padding: "8px 0",
  },
  envName: {
    color: "#8888a0",
    fontSize: "0.78rem",
    fontWeight: 500,
  },
  allIcon: {
    fontSize: "1rem",
    cursor: "pointer",
    lineHeight: 1,
    transition: "color 0.15s",
  },
  serverCell: {
    padding: "10px 12px",
    display: "flex",
    flexDirection: "column",
    gap: 2,
  },
  serverTopRow: {
    display: "flex",
    alignItems: "center",
  },
  serverName: {
    color: "#e0e0e8",
    fontWeight: 600,
    fontSize: "0.88rem",
  },
  serverSubRow: {
    display: "flex",
    alignItems: "center",
    gap: 2,
    paddingLeft: 16,
  },
  resourceType: {
    color: "#5a5a6e",
    fontSize: "0.7rem",
    textTransform: "uppercase" as const,
    letterSpacing: "0.5px",
  },
  sep: {
    color: "#3a3a4e",
    margin: "0 4px",
    fontSize: "0.7rem",
  },
  probeTime: {
    fontSize: "0.7rem",
    color: "#5a5a6e",
  },
  dotCell: {
    display: "flex",
    justifyContent: "center",
    alignItems: "center",
    padding: "8px 0",
  },
  actionCell: {
    display: "flex",
    justifyContent: "center",
    alignItems: "center",
    padding: "8px 0",
  },
  actionIcon: {
    fontSize: "1.1rem",
    cursor: "pointer",
    lineHeight: 1,
    transition: "color 0.15s",
  },
};
