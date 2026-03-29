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
 * Normalize the agent's rich discovery-result JSON into the flat findings
 * schema the frontend DiscoveryState expects.
 *
 * Agent returns:  { app_name, services[], runtime, containerization, ... }
 * Frontend needs: { source, framework, build_strategy, app_port, health_endpoint, target_server, services, env_vars, ports, ... }
 */
export function normalizeAgentFindings(raw: Record<string, unknown>): Record<string, unknown> {
  const findings: Record<string, unknown> = {};

  // source / app name
  const appName = raw.app_name || raw.name || raw.repo_id || "";
  if (appName) findings.source = String(appName);

  // framework — frontend expects { framework, runtime?, confidence, evidence[] }
  const services = raw.services as any[] | undefined;
  const runtimeInfo = raw.runtime as any;
  if (raw.framework) {
    // Already in the right shape
    findings.framework = raw.framework;
  } else if (services?.length) {
    const primary = services[0];
    findings.framework = {
      framework: String(primary.framework || primary.type || "unknown"),
      runtime: runtimeInfo?.primary
        ? `${runtimeInfo.primary}${runtimeInfo.version ? " " + runtimeInfo.version : ""}`
        : primary.language || undefined,
      confidence: primary.confidence ?? 0.9,
      evidence: [`Detected from service: ${primary.name || "primary"}`],
    };
  }

  // build_strategy — frontend expects { strategy, confidence, evidence[] }
  if (raw.build_strategy) {
    findings.build_strategy = raw.build_strategy;
  } else if (raw.containerization) {
    const c = raw.containerization as any;
    findings.build_strategy = {
      strategy: c.dockerfile ? "docker" : "script",
      confidence: 0.9,
      evidence: c.dockerfile ? [`Dockerfile: ${c.dockerfile}`] : ["No Dockerfile found"],
    };
  } else if (raw.build) {
    const b = raw.build as any;
    findings.build_strategy = {
      strategy: b.install_command ? "script" : "unknown",
      confidence: 0.8,
      evidence: b.install_command ? [`Install: ${b.install_command}`] : [],
    };
  }

  // app_port — frontend expects a top-level number
  if (raw.app_port != null) {
    findings.app_port = Number(raw.app_port);
  } else if (services?.length) {
    // Pick the first API/backend service port, or just the first port found
    const apiService = services.find((s: any) => s.type === "api" || s.type === "backend") || services[0];
    if (apiService?.port) {
      findings.app_port = Number(apiService.port);
    }
  } else if (raw.ports_summary) {
    // ports_summary is { "18790": "backend", ... }
    const ports = Object.keys(raw.ports_summary as Record<string, string>);
    if (ports.length) findings.app_port = Number(ports[0]);
  }

  // health_endpoint — frontend expects { path, source, confidence }
  if (raw.health_endpoint) {
    findings.health_endpoint = raw.health_endpoint;
  } else if (services?.length) {
    for (const svc of services) {
      if ((svc as any).health_endpoint) {
        findings.health_endpoint = (svc as any).health_endpoint;
        break;
      }
    }
  }

  // target_server — frontend expects { host, port, user, os? }
  // The agent can't know this, so provide an editable default
  if (raw.target_server) {
    findings.target_server = raw.target_server;
  } else {
    findings.target_server = {
      host: "your-server.example.com",
      port: 22,
      user: "deploy",
      os: (raw.containerization as any)?.base_image ? "linux" : "linux",
    };
  }

  // services — pass through as-is, frontend can render the array
  if (services?.length) {
    findings.services = services.map((s: any) => ({
      name: s.name || "unknown",
      type: s.type || "service",
      source: s.source || s.entry_point || `${s.framework || s.language || "unknown"}`,
      confidence: s.confidence ?? 0.85,
      port: s.port,
      command: s.command,
      dev_command: s.dev_command,
    }));
  }

  // env_vars — frontend expects Array<{ name, required, source }>
  if (raw.env_vars) {
    const ev = raw.env_vars as any;
    if (Array.isArray(ev)) {
      findings.env_vars = ev;
    } else if (ev.required || ev.optional) {
      const vars: any[] = [];
      for (const name of (ev.required || [])) {
        vars.push({ name, required: true, source: "discovery" });
      }
      for (const name of (ev.optional || [])) {
        vars.push({ name, required: false, source: "discovery" });
      }
      findings.env_vars = vars;
    }
  }

  // ports — frontend expects Array<{ port, source, confidence }>
  if (raw.ports) {
    findings.ports = raw.ports;
  } else if (raw.ports_summary) {
    const ps = raw.ports_summary as Record<string, string>;
    findings.ports = Object.entries(ps).map(([port, desc]) => ({
      port: Number(port),
      source: String(desc),
      confidence: 0.9,
    }));
  } else if (services?.length) {
    findings.ports = services
      .filter((s: any) => s.port)
      .map((s: any) => ({ port: Number(s.port), source: s.name, confidence: 0.9 }));
  }

  // build/start commands
  if (raw.build) {
    const b = raw.build as any;
    if (b.install_command) findings.build_command = b.install_command;
    if (b.dev_command) findings.start_command = b.dev_command;
  }

  // Pass through description
  if (raw.description) findings.description = raw.description;
  if (raw.architecture) findings.architecture = raw.architecture;
  if (raw.database) findings.database = raw.database;
  if (raw.docker_compose) findings.docker_compose = raw.docker_compose;
  if (raw.containerization) findings.containerization = raw.containerization;

  return findings;
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
): DiscoverySSEPayload | DiscoverySSEPayload[] | null {
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
    // If the done event carries text content, accumulate it
    const doneText = String(data.text || data.content || data.response || data.message || "");
    if (doneText) {
      accumulatedText.value += doneText;
    }

    const results: DiscoverySSEPayload[] = [];

    // Emit accumulated text as a conversation message so the frontend shows it
    if (accumulatedText.value) {
      results.push({
        event: "discovery_agent_progress",
        session_id: sessionId,
        field: "agent_analysis",
        value: accumulatedText.value,
        agent_text: accumulatedText.value,
        msg_type: "assistant",
        confidence: { source: "inferred", detail: "Agent final response", score: 0 },
        completeness: { ready: false, required_coverage: 0, recommended_coverage: 0, missing_required: [], low_confidence: [] },
        probe_name: "agent_turn",
      });
    }

    // Try to parse the accumulated agent text for discovery results
    const parsed = parseDiscoveryResult(accumulatedText.value);
    if (parsed) {
      results.push({
        event: "discovery_agent_completed",
        session_id: sessionId,
        state: {
          findings: normalizeAgentFindings(parsed),
          confidence: {},
          probes_run: [],
          user_answers: {},
          completeness: { ready: true, required_coverage: 1, recommended_coverage: 1, missing_required: [], low_confidence: [] },
        },
        completeness: { ready: true, required_coverage: 1, recommended_coverage: 1, missing_required: [], low_confidence: [] },
      });
    } else {
      // No structured output found — complete with what we have
      results.push({
        event: "discovery_agent_completed",
        session_id: sessionId,
        state: { findings: {}, confidence: {}, probes_run: [], user_answers: {}, completeness: { ready: false, required_coverage: 0, recommended_coverage: 0, missing_required: [], low_confidence: [] } },
        completeness: { ready: false, required_coverage: 0, recommended_coverage: 0, missing_required: [], low_confidence: [] },
        agent_text: accumulatedText.value,
      });
    }

    return results;
  }

  // Backend sends top-level event names: "chunk", "tool_call", "status", "interim_message"
  // Also handle legacy "message" wrapper format with data.type

  if (event === "chunk" || event === "interim_message") {
    const chunk = String(data.content || data.text || "");
    accumulatedText.value += chunk;
    return {
      event: "discovery_agent_progress",
      session_id: sessionId,
      field: "agent_analysis",
      value: chunk,
      agent_text: chunk,
      msg_type: "assistant",
      confidence: { source: "inferred", detail: "Agent analysis in progress", score: 0 },
      completeness: { ready: false, required_coverage: 0, recommended_coverage: 0, missing_required: [], low_confidence: [] },
      probe_name: "agent_turn",
    };
  }

  if (event === "tool_call") {
    const toolName = String(data.tool_name || data.name || data.tool || "unknown_tool");
    const toolArgs = data.args || data.arguments || data.input || {};
    return {
      event: "discovery_agent_progress",
      session_id: sessionId,
      field: "agent_analysis",
      value: `Calling tool: ${toolName}`,
      agent_text: JSON.stringify({ tool: toolName, args: toolArgs }),
      msg_type: "tool_call",
      tool_name: toolName,
      confidence: { source: "inferred", detail: `Tool call: ${toolName}`, score: 0 },
      completeness: { ready: false, required_coverage: 0, recommended_coverage: 0, missing_required: [], low_confidence: [] },
      probe_name: "agent_turn",
    };
  }

  if (event === "status") {
    const statusText = String(data.state || data.status || data.content || "");
    return {
      event: "discovery_agent_progress",
      session_id: sessionId,
      field: "status",
      value: statusText,
      agent_text: statusText,
      msg_type: "status",
      confidence: { source: "inferred", detail: "Agent status update", score: 0 },
      completeness: { ready: false, required_coverage: 0, recommended_coverage: 0, missing_required: [], low_confidence: [] },
      probe_name: "agent_turn",
    };
  }

  // Legacy "message" wrapper format (data.type discriminator)
  if (event === "message") {
    const type = data.type as string;
    if (type === "text" || type === "content") {
      const chunk = String(data.content || data.text || data.delta || "");
      accumulatedText.value += chunk;
      return {
        event: "discovery_agent_progress",
        session_id: sessionId,
        field: "agent_analysis",
        value: chunk,
        agent_text: chunk,
        msg_type: "assistant",
        confidence: { source: "inferred", detail: "Agent analysis in progress", score: 0 },
        completeness: { ready: false, required_coverage: 0, recommended_coverage: 0, missing_required: [], low_confidence: [] },
        probe_name: "agent_turn",
      };
    }

    if (type === "tool_call") {
      const toolName = String(data.tool_name || data.name || data.tool || "unknown_tool");
      const toolArgs = data.args || data.arguments || data.input || {};
      return {
        event: "discovery_agent_progress",
        session_id: sessionId,
        field: "agent_analysis",
        value: `Calling tool: ${toolName}`,
        agent_text: JSON.stringify({ tool: toolName, args: toolArgs }),
        msg_type: "tool_call",
        tool_name: toolName,
        confidence: { source: "inferred", detail: `Tool call: ${toolName}`, score: 0 },
        completeness: { ready: false, required_coverage: 0, recommended_coverage: 0, missing_required: [], low_confidence: [] },
        probe_name: "agent_turn",
      };
    }

    if (type === "tool_result") {
      const toolName = String(data.name || data.tool || "tool");
      const toolOutput = String(data.content || data.output || data.result || "");
      return {
        event: "discovery_agent_progress",
        session_id: sessionId,
        field: "agent_analysis",
        value: `Tool result: ${toolName}`,
        agent_text: toolOutput,
        msg_type: "tool_result",
        tool_name: toolName,
        confidence: { source: "inferred", detail: `Tool result: ${toolName}`, score: 0 },
        completeness: { ready: false, required_coverage: 0, recommended_coverage: 0, missing_required: [], low_confidence: [] },
        probe_name: "agent_turn",
      };
    }

    if (type === "status") {
      const statusText = String(data.state || data.status || data.content || "");
      return {
        event: "discovery_agent_progress",
        session_id: sessionId,
        field: "status",
        value: statusText,
        agent_text: statusText,
        msg_type: "status",
        confidence: { source: "inferred", detail: "Agent status update", score: 0 },
        completeness: { ready: false, required_coverage: 0, recommended_coverage: 0, missing_required: [], low_confidence: [] },
        probe_name: "agent_turn",
      };
    }
  }

  return null;
}
