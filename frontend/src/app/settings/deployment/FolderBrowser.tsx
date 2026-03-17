import React, { useState, useEffect, useCallback } from "react";
import { GATEWAY_API } from "@/lib/config";

interface FolderBrowserProps {
  onSelect: (path: string) => void;
  onCancel: () => void;
}

interface WorkspaceRoot {
  path: string;
  name: string;
}

interface FolderEntry {
  name: string;
  path: string;
  hasChildren: boolean;
}

interface FolderNode {
  name: string;
  path: string;
  hasChildren: boolean;
  children: FolderNode[] | null; // null = not loaded
  expanded: boolean;
  loading: boolean;
}

export default function FolderBrowser({ onSelect, onCancel }: FolderBrowserProps) {
  const [roots, setRoots] = useState<FolderNode[]>([]);
  const [loading, setLoading] = useState(true);
  const [selectedPath, setSelectedPath] = useState<string | null>(null);

  useEffect(() => {
    fetch(`${GATEWAY_API}/deployments/browse/workspaces`)
      .then((r) => (r.ok ? r.json() : []))
      .then((data: WorkspaceRoot[]) => {
        setRoots(
          (Array.isArray(data) ? data : []).map((w) => ({
            name: w.name,
            path: w.path,
            hasChildren: true,
            children: null,
            expanded: false,
            loading: false,
          }))
        );
      })
      .catch(() => {})
      .finally(() => setLoading(false));
  }, []);

  const loadChildren = useCallback(async (nodePath: string) => {
    const res = await fetch(
      `${GATEWAY_API}/deployments/browse/folders?path=${encodeURIComponent(nodePath)}`
    );
    if (!res.ok) return [];
    const data = await res.json();
    return (data.folders || []).map((f: FolderEntry) => ({
      name: f.name,
      path: f.path,
      hasChildren: f.hasChildren,
      children: null,
      expanded: false,
      loading: false,
    }));
  }, []);

  const updateNode = useCallback(
    (nodes: FolderNode[], targetPath: string, updater: (n: FolderNode) => FolderNode): FolderNode[] =>
      nodes.map((n) => {
        if (n.path === targetPath) return updater(n);
        if (n.children) return { ...n, children: updateNode(n.children, targetPath, updater) };
        return n;
      }),
    []
  );

  const toggleExpand = useCallback(
    async (nodePath: string) => {
      // If already expanded, collapse
      setRoots((prev) => {
        const node = findNode(prev, nodePath);
        if (node?.expanded) {
          return updateNode(prev, nodePath, (n) => ({ ...n, expanded: false }));
        }
        if (node?.children) {
          return updateNode(prev, nodePath, (n) => ({ ...n, expanded: true }));
        }
        // Need to load
        return updateNode(prev, nodePath, (n) => ({ ...n, loading: true }));
      });

      // Check if we need to load
      const node = findNode(roots, nodePath);
      if (node?.children && !node.expanded) return; // just toggled
      if (node?.expanded) return; // just collapsed

      const children = await loadChildren(nodePath);
      setRoots((prev) =>
        updateNode(prev, nodePath, (n) => ({
          ...n,
          children,
          expanded: true,
          loading: false,
        }))
      );
    },
    [roots, loadChildren, updateNode]
  );

  const findNode = (nodes: FolderNode[], targetPath: string): FolderNode | null => {
    for (const n of nodes) {
      if (n.path === targetPath) return n;
      if (n.children) {
        const found = findNode(n.children, targetPath);
        if (found) return found;
      }
    }
    return null;
  };

  const renderNode = (node: FolderNode, depth: number) => {
    const isSelected = selectedPath === node.path;
    return (
      <div key={node.path}>
        <div
          style={{
            display: "flex",
            alignItems: "center",
            padding: "5px 8px",
            paddingLeft: 8 + depth * 18,
            cursor: "pointer",
            backgroundColor: isSelected ? "#1e2a4a" : "transparent",
            borderRadius: 4,
          }}
          onClick={() => setSelectedPath(node.path)}
          onDoubleClick={() => node.hasChildren && toggleExpand(node.path)}
        >
          {node.hasChildren ? (
            <span
              style={{ width: 18, textAlign: "center", cursor: "pointer", userSelect: "none" }}
              onClick={(e) => {
                e.stopPropagation();
                toggleExpand(node.path);
              }}
            >
              {node.loading ? (
                <span style={{ fontSize: "0.7rem", color: "#5a5a6e" }}>...</span>
              ) : node.expanded ? (
                "📂"
              ) : (
                "📁"
              )}
            </span>
          ) : (
            <span style={{ width: 18, textAlign: "center" }}>📁</span>
          )}
          <span
            style={{
              marginLeft: 6,
              fontSize: "0.82rem",
              color: isSelected ? "#6c8aff" : "#d0d0d8",
            }}
          >
            {node.name}
          </span>
        </div>
        {node.expanded &&
          node.children?.map((child) => renderNode(child, depth + 1))}
      </div>
    );
  };

  return (
    <div style={styles.overlay}>
      <div style={styles.modal}>
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 12 }}>
          <h3 style={{ margin: 0, fontSize: "1rem", color: "#6c8aff" }}>Browse Workspace Folders</h3>
          <button style={styles.closeBtn} onClick={onCancel}>×</button>
        </div>

        <div style={styles.treeContainer}>
          {loading ? (
            <div style={{ color: "#5a5a6e", fontSize: "0.85rem", padding: 16, textAlign: "center" }}>
              Loading workspaces...
            </div>
          ) : roots.length === 0 ? (
            <div style={{ color: "#5a5a6e", fontSize: "0.85rem", padding: 16, textAlign: "center" }}>
              No workspace mounts found
            </div>
          ) : (
            roots.map((r) => renderNode(r, 0))
          )}
        </div>

        {selectedPath && (
          <div style={{ fontSize: "0.75rem", color: "#5a5a6e", marginTop: 8, wordBreak: "break-all" }}>
            {selectedPath}
          </div>
        )}

        <div style={{ display: "flex", gap: 8, justifyContent: "flex-end", marginTop: 12 }}>
          <button style={styles.cancelBtn} onClick={onCancel}>Cancel</button>
          <button
            style={{ ...styles.selectBtn, opacity: selectedPath ? 1 : 0.4 }}
            disabled={!selectedPath}
            onClick={() => selectedPath && onSelect(selectedPath)}
          >
            Select
          </button>
        </div>
      </div>
    </div>
  );
}

