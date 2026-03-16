import React, { useEffect, useMemo, useState, useCallback } from "react";
import { GATEWAY_API } from "@/lib/config";

interface Props {
  environments: string[];
}

interface InfraNode {
  id: string;
  name: string;
  type: string;
  tier: "edge" | "application" | "data";
  host?: string;
  port?: number;
  health: "healthy" | "degraded" | "unhealthy" | "unknown";
  stats?: { cpu?: number; memory?: number; requests?: number; latencyMs?: number };
}

interface InfraEdge {
  from: string;
  to: string;
  protocol: string;
  port: number;
  health: "healthy" | "degraded" | "unhealthy" | "unknown";
}

const TIER_LABELS: Record<string, string> = { edge: "CDN / Edge", application: "Application", data: "Data" };
const TIER_ORDER: Array<"edge" | "application" | "data"> = ["edge", "application", "data"];

const TYPE_TO_TIER: Record<string, "edge" | "application" | "data"> = {
  cloudfront: "edge", cdn: "edge", "load-balancer": "edge",
  "app-server": "application", application: "application", web_server: "application", api: "application",
  postgresql: "data", mysql: "data", database: "data", redis: "data", cache: "data", s3: "data", storage: "data",
};

const HEALTH_INDICATORS: Record<string, { symbol: string; color: string }> = {
  healthy: { symbol: "●", color: "#6cffa0" },
  degraded: { symbol: "◐", color: "#ffcc6c" },
  unhealthy: { symbol: "○", color: "#ff6c8a" },
  unknown: { symbol: "⊘", color: "#8888a0" },
};

const EDGE_HEALTH_COLORS: Record<string, string> = {
  healthy: "#3a5a3a",
  degraded: "#5a5a3a",
  unhealthy: "#5a3a3a",
  unknown: "#3a3a5a",
};

const NODE_W = 180;
const NODE_H = 70;
const TIER_GAP = 140;
const NODE_GAP = 40;
const TIER_LABEL_H = 30;

