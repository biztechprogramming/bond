"use client";

import { useState, useCallback, useRef, useEffect } from "react";
import { GATEWAY_API } from "@/lib/config";
import type {
  ActivityItem,
  UserQuestion,
  FieldConfidence,
  CompletenessReport,
  ProbeRecord,
  DiscoveryState,
  DiscoverySSEEvent,
} from "@/lib/discovery-types";

export type DiscoveryStatus = "idle" | "connecting" | "discovering" | "degraded" | "question" | "complete" | "error";
export type DiscoveryMode = "full" | "repo-only" | "server-only" | "interview";

export interface RawSSEEvent {
  receivedAt: number;
  data: any;
}

export interface ConversationMessage {
  id: string;
  type: "assistant" | "tool_call" | "tool_result" | "status";
  content: string;
  toolName?: string;
  timestamp: number;
  collapsed?: boolean;
}

export interface UseAgentDiscoveryReturn {
  status: DiscoveryStatus;
  discoveryMode: DiscoveryMode;
  activityLog: ActivityItem[];
  conversationMessages: ConversationMessage[];
  rawEvents: RawSSEEvent[];
  currentQuestion: UserQuestion | null;
  questionsRemaining: number;
  discoveryState: DiscoveryState | null;
  completeness: CompletenessReport | null;
  probesRun: ProbeRecord[];
  error: string | null;
  startDiscovery: (resourceId: string, env: string, repoUrl?: string, agentId?: string, repoId?: string) => Promise<void>;
  answerQuestion: (field: string, value: string) => Promise<void>;
  cancelDiscovery: () => void;
  editField: (field: string, value: string) => void;
  forceComplete: () => void;
}

let activityCounter = 0;
function makeActivityId(): string {
  return `act-${++activityCounter}-${Date.now()}`;
}

