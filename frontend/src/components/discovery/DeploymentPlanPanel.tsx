"use client";

import React, { useState } from "react";
import type { DiscoveryState, CompletenessReport, FieldConfidence } from "@/lib/discovery-types";

interface Props {
  state: DiscoveryState | null;
  completeness: CompletenessReport | null;
  onEditField: (field: string, value: string) => void;
  onShipIt: () => void;
}

const REQUIRED_FIELDS = ["source", "framework", "build_strategy", "target_server", "app_port"];
const RECOMMENDED_FIELDS = ["env_vars", "health_endpoint", "services"];
const OPTIONAL_FIELDS = ["ports", "repo_url"];

const FIELD_LABELS: Record<string, string> = {
  source: "Source",
  framework: "Framework",
  build_strategy: "Build Strategy",
  target_server: "Target Server",
  app_port: "App Port",
  env_vars: "Environment Variables",
  health_endpoint: "Health Endpoint",
  services: "Services",
  ports: "Ports",
  repo_url: "Repository URL",
};

function confidenceIcon(conf?: FieldConfidence): string {
  if (!conf) return "";
  if (conf.source === "user-provided") return "\ud83d\udc64";
  if (conf.score <= 0.5) return "\u26a0\ufe0f";
  if (conf.source === "inferred") return "~";
  return "\u2713";
}

function confidenceColor(conf?: FieldConfidence): string {
  if (!conf) return "#5a5a70";
  if (conf.score <= 0.5) return "#ffcc6c";
  if (conf.source === "user-provided") return "#6c8aff";
  if (conf.source === "inferred") return "#8888a0";
  return "#6cffa0";
}

function formatFieldValue(field: string, value: unknown): string {
  if (value === undefined || value === null) return "\u2014";
  if (typeof value === "string" || typeof value === "number") return String(value);
  if (typeof value === "object") {
    if ("framework" in (value as any)) return (value as any).framework;
    if ("strategy" in (value as any)) return (value as any).strategy;
    if ("host" in (value as any)) return `${(value as any).host}:${(value as any).port || 22}`;
    if ("path" in (value as any)) return (value as any).path;
    if (Array.isArray(value)) return `${value.length} item(s)`;
    return JSON.stringify(value);
  }
  return String(value);
}

function FieldRow({
  field,
  value,
  confidence,
  onEdit,
}: {
  field: string;
  value: unknown;
  confidence?: FieldConfidence;
  onEdit: (field: string, value: string) => void;
}) {
  const [hovering, setHovering] = useState(false);

  return (
    <div
      style={styles.fieldRow}
      onMouseEnter={() => setHovering(true)}
      onMouseLeave={() => setHovering(false)}
    >
      <span style={{ ...styles.confIcon, color: confidenceColor(confidence) }}>
        {confidenceIcon(confidence)}
      </span>
      <span style={styles.fieldLabel}>{FIELD_LABELS[field] || field}</span>
      <span style={styles.fieldValue}>{formatFieldValue(field, value)}</span>
      {hovering && (
        <button
          style={styles.editBtn}
          aria-label={`Edit ${FIELD_LABELS[field] || field}`}
          onClick={() => {
            const current = formatFieldValue(field, value);
            const newVal = prompt(`Edit ${FIELD_LABELS[field] || field}:`, current === "\u2014" ? "" : current);
            if (newVal !== null) onEdit(field, newVal);
          }}
        >
          Edit
        </button>
      )}
    </div>
  );
}

function Section({
  title,
  fields,
  state,
  onEdit,
  defaultExpanded,
}: {
  title: string;
  fields: string[];
  state: DiscoveryState;
  onEdit: (field: string, value: string) => void;
  defaultExpanded: boolean;
}) {
  const [expanded, setExpanded] = useState(defaultExpanded);

  return (
    <div style={styles.section}>
      <button
        style={styles.sectionHeader}
        onClick={() => setExpanded(!expanded)}
        aria-expanded={expanded}
      >
        <span>{expanded ? "\u25bc" : "\u25b6"}</span>
        <span style={styles.sectionTitle}>{title}</span>
        <span style={styles.sectionCount}>
          {fields.filter((f) => (state.findings as any)[f] != null).length}/{fields.length}
        </span>
      </button>
      {expanded && (
        <div style={styles.sectionBody}>
          {fields.map((field) => (
            <FieldRow
              key={field}
              field={field}
              value={(state.findings as any)[field]}
              confidence={state.confidence[field]}
              onEdit={onEdit}
            />
          ))}
        </div>
      )}
    </div>
  );
}

