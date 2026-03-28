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

export interface UseAgentDiscoveryReturn {
  status: DiscoveryStatus;
  discoveryMode: DiscoveryMode;
  activityLog: ActivityItem[];
  currentQuestion: UserQuestion | null;
  questionsRemaining: number;
  discoveryState: DiscoveryState | null;
  completeness: CompletenessReport | null;
  probesRun: ProbeRecord[];
  error: string | null;
  startDiscovery: (resourceId: string, env: string, repoUrl?: string) => Promise<void>;
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

  const abortRef = useRef<AbortController | null>(null);
  const sessionRef = useRef<string | null>(null);
  const discoveredFieldsRef = useRef<Map<string, number>>(new Map()); // field -> confidence score
  const lastEventTimeRef = useRef<number>(0);
  const timeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);
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

  const handleSSEEvent = useCallback((data: DiscoverySSEEvent) => {
    lastEventTimeRef.current = Date.now();

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
        // Accumulate state from progress events for fallback
        if (data.field && data.value !== undefined) {
          accumulatedStateRef.current.findings = {
            ...accumulatedStateRef.current.findings,
            [data.field]: data.value,
          };
        }
        if (data.confidence) {
          accumulatedStateRef.current.confidence = {
            ...accumulatedStateRef.current.confidence,
            [data.field]: data.confidence,
          };
        }
        if (data.completeness) {
          accumulatedStateRef.current.completeness = data.completeness;
        }
        addActivity({
          type: "discovery",
          message: `Discovered ${data.field}`,
          field: data.field,
          confidence: data.confidence,
          status: "done",
        });
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
        if (timeoutRef.current) { clearTimeout(timeoutRef.current); timeoutRef.current = null; }
        const finalState = data.state || accumulatedStateRef.current;
        const finalCompleteness = data.completeness || accumulatedStateRef.current.completeness;
        setDiscoveryState(finalState);
        setCompleteness(finalCompleteness);
        setProbesRun(finalState.probes_run || []);
        setStatus("complete");
        addActivity({ type: "info", message: "Discovery complete", status: "done" });
        break;
      }
    }
  }, [addActivity]);

  const startDiscovery = useCallback(async (resourceId: string, env: string, repoUrl?: string) => {
    // Reset
    setStatus("connecting");
    setError(null);
    setActivityLog([]);
    setCurrentQuestion(null);
    setDiscoveryState(null);
    setCompleteness(null);
    setProbesRun([]);
    setDiscoveryMode("full");

    const controller = new AbortController();
    abortRef.current = controller;

    try {
      // Initiate agent discovery — get session_id
      const initRes = await fetch(`${GATEWAY_API}/deployments/agent-discover`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          resource_id: resourceId,
          repo_url: repoUrl || resourceId,
          environment: env,
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
      // Stream ended — if we didn't get a completion event, auto-complete with accumulated data
      if (timeoutRef.current) { clearTimeout(timeoutRef.current); timeoutRef.current = null; }
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
