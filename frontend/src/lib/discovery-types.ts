/**
 * Discovery Types — TypeScript interfaces matching the backend agent discovery system.
 *
 * Design Doc 072 §5 — Shared Types
 */

export interface FieldConfidence {
  source: "detected" | "inferred" | "user-provided";
  detail: string;
  score: number;
}

export interface CompletenessReport {
  ready: boolean;
  required_coverage: number;
  recommended_coverage: number;
  missing_required: string[];
  low_confidence: string[];
}

export interface ProbeRecord {
  tool: string;
  timestamp: number;
  duration_ms: number;
  success: boolean;
  fields_discovered: string[];
}

export interface DiscoveryState {
  findings: {
    source?: string;
    repo_url?: string;
    framework?: { framework: string; runtime?: string; confidence: number; evidence: string[] };
    build_strategy?: { strategy: string; confidence: number; evidence: string[] };
    services?: Array<{ name: string; type: string; source: string; confidence: number }>;
    env_vars?: Array<{ name: string; required: boolean; source: string }>;
    ports?: Array<{ port: number; source: string; confidence: number }>;
    health_endpoint?: { path: string; source: string; confidence: number };
    target_server?: { host: string; port: number; user: string; os?: string };
    app_port?: number;
    build_command?: string;
    start_command?: string;
  };
  confidence: Record<string, FieldConfidence>;
  probes_run: ProbeRecord[];
  user_answers: Record<string, string>;
  completeness: CompletenessReport;
}

export interface UserQuestion {
  question: string;
  context: string;
  field: string;
  options?: string[];
  default?: string;
  questions_remaining?: number;
}

export interface ActivityItem {
  id: string;
  type: "probe" | "discovery" | "question" | "answer" | "error" | "info";
  message: string;
  timestamp: number;
  field?: string;
  confidence?: FieldConfidence;
  status?: "running" | "done" | "error";
}

// SSE Event Payloads

export interface DiscoveryAgentStartedEvent {
  event: "discovery_agent_started";
  mode: "full" | "repo-only" | "server-only" | "interview";
  session_id: string;
}

export interface DiscoveryAgentProgressEvent {
  event: "discovery_agent_progress";
  field: string;
  value: unknown;
  confidence: FieldConfidence;
  completeness: CompletenessReport;
  probe_name: string;
  msg_type?: "assistant" | "tool_call" | "tool_result" | "status";
  agent_text?: string;
  tool_name?: string;
}

export interface DiscoveryUserQuestionEvent {
  event: "discovery_user_question";
  question: UserQuestion;
}

export interface DiscoveryAgentCompletedEvent {
  event: "discovery_agent_completed";
  state: DiscoveryState;
  completeness: CompletenessReport;
}

export type DiscoverySSEEvent =
  | DiscoveryAgentStartedEvent
  | DiscoveryAgentProgressEvent
  | DiscoveryUserQuestionEvent
  | DiscoveryAgentCompletedEvent;
