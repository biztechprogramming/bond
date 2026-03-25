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
  startDiscovery: (resourceId: string, env: string) => Promise<void>;
  answerQuestion: (field: string, value: string) => Promise<void>;
  cancelDiscovery: () => void;
  editField: (field: string, value: string) => void;
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

  const addActivity = useCallback((item: Omit<ActivityItem, "id" | "timestamp">) => {
    setActivityLog((prev) => [...prev, { ...item, id: makeActivityId(), timestamp: Date.now() }]);
  }, []);

  const handleSSEEvent = useCallback((data: DiscoverySSEEvent) => {
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
        setCompleteness(data.completeness);
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
        setCurrentQuestion(q);
        setQuestionsRemaining(q.questions_remaining ?? 0);
        setStatus("question");
        addActivity({ type: "question", message: q.question });
        break;
      }
      case "discovery_agent_completed": {
        setDiscoveryState(data.state);
        setCompleteness(data.completeness);
        setProbesRun(data.state.probes_run);
        setStatus("complete");
        addActivity({ type: "info", message: "Discovery complete", status: "done" });
        break;
      }
    }
  }, [addActivity]);

  const startDiscovery = useCallback(async (resourceId: string, env: string) => {
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
      const initRes = await fetch(`${GATEWAY_API}/broker/deploy`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ action: "discover", resource_id: resourceId, agent: true }),
        signal: controller.signal,
      });

      if (!initRes.ok) {
        throw new Error(`Discovery init failed: ${initRes.status}`);
      }

      const initData = await initRes.json();
      const sessionId = initData.session_id;

      if (!sessionId) {
        // Agent discovery not enabled — fall back handled by caller
        throw new Error("no_session");
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
            } catch { /* skip malformed */ }
          }
        }
      }
    } catch (err: any) {
      if (err.name === "AbortError") return;
      if (err.message === "no_session") {
        setError("no_session");
        setStatus("error");
        return;
      }
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

    try {
      await fetch(`${GATEWAY_API}/deployments/discovery/answer/${sessionRef.current}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ field, value }),
      });
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
  };
}
