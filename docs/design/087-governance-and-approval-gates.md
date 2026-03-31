# Design Doc 087: Governance & Approval Gates

**Status:** Draft  
**Author:** Bond  
**Date:** 2026-03-30  
**Depends on:** 081 (Cost Tracking), 084 (Multi-Agent Coordination), 085 (Audit Trails)  
**Inspired by:** Paperclip's board-level governance, approval workflows, and emergency controls

---

## 1. Problem Statement

As Bond gains autonomy — scheduled tasks (083), multi-agent coordination (084), and budget-driven execution (081) — users need **guardrails** to maintain control without micromanaging. Currently:

- **No approval workflow**: Bond either does something or doesn't. There's no "propose and wait for approval" mode for high-risk actions.
- **No configurable risk thresholds**: Users can't say "auto-approve file reads, but ask me before any `git push` or `rm` command."
- **No emergency stop**: If Bond is running a long autonomous workflow that's going sideways, there's no clean way to halt all agent activity.
- **No governance policies**: No way to express organizational rules like "never deploy on Fridays" or "require approval for changes to production configs."

Paperclip solves this by making the user "the board" — agents propose actions, the board approves or rejects, and there are emergency controls to pause everything. Bond needs a similar system that scales from single-user to team use.

---

## 2. Goals

1. **Configurable approval rules** — Users define policies that determine which actions require approval, based on action type, risk level, cost, or target.
2. **Approval queue** — When an action triggers a rule, it's queued for user review with full context (what, why, estimated impact).
3. **Auto-approve for low risk** — Trusted action patterns (file reads, searches, test runs) proceed without interruption.
4. **Emergency controls** — A global kill switch that immediately halts all agent activity, plus per-agent pause/resume.
5. **Policy engine** — A rule-based system that evaluates actions against governance policies before execution.

---

## 3. Proposed Schema

### 3.1 SpacetimeDB Tables

```rust
#[table(name = governance_policy, public)]
pub struct GovernancePolicy {
    #[primary_key]
    pub id: String,
    pub name: String,
    pub description: String,
    pub rule_type: String,          // "action_type", "cost_threshold", "target_pattern", "schedule", "custom"
    pub rule_config: String,        // JSON: the rule definition
    pub action: String,             // "auto_approve", "require_approval", "block"
    pub priority: u16,              // higher priority rules override lower ones
    pub enabled: bool,
    pub created_at: Timestamp,
    pub updated_at: Timestamp,
}

#[table(name = approval_request, public)]
pub struct ApprovalRequest {
    #[primary_key]
    pub id: String,
    pub policy_id: String,
    pub correlation_id: String,         // links to activity trail (085)
    pub conversation_id: String,
    pub agent_id: String,
    pub task_id: Option<String>,        // link to delegated_task (084)
    pub action_type: String,            // "tool_call", "agent_spawn", "git_push", "deployment"
    pub action_detail: String,          // JSON: what exactly is being proposed
    pub risk_assessment: String,        // "low", "medium", "high", "critical"
    pub estimated_cost_usd: Option<f64>,
    pub context_summary: String,        // why the agent wants to do this
    pub status: String,                 // "pending", "approved", "rejected", "expired", "auto_approved"
    pub decided_by: Option<String>,
    pub decision_reason: Option<String>,
    pub expires_at: Timestamp,
    pub created_at: Timestamp,
    pub decided_at: Option<Timestamp>,
}

#[table(name = emergency_control, public)]
pub struct EmergencyControl {
    #[primary_key]
    pub id: String,                     // singleton: "global"
    pub all_agents_paused: bool,
    pub paused_agents: String,          // JSON array of agent IDs individually paused
    pub pause_reason: Option<String>,
    pub paused_by: Option<String>,
    pub paused_at: Option<Timestamp>,
    pub updated_at: Timestamp,
}
```

### 3.2 Rule Configuration Examples

```json
// Block destructive file operations
{
  "rule_type": "action_type",
  "rule_config": {
    "tool_names": ["code_execute"],
    "argument_patterns": ["rm -rf", "DROP TABLE", "DELETE FROM", "format", "fdisk"],
    "match": "any_argument_contains"
  },
  "action": "block"
}

// Require approval for git push
{
  "rule_type": "action_type",
  "rule_config": {
    "tool_names": ["code_execute"],
    "argument_patterns": ["git push", "git force-push"],
    "match": "any_argument_contains"
  },
  "action": "require_approval"
}

// Require approval when spending exceeds $1 in a single task
{
  "rule_type": "cost_threshold",
  "rule_config": {
    "scope": "task",
    "threshold_usd": 1.00
  },
  "action": "require_approval"
}

// Block deployments on Fridays
{
  "rule_type": "schedule",
  "rule_config": {
    "blocked_days": ["friday"],
    "blocked_hours": null,
    "timezone": "America/New_York",
    "applies_to": ["deployment", "git_push"]
  },
  "action": "block"
}

// Require approval for changes to specific paths
{
  "rule_type": "target_pattern",
  "rule_config": {
    "path_patterns": ["**/production/**", "**/.env*", "**/Dockerfile", "**/docker-compose*"],
    "operations": ["write", "delete"]
  },
  "action": "require_approval"
}
```