export function useAgentDiscovery(): UseAgentDiscoveryReturn {
  const [status, setStatus] = useState<DiscoveryStatus>("idle");
  const [discoveryMode, setDiscoveryMode] = useState<DiscoveryMode>("full");
  const [activityLog, setActivityLog] = useState<ActivityItem[]>([]);
  const [currentQuestion, setCurrentQuestion] = useState<UserQuestion | null>(null);
  const [questionsRemaining, setQuestionsRemaining] = useState(0);
  const [discoveryState, setDiscoveryState] = useState<DiscoveryState | null>(null);
  const [completeness, setCompleteness] = useState<CompletenessReport | null>(null);
  const [probesRun, setProbesRun] = useState<ProbeRecord[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [rawEvents, setRawEvents] = useState<RawSSEEvent[]>([]);
  const [conversationMessages, setConversationMessages] = useState<ConversationMessage[]>([]);

  const abortRef = useRef<AbortController | null>(null);
  const sessionRef = useRef<string | null>(null);
  const discoveredFieldsRef = useRef<Map<string, number>>(new Map()); // field -> confidence score
  const lastEventTimeRef = useRef<number>(0);
  const timeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const completedRef = useRef(false);
  // Accumulate discovery state from progress events for fallback
  const accumulatedStateRef = useRef<DiscoveryState>({
    findings: {},
    confidence: {},
    probes_run: [],
    user_answers: {},
    completeness: { ready: false, required_coverage: 0, recommended_coverage: 0, missing_required: [], low_confidence: [] },
  });

  const addActivity = useCallback((item: Omit<ActivityItem, "id" | "timestamp">) => {
    setActivityLog((prev) => [...prev, { ...item, id: makeActivityId(), timestamp: Date.now() }]);
  }, []);

  const handleSSEEvent = useCallback((data: DiscoverySSEEvent & Record<string, any>) => {
    lastEventTimeRef.current = Date.now();

    // Capture every raw SSE event
    setRawEvents((prev) => [...prev, { receivedAt: Date.now(), data }]);

    switch (data.event) {
      case "discovery_agent_started": {
        const mode = data.mode || "full";
        setDiscoveryMode(mode);
        if (mode === "full") {
          setStatus("discovering");
        } else {
          setStatus("degraded");
        }
        addActivity({ type: "info", message: "Discovery started", status: "running" });
        break;
      }
      case "discovery_agent_progress": {
        if (data.completeness) setCompleteness(data.completeness);
        if (data.confidence) {
          discoveredFieldsRef.current.set(data.field, data.confidence.score);
        }

        // Collect conversation messages from agent turn events
        const msgType = (data as any).msg_type as string | undefined;
        const agentText = (data as any).agent_text as string | undefined;
        if (msgType && agentText) {
          const convType = msgType === "tool_call" ? "tool_call" as const
            : msgType === "tool_result" ? "tool_result" as const
            : msgType === "assistant" ? "assistant" as const
            : "status" as const;

          // For assistant text, append to the last assistant message if consecutive
          if (convType === "assistant") {
            setConversationMessages((prev) => {
              const last = prev[prev.length - 1];
              if (last && last.type === "assistant") {
                const updated = [...prev];
                updated[updated.length - 1] = { ...last, content: last.content + agentText };
                return updated;
              }
              return [...prev, {
                id: `conv-${Date.now()}-${Math.random().toString(36).slice(2, 6)}`,
                type: convType,
                content: agentText,
                toolName: (data as any).tool_name,
                timestamp: Date.now(),
              }];
            });
          } else {
            setConversationMessages((prev) => [...prev, {
              id: `conv-${Date.now()}-${Math.random().toString(36).slice(2, 6)}`,
              type: convType,
              content: agentText,
              toolName: (data as any).tool_name,
              timestamp: Date.now(),
            }]);
          }
        }

        // Accumulate state from progress events (skip agent_analysis field — that's conversation, not findings)
        if (data.field && data.field !== "agent_analysis" && data.field !== "status" && data.value !== undefined) {
          accumulatedStateRef.current.findings = {
            ...accumulatedStateRef.current.findings,
            [data.field]: data.value,
          };
        }
        if (data.confidence && data.field !== "agent_analysis" && data.field !== "status") {
          accumulatedStateRef.current.confidence = {
            ...accumulatedStateRef.current.confidence,
            [data.field]: data.confidence,
          };
        }
        if (data.completeness) {
          accumulatedStateRef.current.completeness = data.completeness;
        }
        // Update discoveryState progressively so the plan panel renders live
        setDiscoveryState({ ...accumulatedStateRef.current });

        // Only add activity items for non-conversation events (actual discoveries)
        if (!msgType || msgType === "tool_result") {
          const rawDetail = (data as any).raw_response ?? (data as any).value;
          const probeError = (data as any).probe_error;
          const detailStr = probeError
            ? ` — ERROR: ${probeError}`
            : rawDetail != null
              ? ` — ${typeof rawDetail === "object" ? JSON.stringify(rawDetail) : String(rawDetail)}`
              : "";
          addActivity({
            type: probeError ? "error" : "discovery",
            message: `${probeError ? "Probe failed" : "Discovered"} ${data.field}${detailStr}`,
            field: data.field,
            confidence: data.confidence,
            status: probeError ? "error" : "done",
          });
        }
        break;
      }
      case "discovery_user_question": {
        const q = data.question;
        // Issue 3: Don't show questions for fields already discovered with ≥80% confidence
        const existingScore = discoveredFieldsRef.current.get(q.field);
        if (existingScore !== undefined && existingScore >= 0.8) {
          addActivity({ type: "info", message: `Skipped question for ${q.field} (already ${Math.round(existingScore * 100)}% confident)`, status: "done" });
          break;
        }
        setCurrentQuestion(q);
        setQuestionsRemaining(q.questions_remaining ?? 0);
        setStatus("question");
        addActivity({ type: "question", message: q.question });
        break;
      }
      case "discovery_agent_completed": {
        completedRef.current = true;
        if (timeoutRef.current) { clearTimeout(timeoutRef.current); timeoutRef.current = null; }
        const completedData = data as any;
        // If the backend sent an error (e.g. agent threw), surface it
        if (completedData.error) {
          setError(completedData.error);
          setStatus("error");
          addActivity({ type: "error", message: `Discovery failed: ${completedData.error}`, status: "error" });
          break;
        }
        const finalState = data.state || accumulatedStateRef.current;
        const finalCompleteness = data.completeness || accumulatedStateRef.current.completeness;
        setDiscoveryState(finalState);
        setCompleteness(finalCompleteness);
        setProbesRun(finalState.probes_run || []);
        setStatus("complete");
        addActivity({ type: "info", message: "Discovery complete", status: "done" });
        break;
      }
      default: {
        // Catch-all: log unknown event types with full payload
        addActivity({
          type: "info",
          message: `Unknown SSE event: ${(data as any).event} — ${JSON.stringify(data)}`,
          status: "done",
        });
        break;
      }
    }
  }, [addActivity]);

  const startDiscovery = useCallback(async (resourceId: string, env: string, repoUrl?: string, agentId?: string, repoId?: string) => {
    // Reset
    completedRef.current = false;
    setStatus("connecting");
    setError(null);
    setActivityLog([]);
    setCurrentQuestion(null);
    setDiscoveryState(null);
    setCompleteness(null);
    setProbesRun([]);
    setDiscoveryMode("full");
    setRawEvents([]);
    setConversationMessages([]);

    const controller = new AbortController();
    abortRef.current = controller;

    try {
      // Initiate agent discovery — get session_id
      const initRes = await fetch(`${GATEWAY_API}/deployments/agent-discover`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          resource_id: resourceId || undefined,
          repo_url: repoUrl || resourceId,
          environment: env,
          agent_id: agentId || undefined,
          repo_id: repoId || undefined,
        }),
        signal: controller.signal,
      });

      if (!initRes.ok) {
        throw new Error(`Discovery init failed: ${initRes.status}`);
      }

      const initData = await initRes.json();
      const sessionId = initData.session_id;

      if (!sessionId) {
        throw new Error("Agent discovery did not return a session");
      }

      sessionRef.current = sessionId;

      // Connect to SSE stream
      const sseUrl = `${GATEWAY_API}/deployments/discovery/stream/${sessionId}`;
      const sseRes = await fetch(sseUrl, { signal: controller.signal });

      if (!sseRes.ok || !sseRes.body) {
        throw new Error(`SSE connection failed: ${sseRes.status}`);
      }

      const reader = sseRes.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";

      setStatus("discovering");
      lastEventTimeRef.current = Date.now();

      // Timeout: if no events for 30s after the last event, auto-complete with accumulated data
      const startTimeout = () => {
        if (timeoutRef.current) clearTimeout(timeoutRef.current);
        timeoutRef.current = setTimeout(() => {
          if (completedRef.current) return;
          // Only auto-complete if still in a discovering/question state
          setStatus((currentStatus) => {
            if (currentStatus === "discovering" || currentStatus === "degraded" || currentStatus === "question") {
              const accState = accumulatedStateRef.current;
              setDiscoveryState(accState);
              setCompleteness(accState.completeness);
              setProbesRun(accState.probes_run || []);
              addActivity({ type: "info", message: "Discovery timed out — completing with available data", status: "done" });
              return "complete";
            }
            return currentStatus;
          });
        }, 30_000);
      };
      startTimeout();

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });

        const lines = buffer.split("\n");
        buffer = lines.pop() || "";
        for (const line of lines) {
          if (line.startsWith("data: ")) {
            try {
              const event = JSON.parse(line.slice(6)) as DiscoverySSEEvent;
              handleSSEEvent(event);
              startTimeout(); // Reset timeout on each event
            } catch { /* skip malformed */ }
          }
        }
      }
      // Stream ended — check if we received ANY events
      if (timeoutRef.current) { clearTimeout(timeoutRef.current); timeoutRef.current = null; }
      setRawEvents((evts) => {
        if (evts.length === 0) {
          setError("No SSE events received from gateway");
          setStatus("error");
          addActivity({ type: "error", message: "No SSE events received from gateway — the discovery stream returned empty", status: "error" });
        }
        return evts;
      });
      if (!completedRef.current) {
        setStatus((currentStatus) => {
          if (currentStatus === "discovering" || currentStatus === "degraded" || currentStatus === "question") {
            const accState = accumulatedStateRef.current;
            setDiscoveryState(accState);
            setCompleteness(accState.completeness);
            setProbesRun(accState.probes_run || []);
            setCurrentQuestion(null);
            addActivity({ type: "info", message: "Discovery stream ended — completing with available data", status: "done" });
            return "complete";
          }
          return currentStatus;
        });
      }
    } catch (err: any) {
      if (err.name === "AbortError") return;
      setError(err.message);
      setStatus("error");
      addActivity({ type: "error", message: err.message });
    }
  }, [handleSSEEvent, addActivity]);

  const answerQuestion = useCallback(async (field: string, value: string) => {
    if (!sessionRef.current) return;
    setCurrentQuestion(null);
    setStatus("discovering");
    addActivity({ type: "answer", message: `Answered: ${value}`, field });
    // Track as discovered with full confidence
    discoveredFieldsRef.current.set(field, 1.0);

    try {
      const res = await fetch(`${GATEWAY_API}/deployments/discovery/answer/${sessionRef.current}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ field, value }),
      });
      // If the answer endpoint returns a next action or triggers agent resume,
      // the SSE stream (still open) will deliver subsequent events.
      // If the response indicates completion, handle it:
      if (res.ok) {
        const data = await res.json().catch(() => null);
        if (data?.completed && data?.state) {
          setDiscoveryState(data.state);
          setCompleteness(data.completeness || null);
          setStatus("complete");
          addActivity({ type: "info", message: "Discovery complete", status: "done" });
        }
      }
    } catch (err: any) {
      setError(err.message);
    }
  }, [addActivity]);

  const cancelDiscovery = useCallback(() => {
    if (abortRef.current) abortRef.current.abort();
    if (sessionRef.current) {
      fetch(`${GATEWAY_API}/deployments/discovery/cancel/${sessionRef.current}`, {
        method: "POST",
      }).catch(() => {});
    }
    setStatus("idle");
  }, []);

  const forceComplete = useCallback(() => {
    if (timeoutRef.current) { clearTimeout(timeoutRef.current); timeoutRef.current = null; }
    const accState = accumulatedStateRef.current;
    setDiscoveryState(accState);
    setCompleteness(accState.completeness);
    setProbesRun(accState.probes_run || []);
    setCurrentQuestion(null);
    setStatus("complete");
    addActivity({ type: "info", message: "Discovery force-completed with available data", status: "done" });
  }, [addActivity]);

  const editField = useCallback((field: string, value: string) => {
    setDiscoveryState((prev) => {
      if (!prev) return prev;
      return {
        ...prev,
        findings: { ...prev.findings, [field]: value },
        confidence: {
          ...prev.confidence,
          [field]: { source: "user-provided" as const, detail: "Edited by user", score: 1.0 },
        },
      };
    });
  }, []);

  // Cleanup on unmount
  useEffect(() => {
    return () => {
      if (abortRef.current) abortRef.current.abort();
      if (timeoutRef.current) clearTimeout(timeoutRef.current);
    };
  }, []);

  return {
    status,
    discoveryMode,
    activityLog,
    conversationMessages,
    rawEvents,
    currentQuestion,
    questionsRemaining,
    discoveryState,
    completeness,
    probesRun,
    error,
    startDiscovery,
    answerQuestion,
    cancelDiscovery,
    editField,
    forceComplete,
  };
}