const styles: Record<string, React.CSSProperties> = {
  overlay: {
    position: "fixed",
    top: 0,
    left: 0,
    right: 0,
    bottom: 0,
    backgroundColor: "rgba(0,0,0,0.6)",
    display: "flex",
    alignItems: "center",
    justifyContent: "center",
    zIndex: 1000,
  },
  modal: {
    backgroundColor: "#12121a",
    border: "1px solid #2a2a3e",
    borderRadius: 12,
    padding: 20,
    width: 480,
    maxHeight: "70vh",
    display: "flex",
    flexDirection: "column",
  },
  treeContainer: {
    backgroundColor: "#0a0a12",
    border: "1px solid #1e1e2e",
    borderRadius: 8,
    padding: 8,
    flex: 1,
    overflowY: "auto",
    minHeight: 200,
    maxHeight: 400,
  },
  closeBtn: {
    background: "none",
    border: "none",
    color: "#8888a0",
    fontSize: "1.2rem",
    cursor: "pointer",
    padding: "2px 6px",
  },
  cancelBtn: {
    backgroundColor: "#2a2a3e",
    color: "#e0e0e8",
    border: "1px solid #3a3a4e",
    borderRadius: 8,
    padding: "8px 16px",
    fontSize: "0.85rem",
    cursor: "pointer",
  },
  selectBtn: {
    backgroundColor: "#6c8aff",
    color: "#fff",
    border: "none",
    borderRadius: 8,
    padding: "8px 20px",
    fontSize: "0.85rem",
    cursor: "pointer",
    fontWeight: 600,
  },
};