### 3.3 Reducers

- `create_policy {id, name, description, ruleType, ruleConfig, action, priority, enabled}` — Define a governance policy.
- `update_policy {id, ruleConfig, action, priority, enabled}` — Modify an existing policy.
- `delete_policy {id}` — Remove a policy.
- `submit_approval_request {id, policyId, correlationId, conversationId, agentId, taskId, actionType, actionDetail, riskAssessment, estimatedCostUsd, contextSummary, expiresAt}` — Queue an action for approval.
- `decide_approval {id, status, decidedBy, decisionReason}` — Approve or reject a pending request.
- `set_emergency_control {allAgentsPaused, pausedAgents, pauseReason, pausedBy}` — Global or per-agent pause/resume.

---

## 4. Architecture

### 4.1 Policy Evaluation Engine

Every action passes through the policy engine before execution:

```python
class PolicyEngine:
    """Evaluates actions against governance policies."""
    
    async def evaluate(self, action: ProposedAction) -> PolicyDecision:
        """Check all applicable policies and return the highest-priority decision."""
        policies = await get_enabled_policies()
        
        # Check emergency controls first
        emergency = await get_emergency_control()
        if emergency.all_agents_paused:
            return PolicyDecision.BLOCKED, f"All agents paused: {emergency.pause_reason}"
        if action.agent_id in json.loads(emergency.paused_agents):
            return PolicyDecision.BLOCKED, f"Agent {action.agent_id} is paused"
        
        # Evaluate policies in priority order (highest first)
        applicable = []
        for policy in sorted(policies, key=lambda p: p.priority, reverse=True):
            if self._matches(policy, action):
                applicable.append(policy)
        
        if not applicable:
            return PolicyDecision.AUTO_APPROVED, "No matching policies"
        
        # Highest priority policy wins
        top_policy = applicable[0]
        
        if top_policy.action == "block":
            return PolicyDecision.BLOCKED, f"Blocked by policy '{top_policy.name}'"
        
        if top_policy.action == "require_approval":
            request_id = await self._create_approval_request(top_policy, action)
            return PolicyDecision.PENDING_APPROVAL, request_id
        
        return PolicyDecision.AUTO_APPROVED, f"Auto-approved by policy '{top_policy.name}'"
    
    def _matches(self, policy: GovernancePolicy, action: ProposedAction) -> bool:
        """Check if a policy applies to this action."""
        config = json.loads(policy.rule_config)
        
        if policy.rule_type == "action_type":
            if action.tool_name not in config.get("tool_names", []):
                return False
            patterns = config.get("argument_patterns", [])
            if config.get("match") == "any_argument_contains":
                args_str = json.dumps(action.arguments).lower()
                return any(p.lower() in args_str for p in patterns)
            return True
        
        if policy.rule_type == "cost_threshold":
            current_cost = action.accumulated_cost_usd or 0
            return current_cost >= config["threshold_usd"]
        
        if policy.rule_type == "target_pattern":
            from fnmatch import fnmatch
            target = action.target_path or ""
            return any(fnmatch(target, pat) for pat in config.get("path_patterns", []))
        
        if policy.rule_type == "schedule":
            from datetime import datetime
            import pytz
            tz = pytz.timezone(config.get("timezone", "UTC"))
            now = datetime.now(tz)
            day_name = now.strftime("%A").lower()
            return day_name in [d.lower() for d in config.get("blocked_days", [])]
        
        return False
```

### 4.2 Approval Queue Flow

```
Agent wants to execute action
         │
         ▼
┌─────────────────┐
│  Policy Engine   │
│  evaluate()      │
└────────┬────────┘
         │
    ┌────┴────┬──────────┐
    ▼         ▼          ▼
 AUTO_APPROVED  PENDING   BLOCKED
    │          │          │
    ▼          ▼          ▼
 Execute    Queue for    Return error
 immediately  user review  to agent
              │
              ▼
        ┌──────────┐
        │ Frontend  │
        │ Approval  │  ← User sees notification
        │ Queue     │     with full context
        └────┬─────┘
             │
        ┌────┴────┐
        ▼         ▼
     APPROVED   REJECTED
        │         │
        ▼         ▼
     Execute    Agent informed,
     action     suggests alternative
```

### 4.3 Agent-Side Integration

