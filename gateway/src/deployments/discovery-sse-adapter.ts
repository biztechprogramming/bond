/**
 * Discovery SSE Adapter — translates agent turn SSE events into discovery
 * progress events expected by the frontend.
 *
 * Design Doc 080 §4 — SSE Event Adapter
 */

import type { SSEEvent } from "../backend/sse-parser.js";
import type { ProbeResults } from "./discovery-agent.js";

export interface DiscoverySSEPayload {
  event: string;
  session_id: string;
  [key: string]: unknown;
}

/**
 * Parse the agent's text response for the structured discovery-result JSON block.
 * Returns null if not found.
 */
export function parseDiscoveryResult(text: string): Record<string, unknown> | null {
  // Match ```discovery-result ... ``` or ```json ... ```
  const match = text.match(/```(?:discovery-result|json)\s*([\s\S]*?)```/);
  if (!match) return null;
  try {
    return JSON.parse(match[1].trim());
  } catch {
    return null;
  }
}

/**
 * Convert pre-gathered probe results into a discovery state findings object,
 * for use in building the final DiscoveryState.
 */
export function probeResultsToFindings(probes: ProbeResults): Record<string, unknown> {
  const findings: Record<string, unknown> = {};
  if (probes.framework) findings.framework = probes.framework;
  if (probes.build_strategy) findings.build_strategy = probes.build_strategy;
  if (probes.services) findings.services = probes.services;
  if (probes.env_vars) findings.env_vars = probes.env_vars;
  if (probes.ports) findings.ports = probes.ports;
  if (probes.health_endpoint) findings.health_endpoint = probes.health_endpoint;
  if (probes.app_port) findings.app_port = probes.app_port;
  if (probes.server_os) findings.server_os = probes.server_os;
  return findings;
}

/**
 * Build a discovery prompt to send to the agent, including pre-gathered probe results.
 */
export function buildDiscoveryPrompt(
  repoId: string,
  probeResults: ProbeResults,
  resourceId?: string,
): string {
  let prompt = `[DEPLOYMENT DISCOVERY]\n\nAnalyze the repository (repo_id: ${repoId}) for deployment configuration.\n`;

  if (resourceId) {
    prompt += `Target resource: ${resourceId}\n`;
  }

  const probeData = probeResultsToFindings(probeResults);
  if (Object.keys(probeData).length > 0) {
    prompt += `\nPre-gathered probe results from automated file/SSH scanners:\n\`\`\`json\n${JSON.stringify(probeData, null, 2)}\n\`\`\`\n`;
    prompt += `\nUse these probe results as a starting point. Confirm, correct, or augment them with your own analysis of the repository files.\n`;
  } else {
    prompt += `\nNo automated probe results were available. Please analyze the repository files directly.\n`;
  }

  prompt += `\nReturn your findings as a structured JSON block wrapped in \`\`\`discovery-result ... \`\`\` markers, matching the discovery schema from the appdeploy skill.\n`;

  return prompt;
}

/**
 * Map a single agent SSE event to a discovery SSE payload.
 * Returns null if the event should not be forwarded.
 *
 * Agent SSE events have:
 *   - event: "message" with data.type: "text", "tool_call", "tool_result", "status", etc.
 *   - event: "done"
 *   - event: "error"
 */
export function mapAgentEventToDiscovery(
  sseEvent: SSEEvent,
  sessionId: string,
  accumulatedText: { value: string },
): DiscoverySSEPayload | null {
  const { event, data } = sseEvent;

  if (event === "error") {
    return {
      event: "discovery_agent_completed",
      session_id: sessionId,
      state: null,
      completeness: null,
      error: String(data.message || data.error || "Agent error"),
    };
  }

  if (event === "done") {
    // Try to parse the accumulated agent text for discovery results
    const parsed = parseDiscoveryResult(accumulatedText.value);
    if (parsed) {
      return {
        event: "discovery_agent_completed",
        session_id: sessionId,
        state: {
          findings: parsed,
          confidence: {},
          probes_run: [],
          user_answers: {},
          completeness: { ready: true, required_coverage: 1, recommended_coverage: 1, missing_required: [], low_confidence: [] },
        },
        completeness: { ready: true, required_coverage: 1, recommended_coverage: 1, missing_required: [], low_confidence: [] },
      };
    }
    // No structured output found — complete with what we have
    return {
      event: "discovery_agent_completed",
      session_id: sessionId,
      state: { findings: {}, confidence: {}, probes_run: [], user_answers: {}, completeness: { ready: false, required_coverage: 0, recommended_coverage: 0, missing_required: [], low_confidence: [] } },
      completeness: { ready: false, required_coverage: 0, recommended_coverage: 0, missing_required: [], low_confidence: [] },
      agent_text: accumulatedText.value,
    };
  }

  // Accumulate text from message events
  if (event === "message") {
    const type = data.type as string;
    if (type === "text" || type === "content") {
      const chunk = String(data.content || data.text || data.delta || "");
      accumulatedText.value += chunk;

      // Emit progress events for agent reasoning
      return {
        event: "discovery_agent_progress",
        session_id: sessionId,
        field: "agent_analysis",
        value: chunk,
        confidence: { source: "inferred", detail: "Agent analysis in progress", score: 0 },
        completeness: { ready: false, required_coverage: 0, recommended_coverage: 0, missing_required: [], low_confidence: [] },
        probe_name: "agent_turn",
      };
    }

    if (type === "status") {
      return {
        event: "discovery_agent_progress",
        session_id: sessionId,
        field: "status",
        value: String(data.status || data.content || ""),
        confidence: { source: "inferred", detail: "Agent status update", score: 0 },
        completeness: { ready: false, required_coverage: 0, recommended_coverage: 0, missing_required: [], low_confidence: [] },
        probe_name: "agent_turn",
      };
    }
  }

  return null;
}
