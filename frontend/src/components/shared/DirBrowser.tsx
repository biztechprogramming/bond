import React, { useEffect, useState, useCallback, useRef } from "react";
import { BACKEND_API, apiFetch } from "@/lib/config";

interface DirBrowserProps {
  hostId: string;
  onSelect: (path: string) => void;
  onClose: () => void;
}

export default function DirBrowser({ hostId, onSelect, onClose }: DirBrowserProps) {
  const [currentPath, setCurrentPath] = useState("");
  const [dirs, setDirs] = useState<{ name: string; path: string }[]>([]);
  const [parentPath, setParentPath] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [showHidden, setShowHidden] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [homePath, setHomePath] = useState<string | null>(null);

  const showHiddenRef = useRef(showHidden);
  showHiddenRef.current = showHidden;

  const browse = useCallback(async (path: string, hidden?: boolean) => {
    const h = hidden ?? showHiddenRef.current;
    setLoading(true);
    try {
      const res = await apiFetch(
        `${BACKEND_API}/agents/browse-dirs?host_id=${encodeURIComponent(hostId)}&path=${encodeURIComponent(path)}&show_hidden=${h}`
      );
      if (res.ok) {
        const data = await res.json();
        setCurrentPath(data.current || "");
        setParentPath(data.parent || null);
        setDirs(data.directories || []);
        setHomePath(data.home || null);
        setError(data.error || null);
      } else {
        const data = await res.json().catch(() => ({}));
        setDirs([]);
        setParentPath(null);
        setError(data.detail || "Directory browsing is unavailable.");
      }
    } catch {
      setDirs([]);
      setParentPath(null);
      setError("Directory browsing is unavailable.");
    }
    setLoading(false);
  }, []);

  useEffect(() => {
    browse("", false);
  }, []);

  return (
    <div style={modalStyles.overlay} onClick={onClose}>
      <div style={modalStyles.modal} onClick={(e) => e.stopPropagation()}>
        <div style={modalStyles.header}>
          <span style={modalStyles.title}>Select Directory</span>
          <button style={modalStyles.close} onClick={onClose}>✕</button>
        </div>
        <div style={modalStyles.pathBar}>
          {homePath && (
            <button style={{ ...modalStyles.selectBtn, backgroundColor: "#2a2a3e", color: "#cfd3ff" }} onClick={() => browse(homePath)}>Home</button>
          )}
          <button style={{ ...modalStyles.selectBtn, backgroundColor: "#2a2a3e", color: "#cfd3ff" }} onClick={() => browse("/")}>Root</button>
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
        </div>
        <div style={modalStyles.dirList}>
          {loading && <div style={{ color: "#8888a0", padding: "12px" }}>Loading...</div>}
          {!loading && error && <div style={{ color: "#ff8a8a", padding: "12px", fontSize: "0.9rem", lineHeight: 1.5 }}>{error}</div>}
          {dirs.map((d) => (
            <div
              key={d.path}
              style={modalStyles.dirItem}
              onClick={() => browse(d.path)}
            >
              📁 {d.name}
            </div>
          ))}
          {!loading && !error && dirs.length === 0 && (
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
    borderWidth: "1px", borderStyle: "solid", borderColor: "#1e1e2e",
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
    borderBottomWidth: "1px", borderBottomStyle: "solid", borderBottomColor: "#1e1e2e",
  },
  title: { fontSize: "1rem", fontWeight: 600, color: "#e0e0e8" },
  close: {
    background: "none",
    borderWidth: 0, borderStyle: "none", borderColor: "transparent",
    color: "#8888a0",
    fontSize: "1.2rem",
    cursor: "pointer",
  },
  pathBar: {
    display: "flex",
    alignItems: "center",
    gap: "12px",
    padding: "12px 20px",
    borderBottomWidth: "1px", borderBottomStyle: "solid", borderBottomColor: "#1e1e2e",
  },
  selectBtn: {
    backgroundColor: "#6c8aff",
    color: "#fff",
    borderWidth: 0, borderStyle: "none", borderColor: "transparent",
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
    borderBottomWidth: "1px", borderBottomStyle: "solid", borderBottomColor: "#1a1a2a",
  },
};
