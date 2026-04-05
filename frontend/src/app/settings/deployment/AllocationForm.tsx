import React, { useState, useEffect, useCallback, useRef } from "react";
import { GATEWAY_API , apiFetch } from "@/lib/config";

interface ServicePort {
  service_name: string;
  port: number;
  protocol: string;
  health_endpoint: string;
  description: string;
  confidence: "detected" | "inferred" | "user";
}

interface OtherAllocation {
  app_name: string;
  environment_name: string;
  base_port: number;
  services: Array<{ service_name: string; port: number }>;
}

interface Conflict {
  type: "port" | "directory";
  field: string;
  value: string | number;
  conflicting_app: string;
  conflicting_env: string;
  detail: string;
}

interface Warning {
  type: string;
  field: string;
  message: string;
}

interface AllocationFormProps {
  resourceId: string;
  resourceName?: string;
  appName: string;
  environmentName: string;
  initialData?: {
    base_port?: number;
    app_dir?: string;
    data_dir?: string;
    log_dir?: string;
    config_dir?: string;
    tls_cert_path?: string;
    tls_key_path?: string;
    services?: ServicePort[];
  };
  onSave?: (data: any) => void;
  onCancel?: () => void;
  readOnly?: boolean;
}

const CONFIDENCE_BADGES: Record<string, string> = {
  detected: "✓",
  inferred: "~",
  user: "👤",
};

