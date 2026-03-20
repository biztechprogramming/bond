/**
 * Permission Broker — shared type definitions.
 */

export interface AgentTokenPayload {
  sub: string;   // agent ID (ULID)
  sid: string;   // session/conversation ID
  iat: number;   // issued at (unix seconds)
  exp: number;   // expires at (unix seconds)
}

export interface PolicyRule {
  commands: string[];
  decision: "allow" | "deny" | "prompt";
  reason?: string;
  timeout?: number;
  cwd?: string[];
}

export interface Policy {
  version: string;
  name: string;
  extends?: string;
  agent_id?: string;
  rules: PolicyRule[];
}

export interface PolicyDecision {
  decision: "allow" | "deny" | "prompt";
  reason?: string;
  timeout?: number;
  source: string;
}

export interface AuditEntry {
  timestamp: string;
  agent_id: string;
  session_id: string;
  command: string;
  cwd?: string;
  decision: string;
  policy_rule: string;
  exit_code?: number;
  stdout_len?: number;
  stderr_len?: number;
  duration_ms?: number;
  error?: string;
}

export interface ExecResult {
  exit_code: number;
  stdout: string;
  stderr: string;
  duration_ms: number;
}

export interface BrokerConfig {
  dataDir: string;
  policyDir: string;
}

export interface MCPPolicyRule {
  name?: string;
  tools: string[];          // glob patterns for tool names (e.g. "mcp_*", "mcp_github_*")
  agent_ids?: string[];     // glob patterns for agent IDs (empty = all agents)
  decision: "allow" | "deny";
  reason?: string;
}
