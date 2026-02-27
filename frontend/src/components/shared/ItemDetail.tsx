import React from "react";
import type { WorkItem } from "@/lib/types";

interface ItemDetailProps {
  item: WorkItem;
}

export default function ItemDetail({ item }: ItemDetailProps) {
  return (
    <div style={styles.cardDetail}>
      {item.notes.length > 0 && (
        <div style={styles.detailSection}>
          <div style={styles.detailLabel}>Notes</div>
          {item.notes.map((note, i) => (
            <div key={i} style={styles.noteItem}>{note}</div>
          ))}
        </div>
      )}
      {item.files_changed.length > 0 && (
        <div style={styles.detailSection}>
          <div style={styles.detailLabel}>Files Changed</div>
          {item.files_changed.map((f, i) => (
            <div key={i} style={styles.fileItem}>{f}</div>
          ))}
        </div>
      )}
      {item.context_snapshot && (
        <div style={styles.detailSection}>
          <div style={styles.detailLabel}>Context Snapshot</div>
          <pre style={styles.snapshotPre}>
            {JSON.stringify(item.context_snapshot, null, 2)}
          </pre>
        </div>
      )}
    </div>
  );
}

const styles: Record<string, React.CSSProperties> = {
  cardDetail: {
    marginTop: "10px",
    paddingTop: "10px",
    borderTop: "1px solid #1e1e2e",
  },
  detailSection: {
    marginBottom: "8px",
  },
  detailLabel: {
    fontSize: "0.7rem",
    fontWeight: 600,
    color: "#6c8aff",
    marginBottom: "4px",
    textTransform: "uppercase" as const,
  },
  noteItem: {
    fontSize: "0.78rem",
    color: "#aaa",
    padding: "4px 0",
    borderBottom: "1px solid #1a1a2a",
    whiteSpace: "pre-wrap" as const,
  },
  fileItem: {
    fontSize: "0.78rem",
    color: "#6cffa0",
    fontFamily: "monospace",
    padding: "2px 0",
  },
  snapshotPre: {
    fontSize: "0.72rem",
    color: "#8888a0",
    backgroundColor: "#0a0a14",
    borderRadius: "6px",
    padding: "8px",
    overflow: "auto" as const,
    maxHeight: "200px",
    margin: 0,
  },
};
