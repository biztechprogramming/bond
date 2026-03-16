/**
 * Log Collector — remote log collection via SSH.
 *
 * Design Doc 044 §7.3 — Log Monitoring
 */

import { getResource } from "./resources.js";
import { loadSecrets } from "./secrets.js";
import { executeSshScript } from "./discovery-scripts.js";
import type { GatewayConfig } from "../config/index.js";

// ── Types ───────────────────────────────────────────────────────────────────

export interface LogSource {
  source: string;
  service?: string;
  container?: string;
  error_count: number;
  warning_count?: number;
  error_messages?: string[];
  error_lines?: string[];
}

export interface LogCheckResult {
  status: "ok" | "error";
  action: string;
  environment?: string;
  reason?: string;
  info?: { log_sources: LogSource[] };
}

// ── Log Collection ──────────────────────────────────────────────────────────

const LOG_COLLECTION_SCRIPT = `#!/usr/bin/env bash
set -euo pipefail

SINCE="\${SINCE_MINUTES:-5}"
result='{"log_sources":[]}'

# journalctl for systemd services
for service in \${MONITORED_SERVICES:-}; do
  LOGS=$(journalctl -u "$service" --since "$SINCE minutes ago" --no-pager -o json 2>/dev/null | tail -100 || echo "")
  ERRORS=$(echo "$LOGS" | grep -c '"PRIORITY":"3"' 2>/dev/null || echo "0")
  WARNINGS=$(echo "$LOGS" | grep -c '"PRIORITY":"4"' 2>/dev/null || echo "0")

  ERROR_MSGS=$(echo "$LOGS" | python3 -c "
import sys, json
errors = []
for line in sys.stdin:
    try:
        entry = json.loads(line)
        if entry.get('PRIORITY') in ('3', '4'):
            msg = entry.get('MESSAGE', '')
            if msg and msg not in errors:
                errors.append(msg)
    except: pass
print(json.dumps(errors[:20]))
" 2>/dev/null || echo "[]")

  result=$(echo "$result" | python3 -c "
import sys, json
r = json.load(sys.stdin)
r['log_sources'].append({
    'source': 'journalctl',
    'service': '$service',
    'error_count': $ERRORS,
    'warning_count': $WARNINGS,
    'error_messages': $ERROR_MSGS
})
json.dump(r, sys.stdout)
")
done

# Docker container logs
for container in $(docker ps --format '{{.Names}}' 2>/dev/null); do
  LOGS=$(docker logs --since "\${SINCE}m" "$container" 2>&1 | tail -100 || echo "")
  ERRORS=$(echo "$LOGS" | grep -ciE "(error|exception|fatal|panic)" || echo "0")

  ERROR_LINES=$(echo "$LOGS" | grep -iE "(error|exception|fatal|panic)" | head -10 | python3 -c "import sys,json; print(json.dumps([l.strip() for l in sys.stdin]))" 2>/dev/null || echo "[]")

  result=$(echo "$result" | python3 -c "
import sys, json
r = json.load(sys.stdin)
r['log_sources'].append({
    'source': 'docker',
    'container': '$container',
    'error_count': $ERRORS,
    'error_lines': $ERROR_LINES
})
json.dump(r, sys.stdout)
")
done

# Nginx error log
if [[ -f /var/log/nginx/error.log ]]; then
  ERRORS=$(tail -200 /var/log/nginx/error.log | grep -c "error" 2>/dev/null || echo "0")
  ERROR_LINES=$(tail -200 /var/log/nginx/error.log | grep "error" | tail -10 | python3 -c "import sys,json; print(json.dumps([l.strip() for l in sys.stdin]))" 2>/dev/null || echo "[]")

  result=$(echo "$result" | python3 -c "
import sys, json
r = json.load(sys.stdin)
r['log_sources'].append({
    'source': 'nginx-error',
    'error_count': $ERRORS,
    'error_lines': $ERROR_LINES
})
json.dump(r, sys.stdout)
")
fi

echo "$result"
`;

/**
 * Collect logs from a remote resource via SSH.
 */
export async function collectLogs(
  cfg: GatewayConfig,
  resourceId: string,
  env: string,
  sinceMinutes = 5,
  logSources?: string[],
): Promise<LogCheckResult> {
  const resource = await getResource(cfg, resourceId);
  if (!resource) {
    return { status: "error", action: "log-check", reason: "Resource not found" };
  }

  let conn: any;
  try {
    conn = JSON.parse(resource.connection_json);
  } catch {
    return { status: "error", action: "log-check", reason: "Invalid connection JSON" };
  }

  if (!conn.host) {
    return { status: "error", action: "log-check", reason: "Resource requires SSH connection with host" };
  }

  const secrets = loadSecrets(env);
  const envVars: Record<string, string> = {
    ...secrets,
    BOND_DEPLOY_ENV: env,
    SINCE_MINUTES: String(sinceMinutes),
  };
  if (logSources && logSources.length > 0) {
    envVars.MONITORED_SERVICES = logSources.join(" ");
  }

  const result = await executeSshScript(
    conn.host,
    conn.port || 22,
    conn.user || "deploy",
    LOG_COLLECTION_SCRIPT,
    envVars,
    conn.key_path,
    30,
  );

  if (result.exit_code === 0) {
    try {
      const parsed = JSON.parse(result.stdout);
      return {
        status: "ok",
        action: "log-check",
        environment: env,
        info: parsed,
      };
    } catch {
      return {
        status: "ok",
        action: "log-check",
        environment: env,
        info: { log_sources: [] },
      };
    }
  }

  return {
    status: "error",
    action: "log-check",
    environment: env,
    reason: result.stderr || "Log collection failed",
  };
}