```python
async def execute_with_governance(tool_name: str, args: dict, context: AgentContext) -> Any:
    """Execute a tool call with governance policy enforcement."""
    proposed = ProposedAction(
        tool_name=tool_name,
        arguments=args,
        agent_id=context.agent_id,
        conversation_id=context.conversation_id,
        task_id=context.task_id,
        target_path=args.get("path"),
        accumulated_cost_usd=context.accumulated_cost,
    )
    
    decision, detail = await policy_engine.evaluate(proposed)
    
    if decision == PolicyDecision.BLOCKED:
        logger.warning("Action blocked by governance: %s", detail)
        raise GovernanceBlockedError(detail)
    
    if decision == PolicyDecision.PENDING_APPROVAL:
        approval_id = detail
        logger.info("Action pending approval: %s (request %s)", tool_name, approval_id)
        
        await notify_user(
            context.conversation_id,
            f"⏳ Approval required: {tool_name}\nWaiting for your review in the approval queue."
        )
        
        result = await wait_for_approval(approval_id, timeout_seconds=300)
        
        if result.status == "approved":
            logger.info("Action approved by %s: %s", result.decided_by, tool_name)
        elif result.status == "rejected":
            raise GovernanceRejectedError(
                f"Action rejected by {result.decided_by}: {result.decision_reason}"
            )
        elif result.status == "expired":
            raise GovernanceExpiredError(
                "Approval request expired after 5 minutes. Re-run to try again."
            )
    
    return await execute_tool(tool_name, args)
```

### 4.4 Emergency Controls UI

```
┌─────────────────────────────────────────────┐
│ 🛡️ Governance Controls                      │
├─────────────────────────────────────────────┤
│                                             │
│  ⏸️ Emergency Stop                          │
│  ┌─────────────────────────────────────┐    │
│  │  🔴 PAUSE ALL AGENTS               │    │
│  └─────────────────────────────────────┘    │
│                                             │
│  Active Agents:                             │
│  ├─ claude-main    ● Running   [⏸ Pause]   │
│  ├─ codex-worker-1 ● Running   [⏸ Pause]   │
│  └─ claude-review  ○ Idle                   │
│                                             │
│  Pending Approvals (2):                     │
│  ┌─────────────────────────────────────┐    │
│  │ git push origin feature/auth-fix    │    │
│  │ Agent: claude-main | Risk: medium   │    │
│  │ Policy: "Require approval for push" │    │
│  │  [✅ Approve]  [❌ Reject]          │    │
│  └─────────────────────────────────────┘    │
│                                             │
│  Active Policies (5):                       │
│  ├─ Block destructive ops     [✓] p:100    │
│  ├─ Require approval for push [✓] p:90     │
│  ├─ Cost gate > $1/task       [✓] p:80     │
│  ├─ No Friday deploys         [✓] p:70     │
│  └─ Protect production paths  [✓] p:60     │
│                                             │
└─────────────────────────────────────────────┘
```

---

## 5. Default Policies

Bond ships with sensible defaults that users can customize:

| Policy | Type | Action | Priority |
|--------|------|--------|----------|
| Block `rm -rf /`, `DROP DATABASE`, `format` | action_type | block | 100 |
| Block force-push to main/master | action_type | block | 95 |
| Require approval for `git push` | action_type | require_approval | 90 |
| Require approval when task cost > $1 | cost_threshold | require_approval | 80 |
| Require approval for production path changes | target_pattern | require_approval | 70 |
| Auto-approve file reads, searches, grep | action_type | auto_approve | 10 |

---

## 6. Interaction with Existing Systems

| System | Integration |
|--------|------------|
| Cost tracking (081) | Cost threshold policies use accumulated spend from cost ledger |
| Multi-agent coordination (084) | Each agent's actions are independently evaluated; parent agent can set sub-agent policies |
| Audit trails (085) | Every policy evaluation, approval request, and decision is logged as an activity event |
| Heartbeat/scheduled tasks (083) | Scheduled tasks respect governance policies; overnight autonomous work can be gated |
| Circuit breakers (070) | Circuit breakers are the fast, automatic safety net; governance gates are the deliberate, user-controlled layer |
| AGENTS.md | Existing AGENTS.md rules are migrated into governance policies for runtime enforcement |

---

## 7. Migration Path

1. **Phase 1**: Policy engine + default policies. All tool calls pass through `evaluate()`. Default action for unmatched: auto-approve. No UI yet — policies configured via reducers.
2. **Phase 2**: Approval queue — pending requests stored in SpacetimeDB, agent waits for decision. CLI-based approval (`bond approve <id>`).
3. **Phase 3**: Frontend governance panel — approval queue, policy management, emergency controls.
4. **Phase 4**: Emergency controls — global pause button, per-agent pause/resume, auto-pause on anomaly detection.
5. **Phase 5**: Smart defaults — Bond learns from approval patterns and suggests policy refinements.

---

## 8. Open Questions

- How long should approval requests wait before expiring? Too short and the user misses them; too long and the agent is blocked forever. Should it be configurable per policy?
- Should there be a "trust escalation" model? (After 10 approved pushes, auto-approve future pushes.) This reduces friction but could be exploited.
- How do we handle approval when the user is offline? Queue indefinitely? Send a push notification? Auto-reject after timeout?
- Should policies be shareable/exportable? Teams might want a standard governance profile they apply across all Bond instances.
- How does the emergency stop propagate to coding agents running in separate Docker containers? We'd need a coordination mechanism (kill the container? Send a signal via SpacetimeDB?).
