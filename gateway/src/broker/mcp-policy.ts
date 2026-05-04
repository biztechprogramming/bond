/**
 * MCP Policy Engine — evaluates whether an agent may call a specific MCP tool.
 *
 * Supports two rule sources:
 * 1. Static glob-based rules (MCPPolicyRule[]) — first-match-wins.
 * 2. Per-server method permissions loaded from backend — tool-level allow/deny.
 *
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

interface ServerMethodPermissions {
  serverName: string;
  agentId: string | null;
  permissions: Record<string, "allow" | "deny">;
}

export class MCPPolicyEngine {
  private rules: MCPPolicyRule[] = [];
  private serverPerms: ServerMethodPermissions[] = [];

  constructor(rules?: MCPPolicyRule[]) {
    this.rules = rules ?? [];
  }

  loadRules(rules: MCPPolicyRule[]): void {
    this.rules = rules;
  }

  /**
   * Load per-server method permissions fetched from backend.
   */
  loadServerPermissions(perms: ServerMethodPermissions[]): void {
    this.serverPerms = perms;
  }

  /**
   * Evaluate whether an agent may call a specific MCP tool.
   * 1. Check per-server method permissions first (explicit deny wins).
   * 2. Then check glob rules (first-match-wins).
   * 3. Default: allow.
   */
  evaluate(
    toolName: string,
    agentId: string,
    _sessionId?: string,
  ): MCPPolicyDecision {
    // Check per-server method permissions
    for (const sp of this.serverPerms) {
      const prefix = `mcp_${sp.serverName}_`;
      if (!toolName.startsWith(prefix)) continue;

      // Check agent scope
      if (sp.agentId && sp.agentId !== agentId) continue;

      const methodName = toolName.slice(prefix.length);
      const decision = sp.permissions[methodName];
      if (decision === "deny") {
        return {
          decision: "deny",
          reason: `Method '${methodName}' denied on server '${sp.serverName}'`,
          rule: `server-perm:${sp.serverName}:${methodName}`,
        };
      }
      // If "allow" or not listed, fall through to glob rules then default
    }

    // Check glob-based rules (first-match-wins)
    for (const rule of this.rules) {
      if (rule.agent_ids && rule.agent_ids.length > 0) {
        const agentMatch = rule.agent_ids.some((pattern) =>
          globToRegex(pattern).test(agentId),
        );
        if (!agentMatch) continue;
      }

      const toolMatch = rule.tools.some((pattern) =>
        globToRegex(pattern).test(toolName),
      );
      if (!toolMatch) continue;

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
