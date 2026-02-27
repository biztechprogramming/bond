import React from "react";
import type { WorkItem } from "@/lib/types";
import { ITEM_STATUS_COLORS } from "@/lib/theme";
import ItemDetail from "./ItemDetail";

interface WorkItemCardProps {
  item: WorkItem;
  expanded: boolean;
  onToggleExpand: () => void;
  onDragStart: () => void;
  onDragEnd: () => void;
  isDragging: boolean;
}

function timeInStatus(item: WorkItem): string {
  const start = item.started_at || item.created_at;
  if (!start) return "";
  const ms = Date.now() - new Date(start).getTime();
  if (ms < 60000) return "<1m";
  if (ms < 3600000) return `${Math.floor(ms / 60000)}m`;
  if (ms < 86400000) return `${Math.floor(ms / 3600000)}h`;
  return `${Math.floor(ms / 86400000)}d`;
}

export default function WorkItemCard({ item, expanded, onToggleExpand, onDragStart, onDragEnd, isDragging }: WorkItemCardProps) {
  const time = timeInStatus(item);

  return (
    <div
      draggable
      onDragStart={onDragStart}
      onDragEnd={onDragEnd}
      style={{
        ...styles.card,
        borderLeftColor: ITEM_STATUS_COLORS[item.status] || "#5a5a6e",
        opacity: isDragging ? 0.5 : 1,
        cursor: "grab",
      }}
      onClick={onToggleExpand}
    >
      <div style={styles.cardTitle}>{item.title}</div>
      <div style={styles.cardMeta}>
        {time && <span>{time}</span>}
        {item.notes.length > 0 && (
          <span>{item.notes.length} note{item.notes.length !== 1 ? "s" : ""}</span>
        )}
        {item.files_changed.length > 0 && (
          <span>{item.files_changed.length} file{item.files_changed.length !== 1 ? "s" : ""}</span>
        )}
      </div>
      {expanded && <ItemDetail item={item} />}
    </div>
  );
}

const styles: Record<string, React.CSSProperties> = {
  card: {
    backgroundColor: "#12121a",
    borderRadius: "8px",
    padding: "10px 12px",
    borderLeft: "3px solid #5a5a6e",
    cursor: "pointer",
    transition: "background-color 0.15s",
  },
  cardTitle: {
    fontSize: "0.85rem",
    color: "#e0e0e8",
    lineHeight: 1.3,
  },
  cardMeta: {
    display: "flex",
    gap: "8px",
    fontSize: "0.7rem",
    color: "#5a5a6e",
    marginTop: "6px",
  },
};
