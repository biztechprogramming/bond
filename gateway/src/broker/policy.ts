/**
 * Permission Broker — hardcoded policy engine (Phase 1).
 *
 * Converts glob patterns to regex at construction time. First match wins.
 * Default deny if no rule matches.
 */

import type { PolicyRule, PolicyDecision } from "./types.js";

interface CompiledRule {
  patterns: RegExp[];
  cwdPatterns?: RegExp[];
  decision: "allow" | "deny" | "prompt";
  reason?: string;
  timeout?: number;
  index: number;
  policyName: string;
}

function globToRegex(glob: string): RegExp {
  let regex = "";
  for (let i = 0; i < glob.length; i++) {
    const ch = glob[i];
    if (ch === "*") {
      regex += ".*";
    } else if (ch === "?") {
      regex += ".";
    } else if ("[{()+^$|\\.]".includes(ch)) {
      regex += "\\" + ch;
    } else {
      regex += ch;
    }
  }
  return new RegExp("^" + regex + "$");
}

const DEFAULT_RULES: PolicyRule[] = [
  // Git read-only
  { commands: ["git status*", "git log*", "git diff*", "git branch*", "git show*", "git rev-parse*"], decision: "allow" },
  // Git branch/commit
  { commands: ["git checkout*", "git switch*", "git add*", "git commit*", "git stash*"], decision: "allow" },
  // Git push to feature/fix branches
  { commands: ["git push*feat/*", "git push*fix/*", "git push*agent/*"], decision: "allow" },
  // Git push to protected branches — deny
  { commands: ["git push*main*", "git push*master*"], decision: "deny", reason: "Direct push to protected branches is not allowed" },
  // Git push catch-all — prompt
  { commands: ["git push*"], decision: "prompt", timeout: 120 },
  // GitHub CLI — PR operations
  { commands: ["gh pr create*", "gh pr list*", "gh pr view*", "gh pr status*"], decision: "allow" },
  // GitHub CLI — merge requires approval
  { commands: ["gh pr merge*"], decision: "prompt", timeout: 120 },
  // Build and test tools
  {
    commands: [
      "npm test*", "npm run build*", "npm run lint*", "npm run dev*",
      "npx vitest*", "npx tsc*",
      "uv run*", "python -m pytest*", "python -m mypy*",
      "make *",
      "cargo test*", "cargo build*",
      "go test*", "go build*",
    ],
    decision: "allow",
  },
  // Package install — prompt
  { commands: ["npm install*", "npm ci*", "pip install*", "uv pip install*", "apt*"], decision: "prompt", timeout: 120 },
  // Read-only filesystem/info commands
  {
    commands: [
      "ls *", "cat *", "head *", "tail *", "grep *", "find *", "wc *",
      "tree *", "file *", "stat *", "du *", "df *",
      "which *", "env", "pwd", "whoami", "uname*", "date",
    ],
    decision: "allow",
  },
  // File mutation — allow in workspace paths
  { commands: ["mkdir *", "cp *", "mv *", "touch *"], decision: "allow", cwd: ["/home/*/bond*", "/workspace/*"] },
  // Dangerous commands — deny
  {
    commands: [
      "rm -rf /*", "rm -rf /",
      "chmod 777*", "chown*",
      "curl*", "wget*",
      "sudo*",
      "docker rm*", "docker stop*", "docker kill*",
      "kill*", "killall*",
      "shutdown*", "reboot*",
    ],
    decision: "deny",
    reason: "Command is on the deny list",
  },
  // Catch-all deny
  { commands: ["*"], decision: "deny", reason: "Command not in allowlist" },
];

export class PolicyEngine {
  private compiled: CompiledRule[];

  constructor() {
    this.compiled = DEFAULT_RULES.map((rule, index) => ({
      patterns: rule.commands.map(globToRegex),
      cwdPatterns: rule.cwd?.map(globToRegex),
      decision: rule.decision,
      reason: rule.reason,
      timeout: rule.timeout,
      index,
      policyName: "default",
    }));
  }

  evaluate(command: string, cwd: string | undefined, _agentId: string, _sessionId: string): PolicyDecision {
    for (const rule of this.compiled) {
      const commandMatches = rule.patterns.some((p) => p.test(command));
      if (!commandMatches) continue;

      // If rule has cwd constraints, check them
      if (rule.cwdPatterns && cwd) {
        const cwdMatches = rule.cwdPatterns.some((p) => p.test(cwd));
        if (!cwdMatches) continue;
      } else if (rule.cwdPatterns && !cwd) {
        // Rule requires cwd but none provided — skip
        continue;
      }

      return {
        decision: rule.decision,
        reason: rule.reason,
        timeout: rule.timeout,
        source: `${rule.policyName}#rule-${rule.index}`,
      };
    }

    return {
      decision: "deny",
      reason: "No matching policy rule",
      source: "default#implicit-deny",
    };
  }
}