export default function InfraMap({ environments }: Props) {
  const [env, setEnv] = useState(environments[0] || "production");
  const [nodes, setNodes] = useState<InfraNode[]>([]);
  const [edges, setEdges] = useState<InfraEdge[]>([]);
  const [loading, setLoading] = useState(true);
  const [hoveredNode, setHoveredNode] = useState<string | null>(null);
  const [selectedNode, setSelectedNode] = useState<InfraNode | null>(null);

  const fetchData = useCallback(async () => {
    setLoading(true);
    try {
      const [resRes, manRes] = await Promise.all([
        fetch(`${GATEWAY_API}/deployments/resources/${env}`),
        fetch(`${GATEWAY_API}/deployments/discovery/manifests`),
      ]);
      const resources = resRes.ok ? await resRes.json() : [];
      const manifests = manRes.ok ? await manRes.json() : [];

      const nodeMap = new Map<string, InfraNode>();
      const edgeList: InfraEdge[] = [];

      // From resources
      for (const r of (Array.isArray(resources) ? resources : resources.resources || [])) {
        nodeMap.set(r.id || r.name, {
          id: r.id || r.name, name: r.name, type: r.type || "unknown",
          tier: TYPE_TO_TIER[r.type] || "application",
          host: r.host, port: r.port,
          health: r.health || "unknown",
          stats: r.stats,
        });
      }

      // From manifests
      for (const m of manifests) {
        if (!m.layers) continue;
        for (const [key, val] of Object.entries(m.layers as Record<string, any>)) {
          if (key === "dns") continue;
          if (key === "topology" && val) {
            const topo = val as any;
            for (const e of (topo.edges || [])) {
              edgeList.push({ from: e.from, to: e.to, protocol: e.protocol || "tcp", port: e.port || 0, health: "unknown" });
            }
            continue;
          }
          const items = Array.isArray(val) ? val : val ? [val] : [];
          for (const item of items) {
            const id = item.id || item.name || key;
            if (!nodeMap.has(id)) {
              const type = item.type || key;
              nodeMap.set(id, { id, name: item.name || key, type, tier: TYPE_TO_TIER[type] || "application", host: item.host, port: item.port, health: "unknown" });
            }
          }
        }
      }

      setNodes(Array.from(nodeMap.values()));
      setEdges(edgeList);
    } catch { setNodes([]); setEdges([]); }
    setLoading(false);
  }, [env]);

  useEffect(() => { fetchData(); }, [fetchData]);

  // Poll resource usage
  useEffect(() => {
    const interval = setInterval(async () => {
      try {
        const res = await fetch(`${GATEWAY_API}/deployments/resource-usage/${env}`);
        if (!res.ok) return;
        const usage = await res.json();
        setNodes((prev) => prev.map((n) => {
          const u = (usage.nodes || usage)?.[n.id];
          return u ? { ...n, health: u.health || n.health, stats: { ...n.stats, ...u.stats } } : n;
        }));
      } catch { /* ignore */ }
    }, 15000);
    return () => clearInterval(interval);
  }, [env]);

  const { positions, svgW, svgH } = useMemo(() => {
    const tiers: Record<string, InfraNode[]> = {};
    for (const node of nodes) {
      (tiers[node.tier] ||= []).push(node);
    }

    const pos: Record<string, { x: number; y: number }> = {};
    let maxW = 0;

    for (let ti = 0; ti < TIER_ORDER.length; ti++) {
      const tierNodes = tiers[TIER_ORDER[ti]] || [];
      const totalW = tierNodes.length * NODE_W + (tierNodes.length - 1) * NODE_GAP;
      maxW = Math.max(maxW, totalW);
      for (let ni = 0; ni < tierNodes.length; ni++) {
        pos[tierNodes[ni].id] = { x: ni * (NODE_W + NODE_GAP), y: ti * (NODE_H + TIER_GAP) + TIER_LABEL_H + 20 };
      }
    }

    // Center tiers
    for (const tier of TIER_ORDER) {
      const tierNodes = tiers[tier] || [];
      const totalW = tierNodes.length * NODE_W + (tierNodes.length - 1) * NODE_GAP;
      const off = (maxW - totalW) / 2;
      for (const n of tierNodes) pos[n.id].x += off;
    }

    return { positions: pos, svgW: Math.max(maxW + 40, 400), svgH: TIER_ORDER.length * (NODE_H + TIER_GAP) + TIER_LABEL_H + 40 };
  }, [nodes]);

  const handleExportSvg = () => {
    const svgEl = document.getElementById("infra-map-svg");
    if (!svgEl) return;
    const data = new XMLSerializer().serializeToString(svgEl);
    const blob = new Blob([data], { type: "image/svg+xml" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url; a.download = `infra-map-${env}.svg`; a.click();
    URL.revokeObjectURL(url);
  };

  const handleFullscreen = () => {
    const el = document.getElementById("infra-map-container");
    if (el?.requestFullscreen) el.requestFullscreen();
  };

  if (loading) return <div style={{ color: "#8888a0", fontSize: "0.85rem" }}>Loading infrastructure...</div>;

  return (
    <div id="infra-map-container" style={styles.container}>
      {/* Toolbar */}
      <div style={styles.toolbar}>
        <select style={styles.select} value={env} onChange={(e) => setEnv(e.target.value)}>
          {environments.map((e) => <option key={e} value={e}>{e}</option>)}
        </select>
        <button style={styles.secondaryButton} onClick={fetchData}>Refresh</button>
        <button style={styles.secondaryButton} onClick={handleExportSvg}>Export SVG</button>
        <button style={styles.secondaryButton} onClick={handleFullscreen}>Fullscreen</button>
      </div>

      {/* SVG Map */}
      <div style={styles.mapWrapper}>
        {nodes.length === 0 ? (
          <div style={{ color: "#8888a0", fontSize: "0.85rem", padding: 20 }}>No infrastructure data for {env}.</div>
        ) : (
          <svg id="infra-map-svg" width={svgW} height={svgH} style={{ overflow: "visible" }}>
            <defs>
              <marker id="infra-arrow" markerWidth="8" markerHeight="6" refX="8" refY="3" orient="auto">
                <path d="M0,0 L8,3 L0,6" fill="#3a3a5a" />
              </marker>
            </defs>

            {/* Tier Labels */}
            {TIER_ORDER.map((tier, ti) => (
              <text key={tier} x={10} y={ti * (NODE_H + TIER_GAP) + 18} fill="#5a5a70" fontSize="0.7rem" fontWeight={600}>
                {TIER_LABELS[tier]}
              </text>
            ))}

            {/* Edges */}
            {edges.map((edge, i) => {
              const from = positions[edge.from];
              const to = positions[edge.to];
              if (!from || !to) return null;
              const x1 = from.x + NODE_W / 2;
              const y1 = from.y + NODE_H;
              const x2 = to.x + NODE_W / 2;
              const y2 = to.y;
              const midY = (y1 + y2) / 2;
              const edgeColor = EDGE_HEALTH_COLORS[edge.health] || EDGE_HEALTH_COLORS.unknown;
              return (
                <g key={i}>
                  <path d={`M${x1},${y1} C${x1},${midY} ${x2},${midY} ${x2},${y2}`} fill="none" stroke={edgeColor} strokeWidth={1.5} markerEnd="url(#infra-arrow)" />
                  <text x={(x1 + x2) / 2 + 4} y={midY - 4} fill="#8888a0" fontSize="0.6rem">
                    {edge.protocol}{edge.port ? `:${edge.port}` : ""}
                  </text>
                </g>
              );
            })}

            {/* Nodes */}
            {nodes.map((node) => {
              const p = positions[node.id];
              if (!p) return null;
              const hi = HEALTH_INDICATORS[node.health] || HEALTH_INDICATORS.unknown;
              const isHov = hoveredNode === node.id;
              const isSel = selectedNode?.id === node.id;
              return (
                <g
                  key={node.id}
                  onMouseEnter={() => setHoveredNode(node.id)}
                  onMouseLeave={() => setHoveredNode(null)}
                  onClick={() => setSelectedNode(isSel ? null : node)}
                  style={{ cursor: "pointer" }}
                >
                  <rect x={p.x} y={p.y} width={NODE_W} height={NODE_H} rx={10} fill="#12121a" stroke={isSel ? "#6c8aff" : isHov ? "#3a3a5a" : "#1e1e2e"} strokeWidth={isSel ? 2 : isHov ? 2 : 1} />
                  {/* Health indicator */}
                  <text x={p.x + 12} y={p.y + 20} fill={hi.color} fontSize="0.7rem">{hi.symbol}</text>
                  <text x={p.x + 26} y={p.y + 20} fill="#e0e0e8" fontSize="0.8rem" fontWeight={600}>{node.name}</text>
                  <text x={p.x + NODE_W / 2} y={p.y + 38} textAnchor="middle" fill="#8888a0" fontSize="0.65rem">
                    {node.type}{node.host ? ` · ${node.host}` : ""}
                  </text>
                  {node.stats && (
                    <text x={p.x + NODE_W / 2} y={p.y + 52} textAnchor="middle" fill="#5a5a70" fontSize="0.55rem">
                      {node.stats.cpu != null ? `CPU ${node.stats.cpu}%` : ""}{node.stats.memory != null ? ` MEM ${node.stats.memory}%` : ""}{node.stats.latencyMs != null ? ` ${node.stats.latencyMs}ms` : ""}
                    </text>
                  )}
                </g>
              );
            })}
          </svg>
        )}
      </div>

      {/* Node Detail Popup */}
      {selectedNode && (
        <div style={styles.card}>
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
            <span style={styles.cardTitle}>{selectedNode.name}</span>
            <span style={{ color: HEALTH_INDICATORS[selectedNode.health]?.color || "#8888a0", fontSize: "0.8rem", fontWeight: 600 }}>
              {HEALTH_INDICATORS[selectedNode.health]?.symbol} {selectedNode.health}
            </span>
          </div>
          <div style={styles.statGrid}>
            <div style={styles.statItem}><span style={styles.statLabel}>Type</span><span style={styles.statValue}>{selectedNode.type}</span></div>
            <div style={styles.statItem}><span style={styles.statLabel}>Host</span><span style={styles.statValue}>{selectedNode.host || "—"}</span></div>
            {selectedNode.port && <div style={styles.statItem}><span style={styles.statLabel}>Port</span><span style={styles.statValue}>{selectedNode.port}</span></div>}
            {selectedNode.stats?.cpu != null && <div style={styles.statItem}><span style={styles.statLabel}>CPU</span><span style={styles.statValue}>{selectedNode.stats.cpu}%</span></div>}
            {selectedNode.stats?.memory != null && <div style={styles.statItem}><span style={styles.statLabel}>Memory</span><span style={styles.statValue}>{selectedNode.stats.memory}%</span></div>}
            {selectedNode.stats?.requests != null && <div style={styles.statItem}><span style={styles.statLabel}>Requests</span><span style={styles.statValue}>{selectedNode.stats.requests}/s</span></div>}
            {selectedNode.stats?.latencyMs != null && <div style={styles.statItem}><span style={styles.statLabel}>Latency</span><span style={styles.statValue}>{selectedNode.stats.latencyMs}ms</span></div>}
          </div>
          <div style={{ display: "flex", gap: 8 }}>
            <button style={styles.actionButton}>View Logs</button>
            <button style={styles.actionButton}>Health Check</button>
            <button style={styles.secondaryButton} onClick={() => setSelectedNode(null)}>Close</button>
          </div>
        </div>
      )}

      {/* Legend */}
      <div style={styles.legend}>
        <span style={{ fontSize: "0.7rem", color: "#8888a0", fontWeight: 600 }}>HEALTH:</span>
        {Object.entries(HEALTH_INDICATORS).map(([key, val]) => (
          <span key={key} style={{ fontSize: "0.7rem", color: val.color }}>{val.symbol} {key}</span>
        ))}
      </div>
    </div>
  );
}

const styles: Record<string, React.CSSProperties> = {
  container: { display: "flex", flexDirection: "column", gap: 12 },
  toolbar: { display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" },
  select: { backgroundColor: "#16162a", color: "#e0e0e8", border: "1px solid #3a3a5a", borderRadius: 6, padding: "6px 10px", fontSize: "0.8rem" },
  mapWrapper: { backgroundColor: "#0a0a12", border: "1px solid #1e1e2e", borderRadius: 12, padding: 16, overflow: "auto", minHeight: 300 },
  card: { backgroundColor: "#12121a", border: "1px solid #1e1e2e", borderRadius: 12, padding: 16, display: "flex", flexDirection: "column", gap: 10 },
  cardTitle: { fontSize: "0.85rem", fontWeight: 600, color: "#e0e0e8" },
  statGrid: { display: "flex", gap: 16, flexWrap: "wrap" },
  statItem: { display: "flex", flexDirection: "column", gap: 2 },
  statLabel: { fontSize: "0.7rem", color: "#8888a0" },
  statValue: { fontSize: "0.85rem", color: "#e0e0e8" },
  legend: { display: "flex", gap: 12, alignItems: "center", padding: "8px 0" },
  secondaryButton: { backgroundColor: "#2a2a3e", color: "#e0e0e8", border: "1px solid #3a3a4e", borderRadius: 6, padding: "6px 12px", fontSize: "0.8rem", cursor: "pointer" },
  actionButton: { backgroundColor: "#2a2a6a", color: "#6c8aff", border: "1px solid #3a3a8a", borderRadius: 6, padding: "6px 12px", fontSize: "0.8rem", cursor: "pointer" },
};
