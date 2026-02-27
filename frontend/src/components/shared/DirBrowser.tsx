import React, { useEffect, useState, useCallback, useRef } from "react";

interface DirBrowserProps {
  onSelect: (path: string) => void;
  onClose: () => void;
}

export default function DirBrowser({ onSelect, onClose }: DirBrowserProps) {
  const [currentPath, setCurrentPath] = useState("/home");
  const [dirs, setDirs] = useState<{ name: string; path: string }[]>([]);
  const [parentPath, setParentPath] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [showHidden, setShowHidden] = useState(false);

  const showHiddenRef = useRef(showHidden);
  showHiddenRef.current = showHidden;

  const browse = useCallback(async (path: string, hidden?: boolean) => {
    const h = hidden ?? showHiddenRef.current;
    setLoading(true);
    try {
      const res = await fetch(
        `http://localhost:18790/api/v1/agents/browse-dirs?path=${encodeURIComponent(path)}&show_hidden=${h}`
      );
      if (res.ok) {
        const data = await res.json();
        setCurrentPath(data.current);
        setParentPath(data.parent);
        setDirs(data.directories);
      }
    } catch {
      // ignore
    }
    setLoading(false);
  }, []);

  useEffect(() => {
    browse(currentPath, false);
  }, []);

  return (
    <div style={modalStyles.overlay} onClick={onClose}>
      <div style={modalStyles.modal} onClick={(e) => e.stopPropagation()}>
        <div style={modalStyles.header}>
          <span style={modalStyles.title}>Select Directory</span>
          <button style={modalStyles.close} onClick={onClose}>✕</button>
        </div>
        <div style={modalStyles.pathBar}>
          <span style={{ color: "#6c8aff", fontSize: "0.85rem", wordBreak: "break-all", flex: 1 }}>
            {currentPath}
          </span>
          <label style={{ display: "flex", alignItems: "center", gap: "4px", color: "#8888a0", fontSize: "0.8rem", flexShrink: 0, cursor: "pointer" }}>
            <input
              type="checkbox"
              checked={showHidden}
              onChange={(e) => {
                setShowHidden(e.target.checked);
                browse(currentPath, e.target.checked);
              }}
              style={{ accentColor: "#6c8aff" }}
            />
            Hidden
          </label>
          <button
            style={{ ...modalStyles.selectBtn, flexShrink: 0 }}
            onClick={() => onSelect(currentPath)}
          >
            Select This
          </button>
        </div>
        <div style={modalStyles.dirList}>
          {parentPath && (
            <div style={modalStyles.dirItem} onClick={() => browse(parentPath)}>
              📁 ..
            </div>
          )}
          {loading && <div style={{ color: "#8888a0", padding: "12px" }}>Loading...</div>}
          {dirs.map((d) => (
            <div
              key={d.path}
              style={modalStyles.dirItem}
              onClick={() => browse(d.path)}
            >
              📁 {d.name}
            </div>
          ))}
          {!loading && dirs.length === 0 && (
            <div style={{ color: "#8888a0", padding: "12px", fontSize: "0.85rem" }}>
              No subdirectories
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

const modalStyles: Record<string, React.CSSProperties> = {
  overlay: {
    position: "fixed",
    top: 0, left: 0, right: 0, bottom: 0,
    backgroundColor: "rgba(0,0,0,0.7)",
    display: "flex",
    alignItems: "center",
    justifyContent: "center",
    zIndex: 1000,
  },
  modal: {
    backgroundColor: "#12121a",
    border: "1px solid #1e1e2e",
    borderRadius: "12px",
    width: "500px",
    height: "70vh",
    display: "flex",
    flexDirection: "column",
    overflow: "hidden",
  },
  header: {
    display: "flex",
    justifyContent: "space-between",
    alignItems: "center",
    padding: "16px 20px",
    borderBottom: "1px solid #1e1e2e",
  },
  title: { fontSize: "1rem", fontWeight: 600, color: "#e0e0e8" },
  close: {
    background: "none",
    border: "none",
    color: "#8888a0",
    fontSize: "1.2rem",
    cursor: "pointer",
  },
  pathBar: {
    display: "flex",
    alignItems: "center",
    gap: "12px",
    padding: "12px 20px",
    borderBottom: "1px solid #1e1e2e",
  },
  selectBtn: {
    backgroundColor: "#6c8aff",
    color: "#fff",
    border: "none",
    borderRadius: "6px",
    padding: "6px 14px",
    fontSize: "0.8rem",
    fontWeight: 600,
    cursor: "pointer",
  },
  dirList: {
    overflowY: "scroll" as const,
    flex: 1,
    minHeight: 0,
    maxHeight: "400px",
    WebkitOverflowScrolling: "touch",
  },
  dirItem: {
    padding: "10px 20px",
    cursor: "pointer",
    fontSize: "0.9rem",
    color: "#e0e0e8",
    borderBottom: "1px solid #1a1a2a",
  },
};