export default function AllocationForm({
  resourceId,
  resourceName,
  appName,
  environmentName,
  initialData,
  onSave,
  onCancel,
  readOnly = false,
}: AllocationFormProps) {
  const [basePort, setBasePort] = useState(initialData?.base_port ?? 3000);
  const [appDir, setAppDir] = useState(initialData?.app_dir ?? `/opt/${appName}/${environmentName}`);
  const [dataDir, setDataDir] = useState(initialData?.data_dir ?? `/var/data/${appName}/${environmentName}`);
  const [logDir, setLogDir] = useState(initialData?.log_dir ?? `/var/log/${appName}/${environmentName}`);
  const [configDir, setConfigDir] = useState(initialData?.config_dir ?? `/etc/${appName}/${environmentName}`);
  const [tlsCertPath, setTlsCertPath] = useState(initialData?.tls_cert_path ?? "");
  const [tlsKeyPath, setTlsKeyPath] = useState(initialData?.tls_key_path ?? "");
  const [services, setServices] = useState<ServicePort[]>(initialData?.services ?? []);
  const [conflicts, setConflicts] = useState<Conflict[]>([]);
  const [warnings, setWarnings] = useState<Warning[]>([]);
  const [suggestions, setSuggestions] = useState<Record<string, number | string>>({});
  const [otherAllocations, setOtherAllocations] = useState<OtherAllocation[]>([]);
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [msg, setMsg] = useState("");
  const debounceRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  // Load other allocations and suggestions on mount
  useEffect(() => {
    loadOtherAllocations();
    if (!initialData?.base_port) {
      loadSuggestions();
    }
  }, [resourceId, appName, environmentName]);

  const loadOtherAllocations = async () => {
    try {
      const res = await apiFetch(`${GATEWAY_API}/deployments/allocations?resource_id=${encodeURIComponent(resourceId)}`);
      if (res.ok) {
        const allocs = await res.json();
        const others: OtherAllocation[] = [];
        for (const a of allocs) {
          if (a.app_name === appName && a.environment_name === environmentName) continue;
          const portsRes = await apiFetch(`${GATEWAY_API}/deployments/allocations/${a.id}/ports`);
          const ports = portsRes.ok ? await portsRes.json() : [];
          others.push({
            app_name: a.app_name,
            environment_name: a.environment_name,
            base_port: a.base_port,
            services: ports.map((p: any) => ({ service_name: p.service_name, port: p.port })),
          });
        }
        setOtherAllocations(others);
      }
    } catch { /* ignore */ }
  };

  const loadSuggestions = async () => {
    try {
      const res = await apiFetch(`${GATEWAY_API}/deployments/allocations/suggest`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          resource_id: resourceId,
          app_name: appName,
          environment_name: environmentName,
          services: services.map(s => s.service_name),
        }),
      });
      if (res.ok) {
        const data = await res.json();
        setBasePort(data.base_port);
        setAppDir(data.app_dir);
        setDataDir(data.data_dir);
        setLogDir(data.log_dir);
        setConfigDir(data.config_dir);
        if (data.service_ports && services.length === 0) {
          setServices(data.service_ports.map((sp: any) => ({
            ...sp,
            health_endpoint: "",
            description: "",
            confidence: "inferred" as const,
          })));
        }
      }
    } catch { /* ignore */ }
  };

  // Debounced conflict check
  const checkConflicts = useCallback(() => {
    if (debounceRef.current) clearTimeout(debounceRef.current);
    debounceRef.current = setTimeout(async () => {
      try {
        const res = await apiFetch(`${GATEWAY_API}/deployments/allocations/check-conflicts`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            resource_id: resourceId,
            app_name: appName,
            environment_name: environmentName,
            ports: services.map(s => ({ service_name: s.service_name, port: s.port, protocol: s.protocol })),
            directories: { app_dir: appDir, data_dir: dataDir, log_dir: logDir, config_dir: configDir },
          }),
        });
        if (res.ok) {
          const data = await res.json();
          setConflicts(data.conflicts || []);
          setWarnings(data.warnings || []);
          setSuggestions(data.suggestions || {});
        }
      } catch { /* ignore */ }
    }, 300);
  }, [resourceId, appName, environmentName, services, appDir, dataDir, logDir, configDir]);

  useEffect(() => { checkConflicts(); }, [checkConflicts]);

  const addService = () => {
    setServices(prev => [...prev, {
      service_name: "",
      port: basePort + prev.length,
      protocol: "tcp",
      health_endpoint: "",
      description: "",
      confidence: "user",
    }]);
  };

  const removeService = (idx: number) => {
    setServices(prev => prev.filter((_, i) => i !== idx));
  };

  const updateService = (idx: number, field: keyof ServicePort, value: any) => {
    setServices(prev => prev.map((s, i) => i === idx ? { ...s, [field]: value, confidence: "user" as const } : s));
  };

  const handleSave = async () => {
    if (conflicts.length > 0) {
      setMsg("Cannot save: resolve all conflicts first");
      return;
    }
    setSaving(true);
    setMsg("");
    try {
      if (onSave) {
        onSave({
          resource_id: resourceId,
          app_name: appName,
          environment_name: environmentName,
          base_port: basePort,
          app_dir: appDir,
          data_dir: dataDir,
          log_dir: logDir,
          config_dir: configDir,
          tls_cert_path: tlsCertPath,
          tls_key_path: tlsKeyPath,
          services,
        });
      }
    } catch (err: any) {
      setMsg(`Error: ${err.message}`);
    } finally {
      setSaving(false);
    }
  };

  const hasConflictFor = (field: string) => conflicts.some(c => c.field === field);
  const getConflictDetail = (field: string) => conflicts.find(c => c.field === field)?.detail;

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: "16px" }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
        <h3 style={{ margin: 0, color: "#6c8aff", fontSize: "1rem", fontWeight: 600 }}>
          Port &amp; Directory Allocation — {resourceName || resourceId}
        </h3>
        <span style={{ fontSize: "0.85rem", color: "#8888a0" }}>
          {appName} / {environmentName}
        </span>
      </div>

      {/* Base Port */}
      <div style={styles.fieldGroup}>
        <label style={styles.label}>Base Port</label>
        <input
          type="number"
          value={basePort}
          onChange={e => setBasePort(Number(e.target.value))}
          disabled={readOnly}
          style={styles.input}
        />
      </div>

      {/* Directories */}
      <div style={{ ...styles.section, display: "flex", flexDirection: "column", gap: "8px" }}>
        <div style={styles.sectionHeader}>Directories</div>
        {([
          ["Application", appDir, setAppDir, "app_dir"],
          ["Data", dataDir, setDataDir, "data_dir"],
          ["Logs", logDir, setLogDir, "log_dir"],
          ["Config", configDir, setConfigDir, "config_dir"],
        ] as const).map(([label, value, setter, field]) => (
          <div key={field} style={styles.fieldRow}>
            <label style={{ ...styles.label, width: "100px" }}>{label}:</label>
            <input
              value={value}
              onChange={e => (setter as any)(e.target.value)}
              disabled={readOnly}
              style={{ ...styles.input, flex: 1, borderColor: hasConflictFor(field) ? "#ff6c8a" : "#3a3a4e" }}
            />
            <span style={{ fontSize: "0.75rem", color: "#8888a0" }}>~</span>
            {hasConflictFor(field) && (
              <span style={{ fontSize: "0.75rem", color: "#ff6c8a" }} title={getConflictDetail(field)}>⚠</span>
            )}
          </div>
        ))}
      </div>

      {/* TLS */}
      <div style={{ ...styles.section, display: "flex", flexDirection: "column", gap: "8px" }}>
        <div style={styles.sectionHeader}>TLS/SSL (optional)</div>
        <div style={styles.fieldRow}>
          <label style={{ ...styles.label, width: "100px" }}>Certificate:</label>
          <input value={tlsCertPath} onChange={e => setTlsCertPath(e.target.value)} disabled={readOnly} style={{ ...styles.input, flex: 1 }} placeholder="/etc/letsencrypt/live/..." />
        </div>
        <div style={styles.fieldRow}>
          <label style={{ ...styles.label, width: "100px" }}>Private Key:</label>
          <input value={tlsKeyPath} onChange={e => setTlsKeyPath(e.target.value)} disabled={readOnly} style={{ ...styles.input, flex: 1 }} placeholder="...privkey.pem" />
        </div>
      </div>

      {/* Service Ports */}
      <div style={{ ...styles.section, display: "flex", flexDirection: "column", gap: "8px" }}>
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
          <div style={styles.sectionHeader}>Service Ports</div>
          {!readOnly && (
            <button style={styles.smallButton} onClick={addService}>+ Add Service</button>
          )}
        </div>
        {services.length === 0 && (
          <div style={{ fontSize: "0.8rem", color: "#666" }}>No services detected. Add services manually or re-run discovery.</div>
        )}
        {services.map((svc, idx) => (
          <div key={idx} style={{ display: "flex", gap: "8px", alignItems: "center" }}>
            <input
              value={svc.service_name}
              onChange={e => updateService(idx, "service_name", e.target.value)}
              placeholder="service"
              disabled={readOnly}
              style={{ ...styles.input, width: "120px" }}
            />
            <input
              type="number"
              value={svc.port}
              onChange={e => updateService(idx, "port", Number(e.target.value))}
              disabled={readOnly}
              style={{ ...styles.input, width: "80px", borderColor: hasConflictFor(svc.service_name) ? "#ff6c8a" : "#3a3a4e" }}
            />
            <select
              value={svc.protocol}
              onChange={e => updateService(idx, "protocol", e.target.value)}
              disabled={readOnly}
              style={{ ...styles.input, width: "70px" }}
            >
              <option value="tcp">TCP</option>
              <option value="udp">UDP</option>
            </select>
            <input
              value={svc.health_endpoint}
              onChange={e => updateService(idx, "health_endpoint", e.target.value)}
              placeholder="/health"
              disabled={readOnly}
              style={{ ...styles.input, flex: 1 }}
            />
            <span style={{ fontSize: "0.75rem" }}>{CONFIDENCE_BADGES[svc.confidence]}</span>
            {hasConflictFor(svc.service_name) && (
              <span style={{ fontSize: "0.75rem", color: "#ff6c8a" }} title={getConflictDetail(svc.service_name)}>⚠</span>
            )}
            {!readOnly && (
              <button style={{ ...styles.smallButton, color: "#ff6c8a" }} onClick={() => removeService(idx)}>×</button>
            )}
          </div>
        ))}
      </div>

      {/* Other Allocations */}
      {otherAllocations.length > 0 && (
        <div style={styles.section}>
          <div style={styles.sectionHeader}>Other Allocations on This Server</div>
          {otherAllocations.map((a, i) => (
            <div key={i} style={{ fontSize: "0.8rem", color: "#8888a0", padding: "4px 0" }}>
              {a.app_name}/{a.environment_name}: {a.services.map(s => `${s.service_name}=${s.port}`).join(", ") || `base=${a.base_port}`}
            </div>
          ))}
        </div>
      )}

      {/* Conflicts & Warnings */}
      {conflicts.length > 0 && (
        <div style={{ padding: "8px 12px", backgroundColor: "#3a1a1e", borderRadius: "6px", fontSize: "0.8rem", color: "#ff6c8a" }}>
          ⚠ {conflicts.length} conflict(s) detected
          {conflicts.map((c, i) => (
            <div key={i} style={{ paddingTop: "4px" }}>• {c.detail}</div>
          ))}
        </div>
      )}
      {warnings.length > 0 && conflicts.length === 0 && (
        <div style={{ padding: "8px 12px", backgroundColor: "#3a3a1e", borderRadius: "6px", fontSize: "0.8rem", color: "#ffa06c" }}>
          {warnings.map((w, i) => (
            <div key={i}>⚠ {w.message}</div>
          ))}
        </div>
      )}
      {conflicts.length === 0 && warnings.length === 0 && services.length > 0 && (
        <div style={{ fontSize: "0.8rem", color: "#6cffa0" }}>✓ No conflicts detected</div>
      )}

      {/* Actions */}
      {!readOnly && (
        <div style={{ display: "flex", gap: "8px", justifyContent: "flex-end" }}>
          {onCancel && <button style={styles.secondaryButton} onClick={onCancel}>Cancel</button>}
          <button
            style={{ ...styles.primaryButton, opacity: conflicts.length > 0 ? 0.5 : 1 }}
            disabled={conflicts.length > 0 || saving}
            onClick={handleSave}
          >
            {saving ? "Saving..." : "Save Allocation"}
          </button>
        </div>
      )}

      {msg && <div style={{ fontSize: "0.85rem", color: msg.startsWith("Error") ? "#ff6c8a" : "#6cffa0" }}>{msg}</div>}
    </div>
  );
}

