import React, { useEffect, useState } from "react";
import { GATEWAY_API } from "@/lib/config";

interface Props {
  appName: string;
  onBack: () => void;
}

interface Script {
  name: string;
  description: string;
  level: number;
  content: string;
  path?: string;
}

interface Proposals {
  scripts: Script[];
  proposal_md?: string;
}

const LEVELS = [
  { level: 0, label: "Level 0 — Replication" },
  { level: 1, label: "Level 1 — Operational" },
  { level: 2, label: "Level 2 — Architecture" },
  { level: 3, label: "Level 3 — Platform" },
];

export default function ProposalViewer({ appName, onBack }: Props) {
  const [proposals, setProposals] = useState<Proposals | null>(null);
  const [loading, setLoading] = useState(true);
  const [activeLevel, setActiveLevel] = useState(0);
  const [expandedScript, setExpandedScript] = useState<string | null>(null);
  const [msg, setMsg] = useState("");

  useEffect(() => {
    setLoading(true);
    fetch(`${GATEWAY_API}/deployments/discovery/proposals/${appName}`)
      .then((r) => r.ok ? r.json() : null)
      .then((data) => setProposals(data))
      .catch(() => setProposals(null))
      .finally(() => setLoading(false));
  }, [appName]);

  const handleAccept = async (script: Script) => {
    setMsg("");
    try {
      const res = await fetch(`${GATEWAY_API}/deployments/scripts`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name: script.name, description: script.description, content: script.content, level: script.level }),
      });
      if (res.ok) setMsg(`Registered: ${script.name}`);
      else setMsg(`Failed to register ${script.name}`);
    } catch (err: any) {
      setMsg(`Error: ${err.message}`);
    }
  };

  if (loading) return <div style={{ color: "#8888a0", fontSize: "0.85rem" }}>Loading proposals...</div>;
  if (!proposals) return <div style={{ color: "#8888a0", fontSize: "0.85rem" }}>No proposals found for {appName}.</div>;

  const levelScripts = (proposals.scripts || []).filter((s) => s.level === activeLevel);

  return (
    <div style={styles.container}>
      <div style={styles.headerRow}>
        <h2 style={styles.title}>Proposals — {appName}</h2>
        <button style={styles.secondaryButton} onClick={onBack}>Back</button>
      </div>

      {/* Level Tabs */}
      <div style={styles.tabRow}>
        {LEVELS.map((l) => (
          <button
            key={l.level}
            style={activeLevel === l.level ? styles.activeTab : styles.tab}
            onClick={() => setActiveLevel(l.level)}
          >
            {l.label}
          </button>
        ))}
      </div>

      {/* Level 3: proposal.md */}
      {activeLevel === 3 && proposals.proposal_md && (
        <div style={styles.card}>
          <span style={styles.cardTitle}>Platform Proposal</span>
          <pre style={styles.codeBlock}>{proposals.proposal_md}</pre>
        </div>
      )}

      {/* Scripts */}
      {levelScripts.length === 0 && !(activeLevel === 3 && proposals.proposal_md) ? (
        <div style={{ color: "#8888a0", fontSize: "0.85rem" }}>No scripts at this level.</div>
      ) : (
        levelScripts.map((script) => {
          const isOpen = expandedScript === script.name;
          return (
            <div key={script.name} style={styles.card}>
              <div style={styles.scriptHeader} onClick={() => setExpandedScript(isOpen ? null : script.name)}>
                <div style={{ flex: 1 }}>
                  <div style={{ fontSize: "0.85rem", fontWeight: 600, color: "#e0e0e8" }}>{script.name}</div>
                  <div style={{ fontSize: "0.75rem", color: "#8888a0", marginTop: 2 }}>{script.description}</div>
                </div>
                <span style={{ color: "#8888a0", fontSize: "0.8rem" }}>{isOpen ? "▾" : "▸"}</span>
              </div>
              {isOpen && (
                <>
                  <pre style={styles.codeBlock}>{script.content}</pre>
                  <div style={{ display: "flex", gap: 8, marginTop: 4 }}>
                    <button style={styles.acceptButton} onClick={() => handleAccept(script)}>Accept &amp; Register</button>
                    <button style={styles.warningButton}>Modify</button>
                    <button style={styles.rejectButton}>Reject</button>
                  </div>
                </>
              )}
            </div>
          );
        })
      )}

      {msg && <div style={{ fontSize: "0.85rem", color: msg.startsWith("Error") || msg.startsWith("Failed") ? "#ff6c8a" : "#6cffa0" }}>{msg}</div>}
    </div>
  );
}

const styles: Record<string, React.CSSProperties> = {
  container: { display: "flex", flexDirection: "column", gap: 12 },
  headerRow: { display: "flex", justifyContent: "space-between", alignItems: "center" },
  title: { fontSize: "1.1rem", fontWeight: 600, color: "#6c8aff", margin: 0 },
  tabRow: { display: "flex", gap: 4, flexWrap: "wrap" },
  tab: {
    backgroundColor: "#12121a",
    color: "#8888a0",
    border: "1px solid #1e1e2e",
    borderRadius: 6,
    padding: "6px 12px",
    fontSize: "0.8rem",
    cursor: "pointer",
  },
  activeTab: {
    backgroundColor: "#2a2a4a",
    color: "#e0e0e8",
    border: "1px solid #6c8aff",
    borderRadius: 6,
    padding: "6px 12px",
    fontSize: "0.8rem",
    cursor: "pointer",
    fontWeight: 600,
  },
  card: {
    backgroundColor: "#12121a",
    border: "1px solid #1e1e2e",
    borderRadius: 12,
    padding: 16,
    display: "flex",
    flexDirection: "column",
    gap: 10,
  },
  cardTitle: { fontSize: "0.8rem", fontWeight: 600, color: "#8888a0", textTransform: "uppercase" as const },
  scriptHeader: { display: "flex", justifyContent: "space-between", alignItems: "center", cursor: "pointer" },
  codeBlock: {
    backgroundColor: "#0a0a12",
    color: "#e0e0e8",
    padding: 12,
    borderRadius: 8,
    fontSize: "0.75rem",
    overflow: "auto",
    maxHeight: 400,
    margin: 0,
    border: "1px solid #1e1e2e",
  },
  acceptButton: {
    backgroundColor: "#2a4a2a",
    color: "#6cffa0",
    border: "1px solid #3a5a3a",
    borderRadius: 6,
    padding: "6px 12px",
    fontSize: "0.8rem",
    cursor: "pointer",
  },
  warningButton: {
    backgroundColor: "#4a4a2a",
    color: "#ffcc6c",
    border: "1px solid #5a5a3a",
    borderRadius: 6,
    padding: "6px 12px",
    fontSize: "0.8rem",
    cursor: "pointer",
  },
  rejectButton: {
    backgroundColor: "#2a2a3e",
    color: "#8888a0",
    border: "1px solid #3a3a4e",
    borderRadius: 6,
    padding: "6px 12px",
    fontSize: "0.8rem",
    cursor: "pointer",
  },
  secondaryButton: {
    backgroundColor: "#2a2a3e",
    color: "#e0e0e8",
    border: "1px solid #3a3a4e",
    borderRadius: 8,
    padding: "8px 16px",
    fontSize: "0.85rem",
    cursor: "pointer",
  },
};