function ProgressBar({ label, value }: { label: string; value: number }) {
  const pct = Math.round(value * 100);
  return (
    <div style={styles.progressContainer}>
      <div style={styles.progressHeader}>
        <span style={styles.progressLabel}>{label}</span>
        <span style={styles.progressPct}>{pct}%</span>
      </div>
      <div style={styles.progressTrack} role="progressbar" aria-valuenow={pct} aria-valuemin={0} aria-valuemax={100}>
        <div
          style={{
            ...styles.progressFill,
            width: `${pct}%`,
            backgroundColor: pct >= 100 ? "#6cffa0" : "#6c8aff",
          }}
        />
      </div>
    </div>
  );
}

export default function DeploymentPlanPanel({ state, completeness, onEditField, onShipIt }: Props) {
  if (!state) {
    return (
      <div style={styles.container}>
        <h3 style={styles.heading}>Deployment Plan</h3>
        <p style={styles.placeholder}>Waiting for discovery data...</p>
      </div>
    );
  }

  return (
    <div style={styles.container}>
      <h3 style={styles.heading}>Deployment Plan</h3>

      {completeness && (
        <div style={styles.progressSection}>
          <ProgressBar label="Required" value={completeness.required_coverage} />
          <ProgressBar label="Recommended" value={completeness.recommended_coverage} />
        </div>
      )}

      <Section title="Required" fields={REQUIRED_FIELDS} state={state} onEdit={onEditField} defaultExpanded={true} />
      <Section title="Recommended" fields={RECOMMENDED_FIELDS} state={state} onEdit={onEditField} defaultExpanded={true} />
      <Section title="Optional" fields={OPTIONAL_FIELDS} state={state} onEdit={onEditField} defaultExpanded={false} />

      <button
        style={{
          ...styles.shipBtn,
          opacity: completeness?.ready ? 1 : 0.4,
          cursor: completeness?.ready ? "pointer" : "not-allowed",
        }}
        disabled={!completeness?.ready}
        onClick={onShipIt}
      >
        Ship It
      </button>
    </div>
  );
}

const styles: Record<string, React.CSSProperties> = {
  container: {
    backgroundColor: "#12121a",
    borderWidth: 1,
    borderStyle: "solid",
    borderColor: "#1e1e2e",
    borderRadius: 12,
    padding: 16,
    display: "flex",
    flexDirection: "column",
    gap: 12,
  },
  heading: { fontSize: "1rem", fontWeight: 700, color: "#e0e0e8", margin: 0 },
  placeholder: { fontSize: "0.85rem", color: "#5a5a70", margin: 0 },
  progressSection: { display: "flex", flexDirection: "column", gap: 8 },
  progressContainer: {},
  progressHeader: { display: "flex", justifyContent: "space-between", marginBottom: 4 },
  progressLabel: { fontSize: "0.75rem", color: "#8888a0" },
  progressPct: { fontSize: "0.75rem", color: "#e0e0e8", fontWeight: 600 },
  progressTrack: {
    height: 6,
    backgroundColor: "#1e1e2e",
    borderRadius: 3,
    overflow: "hidden",
  },
  progressFill: {
    height: "100%",
    borderRadius: 3,
    transition: "width 200ms ease",
  },
  section: {
    borderWidth: 1,
    borderStyle: "solid",
    borderColor: "#1e1e2e",
    borderRadius: 8,
    overflow: "hidden",
  },
  sectionHeader: {
    display: "flex",
    alignItems: "center",
    gap: 8,
    width: "100%",
    padding: "8px 12px",
    backgroundColor: "#0a0a12",
    borderWidth: 0,
    borderStyle: "none",
    borderColor: "transparent",
    color: "#e0e0e8",
    fontSize: "0.85rem",
    fontWeight: 600,
    cursor: "pointer",
    textAlign: "left" as const,
  },
  sectionTitle: { flex: 1 },
  sectionCount: { fontSize: "0.75rem", color: "#8888a0" },
  sectionBody: { padding: "4px 0" },
  fieldRow: {
    display: "flex",
    alignItems: "center",
    gap: 8,
    padding: "6px 12px",
    fontSize: "0.83rem",
    minHeight: 32,
  },
  confIcon: { width: 18, textAlign: "center" as const, fontSize: "0.8rem" },
  fieldLabel: { width: 120, color: "#8888a0", flexShrink: 0 },
  fieldValue: { flex: 1, color: "#e0e0e8" },
  editBtn: {
    backgroundColor: "transparent",
    borderWidth: 1,
    borderStyle: "solid",
    borderColor: "#2a2a3e",
    borderRadius: 4,
    color: "#8888a0",
    fontSize: "0.7rem",
    padding: "2px 8px",
    cursor: "pointer",
  },
  shipBtn: {
    backgroundColor: "#6cffa0",
    color: "#0a0a1a",
    borderWidth: 0,
    borderStyle: "none",
    borderColor: "transparent",
    borderRadius: 8,
    padding: "12px 24px",
    fontSize: "0.9rem",
    fontWeight: 700,
    marginTop: 8,
  },
};