const styles: Record<string, React.CSSProperties> = {
  fieldGroup: { display: "flex", alignItems: "center", gap: "8px" },
  fieldRow: { display: "flex", alignItems: "center", gap: "8px" },
  label: { fontSize: "0.85rem", color: "#c0c0d0", fontWeight: 500 },
  input: {
    backgroundColor: "#1a1a2e",
    color: "#e0e0e8",
    borderWidth: "1px", borderStyle: "solid", borderColor: "#3a3a4e",
    borderRadius: "6px",
    padding: "6px 10px",
    fontSize: "0.85rem",
  },
  section: {
    backgroundColor: "#1a1a2e",
    borderWidth: "1px", borderStyle: "solid", borderColor: "#2a2a3e",
    borderRadius: "8px",
    padding: "12px",
  },
  sectionHeader: { fontSize: "0.8rem", fontWeight: 600, color: "#8888a0", textTransform: "uppercase" as const, letterSpacing: "0.05em" },
  smallButton: {
    backgroundColor: "transparent",
    color: "#6c8aff",
    borderWidth: "1px", borderStyle: "solid", borderColor: "#3a3a4e",
    borderRadius: "4px",
    padding: "2px 8px",
    fontSize: "0.75rem",
    cursor: "pointer",
  },
  secondaryButton: {
    backgroundColor: "#2a2a3e",
    color: "#e0e0e8",
    borderWidth: "1px", borderStyle: "solid", borderColor: "#3a3a4e",
    borderRadius: "8px",
    padding: "8px 16px",
    fontSize: "0.85rem",
    cursor: "pointer",
  },
  primaryButton: {
    backgroundColor: "#6c8aff",
    color: "#fff",
    borderWidth: 0, borderStyle: "none", borderColor: "transparent",
    borderRadius: "8px",
    padding: "8px 20px",
    fontSize: "0.85rem",
    fontWeight: 600,
    cursor: "pointer",
  },
};
