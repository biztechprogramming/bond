/**
 * MCP Policy Engine tests — glob rules and per-server method permissions.
 */

import { describe, it, expect } from "vitest";
import { MCPPolicyEngine } from "../broker/mcp-policy.js";

describe("MCPPolicyEngine", () => {
  describe("default behavior", () => {
    it("allows all tools when no rules or permissions are loaded", () => {
      const engine = new MCPPolicyEngine();
      const result = engine.evaluate("mcp_github_create_issue", "agent-1");
      expect(result.decision).toBe("allow");
      expect(result.reason).toBe("default-allow");
    });
  });

  describe("glob rules", () => {
    it("denies tools matching a deny rule", () => {
      const engine = new MCPPolicyEngine([
        { tools: ["mcp_filesystem_*"], decision: "deny", reason: "filesystem blocked" },
      ]);
      const result = engine.evaluate("mcp_filesystem_delete_file", "agent-1");
      expect(result.decision).toBe("deny");
      expect(result.reason).toBe("filesystem blocked");
    });

    it("allows tools not matching any rule", () => {
      const engine = new MCPPolicyEngine([
        { tools: ["mcp_filesystem_*"], decision: "deny" },
      ]);
      const result = engine.evaluate("mcp_github_create_issue", "agent-1");
      expect(result.decision).toBe("allow");
    });

    it("respects agent_ids scope in rules", () => {
      const engine = new MCPPolicyEngine([
        { tools: ["mcp_deploy_*"], agent_ids: ["deploy-*"], decision: "allow" },
        { tools: ["mcp_deploy_*"], decision: "deny", reason: "deploy restricted" },
      ]);
      expect(engine.evaluate("mcp_deploy_run", "deploy-agent").decision).toBe("allow");
      expect(engine.evaluate("mcp_deploy_run", "research-agent").decision).toBe("deny");
    });
  });

  describe("per-server method permissions", () => {
    it("denies a specifically denied method", () => {
      const engine = new MCPPolicyEngine();
      engine.loadServerPermissions([
        {
          serverName: "github",
          agentId: null,
          permissions: { create_issue: "allow", delete_repo: "deny" },
        },
      ]);
      const result = engine.evaluate("mcp_github_delete_repo", "agent-1");
      expect(result.decision).toBe("deny");
      expect(result.reason).toContain("delete_repo");
      expect(result.reason).toContain("github");
    });

    it("allows an explicitly allowed method", () => {
      const engine = new MCPPolicyEngine();
      engine.loadServerPermissions([
        {
          serverName: "github",
          agentId: null,
          permissions: { create_issue: "allow", delete_repo: "deny" },
        },
      ]);
      const result = engine.evaluate("mcp_github_create_issue", "agent-1");
      expect(result.decision).toBe("allow");
    });

    it("allows methods not listed in permissions (default allow)", () => {
      const engine = new MCPPolicyEngine();
      engine.loadServerPermissions([
        {
          serverName: "github",
          agentId: null,
          permissions: { delete_repo: "deny" },
        },
      ]);
      const result = engine.evaluate("mcp_github_list_repos", "agent-1");
      expect(result.decision).toBe("allow");
    });

    it("respects agent-specific permissions", () => {
      const engine = new MCPPolicyEngine();
      engine.loadServerPermissions([
        {
          serverName: "github",
          agentId: "agent-2",
          permissions: { create_issue: "deny" },
        },
      ]);
      // agent-1 is not affected by agent-2's restrictions
      expect(engine.evaluate("mcp_github_create_issue", "agent-1").decision).toBe("allow");
      // agent-2 is denied
      expect(engine.evaluate("mcp_github_create_issue", "agent-2").decision).toBe("deny");
    });

    it("server permissions take precedence over glob rules", () => {
      const engine = new MCPPolicyEngine([
        { tools: ["mcp_github_*"], decision: "allow" },
      ]);
      engine.loadServerPermissions([
        {
          serverName: "github",
          agentId: null,
          permissions: { delete_repo: "deny" },
        },
      ]);
      // Glob says allow all github, but server perm says deny delete_repo
      expect(engine.evaluate("mcp_github_delete_repo", "agent-1").decision).toBe("deny");
      // Other tools still allowed
      expect(engine.evaluate("mcp_github_create_issue", "agent-1").decision).toBe("allow");
    });

    it("handles servers with no matching tools gracefully", () => {
      const engine = new MCPPolicyEngine();
      engine.loadServerPermissions([
        {
          serverName: "slack",
          agentId: null,
          permissions: { post_message: "deny" },
        },
      ]);
      // Different server, should pass through
      const result = engine.evaluate("mcp_github_create_issue", "agent-1");
      expect(result.decision).toBe("allow");
    });
  });
});
