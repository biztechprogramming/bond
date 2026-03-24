import React from "react";
import type { WorkItem } from "@/lib/types";
import WorkItemCard from "./WorkItemCard";

interface KanbanColumnProps {
  columnKey: string;
  label: string;
  items: WorkItem[];
  expandedItemId: string | null;
  dragItemId: string | null;
  isDropTarget: boolean;
  onToggleExpand: (itemId: string) => void;
  onDragStart: (itemId: string) => void;
  onDragEnd: () => void;
  onDragOver: (e: React.DragEvent) => void;
  onDragLeave: () => void;
  onDrop: () => void;
}

export default function KanbanColumn({
  columnKey,
  label,
  items,
  expandedItemId,
  dragItemId,
  isDropTarget,
  onToggleExpand,
  onDragStart,
  onDragEnd,
  onDragOver,
  onDragLeave,
  onDrop,
}: KanbanColumnProps) {
  return (
    <div
      className="board-column"
      style={{
        ...styles.column,
        ...(isDropTarget ? { backgroundColor: "#12121f", outline: "2px dashed #6c8aff" } : {}),
      }}
      onDragOver={onDragOver}
      onDragLeave={onDragLeave}
      onDrop={onDrop}
    >
      <div style={styles.columnHeader}>
        <span>{label}</span>
        <span style={styles.columnCount}>{items.length}</span>
      </div>
      <div style={styles.columnBody}>
        {items.map(item => (
          <WorkItemCard
            key={item.id}
            item={item}
            expanded={expandedItemId === item.id}
            onToggleExpand={() => onToggleExpand(item.id)}
            onDragStart={() => onDragStart(item.id)}
            onDragEnd={onDragEnd}
            isDragging={dragItemId === item.id}
          />
        ))}
        {items.length === 0 && (
          <div style={styles.columnEmpty}>No items</div>
        )}
      </div>
    </div>
  );
}

const styles: Record<string, React.CSSProperties> = {
  column: {
    flex: 1,
    minWidth: "180px",
    maxWidth: "280px",
    display: "flex",
    flexDirection: "column" as const,
    backgroundColor: "#0a0a14",
    borderRadius: "10px",
    overflow: "hidden",
  },
  columnHeader: {
    display: "flex",
    justifyContent: "space-between",
    alignItems: "center",
    padding: "10px 14px",
    fontSize: "0.8rem",
    fontWeight: 600,
    color: "#8888a0",
    textTransform: "uppercase" as const,
    letterSpacing: "0.05em",
    borderBottomWidth: "1px", borderBottomStyle: "solid", borderBottomColor: "#1e1e2e",
  },
  columnCount: {
    backgroundColor: "#1e1e2e",
    borderRadius: "10px",
    padding: "2px 8px",
    fontSize: "0.75rem",
    color: "#5a5a6e",
  },
  columnBody: {
    flex: 1,
    overflowY: "auto" as const,
    padding: "8px",
    display: "flex",
    flexDirection: "column" as const,
    gap: "8px",
  },
  columnEmpty: {
    textAlign: "center" as const,
    color: "#3a3a4e",
    fontSize: "0.8rem",
    padding: "16px 8px",
  },
};
