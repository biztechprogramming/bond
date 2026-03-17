import React, { useMemo, useState } from "react";

interface Node {
  id: string;
  type: string;
  host: string;
  port?: number;
  version?: string;
}

interface Edge {
  from: string;
  to: string;
  protocol: string;
  port: number;
}

interface Props {
  topology: { nodes: Node[]; edges: Edge[] };
}

const TYPE_COLORS: Record<string, string> = {
  "app-server": "#6c8aff",
  "application": "#6c8aff",
  "postgresql": "#6cffa0",
  "mysql": "#6cffa0",
  "database": "#6cffa0",
  "redis": "#ffcc6c",
  "cache": "#ffcc6c",
  "cloudfront": "#c06cff",
  "cdn": "#c06cff",
  "unknown": "#8888a0",
};

const TYPE_TIERS: Record<string, number> = {
  "cloudfront": 0, "cdn": 0,
  "app-server": 1, "application": 1,
  "postgresql": 2, "mysql": 2, "database": 2, "redis": 2, "cache": 2,
};

const NODE_W = 160;
const NODE_H = 60;
const TIER_GAP = 120;
const NODE_GAP = 40;

export default function TopologyGraph({ topology }: Props) {
  const [hovered, setHovered] = useState<string | null>(null);

  const { positions, svgW, svgH } = useMemo(() => {
    const tiers: Record<number, Node[]> = {};
    for (const node of topology.nodes) {
      const tier = TYPE_TIERS[node.type] ?? 1;
      (tiers[tier] ||= []).push(node);
    }

    const tierKeys = Object.keys(tiers).map(Number).sort();
    const pos: Record<string, { x: number; y: number }> = {};
    let maxW = 0;

    for (let ti = 0; ti < tierKeys.length; ti++) {
      const nodes = tiers[tierKeys[ti]];
      const totalW = nodes.length * NODE_W + (nodes.length - 1) * NODE_GAP;
      maxW = Math.max(maxW, totalW);
      const startX = 0;
      for (let ni = 0; ni < nodes.length; ni++) {
        pos[nodes[ni].id] = {
          x: startX + ni * (NODE_W + NODE_GAP),
          y: ti * (NODE_H + TIER_GAP) + 20,
        };
      }
    }

    // Center tiers
    for (let ti = 0; ti < tierKeys.length; ti++) {
      const nodes = tiers[tierKeys[ti]];
      const totalW = nodes.length * NODE_W + (nodes.length - 1) * NODE_GAP;
      const offset = (maxW - totalW) / 2;
      for (const n of nodes) pos[n.id].x += offset;
    }

    return {
      positions: pos,
      svgW: maxW + 40,
      svgH: tierKeys.length * (NODE_H + TIER_GAP) + 20,
    };
  }, [topology.nodes]);

  const getColor = (type: string) => TYPE_COLORS[type] || TYPE_COLORS.unknown;

  if (topology.nodes.length === 0) {
    return <div style={{ color: "#8888a0", fontSize: "0.85rem" }}>No topology data.</div>;
  }

  return (
    <svg width={svgW} height={svgH} style={{ overflow: "visible" }}>
      <defs>
        <marker id="arrow" markerWidth="8" markerHeight="6" refX="8" refY="3" orient="auto">
          <path d="M0,0 L8,3 L0,6" fill="#3a3a5a" />
        </marker>
      </defs>

      {/* Edges */}
      {topology.edges.map((edge, i) => {
        const from = positions[edge.from];
        const to = positions[edge.to];
        if (!from || !to) return null;
        const x1 = from.x + NODE_W / 2;
        const y1 = from.y + NODE_H;
        const x2 = to.x + NODE_W / 2;
        const y2 = to.y;
        const midY = (y1 + y2) / 2;
        return (
          <g key={i}>
            <path
              d={`M${x1},${y1} C${x1},${midY} ${x2},${midY} ${x2},${y2}`}
              fill="none"
              stroke="#3a3a5a"
              strokeWidth={1.5}
              markerEnd="url(#arrow)"
            />
            <text x={(x1 + x2) / 2 + 4} y={midY - 4} fill="#8888a0" fontSize="0.65rem">
              {edge.protocol}:{edge.port}
            </text>
          </g>
        );
      })}

      {/* Nodes */}
      {topology.nodes.map((node) => {
        const p = positions[node.id];
        if (!p) return null;
        const color = getColor(node.type);
        const isHov = hovered === node.id;
        return (
          <g
            key={node.id}
            onMouseEnter={() => setHovered(node.id)}
            onMouseLeave={() => setHovered(null)}
            style={{ cursor: "default" }}
          >
            <rect
              x={p.x}
              y={p.y}
              width={NODE_W}
              height={NODE_H}
              rx={10}
              fill="#12121a"
              stroke={isHov ? color : "#1e1e2e"}
              strokeWidth={isHov ? 2 : 1}
            />
            <text x={p.x + NODE_W / 2} y={p.y + 22} textAnchor="middle" fill="#e0e0e8" fontSize="0.8rem" fontWeight={600}>
              {node.id}
            </text>
            <text x={p.x + NODE_W / 2} y={p.y + 38} textAnchor="middle" fill={color} fontSize="0.65rem">
              {node.type} · {node.host}
            </text>
            {node.version && (
              <text x={p.x + NODE_W / 2} y={p.y + 52} textAnchor="middle" fill="#8888a0" fontSize="0.6rem">
                v{node.version}
              </text>
            )}
          </g>
        );
      })}
    </svg>
  );
}
