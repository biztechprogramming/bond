/**
 * MCP Policy Engine — evaluates whether an agent may call a specific MCP tool.
 *
 * Rules use glob patterns for tool names and are evaluated first-match-wins.
 * Default rule: allow all (backward compatible).
 */

import type { MCPPolicyRule } from "./types.js";

function globToRegex(pattern: string): RegExp {
  const escaped = pattern
    .replace(/[.+^${}()|[\]\\]/g, "\\$&")
    .replace(/\*/g, ".*")
    .replace(/\?/g, ".");
  return new RegExp(`^${escaped}$`);
}

export interface MCPPolicyDecision {
  decision: "allow" | "deny";
  reason?: string;
  rule?: string;
}

export class MCPPolicyEngine {
  private rules: MCPPolicyRule[] = [];

  constructor(rules?: MCPPolicyRule[]) {
    this.rules = rules ?? [];
  }

  loadRules(rules: MCPPolicyRule[]): void {
    this.rules = rules;
  }

  /**
   * Evaluate whether an agent may call a specific MCP tool.
   * First matching rule wins. If no rule matches, allow (default).
   */
  evaluate(
    toolName: string,
    agentId: string,
    _sessionId?: string,
  ): MCPPolicyDecision {
    for (const rule of this.rules) {
      // Check agent scope
      if (rule.agent_ids && rule.agent_ids.length > 0) {
        const agentMatch = rule.agent_ids.some((pattern) =>
          globToRegex(pattern).test(agentId),
        );
        if (!agentMatch) continue;
      }

      // Check tool name match
      const toolMatch = rule.tools.some((pattern) =>
        globToRegex(pattern).test(toolName),
      );
      if (!toolMatch) continue;

      // First match wins
      return {
        decision: rule.decision,
        reason: rule.reason,
        rule: rule.name ?? `rule:${rule.tools.join(",")}`,
      };
    }

    // Default: allow all
    return { decision: "allow", reason: "default-allow" };
  }
}
