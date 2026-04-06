export interface WorkItem {
  id: string;
  title: string;
  status: string;
  ordinal: number;
  context_snapshot: Record<string, unknown> | null;
  notes: string[];
  files_changed: string[];
  started_at: string | null;
  completed_at: string | null;
  created_at: string;
  updated_at: string;
}

export interface WorkPlan {
  id: string;
  agent_id: string;
  conversation_id: string | null;
  parent_plan_id: string | null;
  title: string;
  status: string;
  created_at: string;
  updated_at: string;
  completed_at: string | null;
  items?: WorkItem[];
}

export type AgentStatus = "idle" | "thinking" | "tool_calling" | "responding" | "stopping" | "interrupted";

export interface ChatMessage {
  id?: string;
  role: "user" | "assistant" | "system";
  content: string;
  status?: "sending" | "queued" | "delivered" | "complete";
  injected?: boolean;
  agentName?: string;
  imageResults?: Array<{ paths: string[]; prompt: string; revised_prompt?: string; provider: string; model: string; size: string; cost?: number }>;
}

export interface PlanCardData {
  id: string;
  title: string;
  status: string;
  items: { id: string; title: string; status: string }[];
}
