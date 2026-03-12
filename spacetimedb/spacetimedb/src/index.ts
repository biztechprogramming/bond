import { schema, table, t } from 'spacetimedb/server';

/**
 * Bond SpacetimeDB Module
 *
 * Central source of truth for Agents, Models, MCP Servers,
 * Conversations, and Message History.
 */

const spacetimedb = schema({
  // -- Global Agent Definitions --
  agents: table(
    { public: true },
    {
      id: t.string().primaryKey(),
      name: t.string(),
      displayName: t.string(),
      systemPrompt: t.string(),
      model: t.string(),
      utilityModel: t.string(),
      tools: t.string(), // JSON array of enabled tool names
      sandboxImage: t.string(),
      maxIterations: t.u32(),
      isActive: t.bool(),
      isDefault: t.bool(),
      createdAt: t.u64(),
    }
  ),

  agent_workspace_mounts: table(
    { public: true },
    {
      id: t.string().primaryKey(),
      agentId: t.string(),
      hostPath: t.string(),
      mountName: t.string(),
      containerPath: t.string(),
      readonly: t.bool(),
    }
  ),

  agent_channels: table(
    { public: true },
    {
      id: t.string().primaryKey(),
      agentId: t.string(),
      channel: t.string(),
      sandboxOverride: t.string(),
      enabled: t.bool(),
      createdAt: t.u64(),
    }
  ),

  // -- Conversations --
  conversations: table(
    { public: true },
    {
      id: t.string().primaryKey(),
      agentId: t.string(),
      channel: t.string(),
      title: t.string(),
      isActive: t.bool(),
      messageCount: t.u32(),
      rollingSummary: t.string(),
      summaryCoversto: t.u32(),
      recentToolsUsed: t.string(), // JSON array
      createdAt: t.u64(),
      updatedAt: t.u64(),
    }
  ),

  // -- Conversation Messages --
  conversationMessages: table(
    { public: true },
    {
      id: t.string().primaryKey(),
      conversationId: t.string(),
      role: t.string(), // 'user', 'assistant', 'system', 'tool'
      content: t.string(),
      toolCalls: t.string(), // JSON or empty
      toolCallId: t.string(),
      tokenCount: t.u32(),
      status: t.string(), // 'queued', 'delivered'
      createdAt: t.u64(),
    }
  ),

  // -- Worker Message History (shadow writes from agent workers) --
  messages: table(
    { public: true },
    {
      id: t.string().primaryKey(),
      agentId: t.string(),
      sessionId: t.string(),
      role: t.string(),
      content: t.string(),
      metadata: t.string(),
      createdAt: t.u64(),
    }
  ),

  // -- Tool Execution Logs --
  tool_logs: table(
    { public: true },
    {
      id: t.string().primaryKey(),
      agentId: t.string(),
      sessionId: t.string(),
      toolName: t.string(),
      input: t.string(),
      output: t.string(),
      duration: t.u32(),
      createdAt: t.u64(),
    }
  ),

  // -- LLM Providers --
  providers: table(
    { public: true },
    {
      id: t.string().primaryKey(),
      displayName: t.string(),
      litellmPrefix: t.string(),
      apiBaseUrl: t.string().optional(),
      modelsEndpoint: t.string().optional(),
      modelsFetchMethod: t.string(), // 'anthropic_api', 'anthropic_scrape', 'google_api', 'openai_compat'
      authType: t.string(), // 'bearer', 'x-api-key', 'query_param'
      isEnabled: t.bool(),
      config: t.string(), // JSON object
      createdAt: t.u64(),
      updatedAt: t.u64(),
    }
  ),

  // -- Provider API Keys (encrypted) --
  provider_api_keys: table(
    { public: true },
    {
      providerId: t.string().primaryKey(),
      encryptedValue: t.string(),
      keyType: t.string(), // 'api_key', 'oauth_token'
      createdAt: t.u64(),
      updatedAt: t.u64(),
    }
  ),

  // -- Provider Aliases (e.g., gemini → google, claude → anthropic) --
  provider_aliases: table(
    { public: true },
    {
      alias: t.string().primaryKey(),
      providerId: t.string(),
    }
  ),

  // -- Model Catalog --
  llm_models: table(
    { public: true },
    {
      id: t.string().primaryKey(),
      provider: t.string(),
      modelId: t.string(),
      displayName: t.string(),
      contextWindow: t.u32(),
      isEnabled: t.bool(),
    }
  ),

  // -- MCP Servers --
  mcp_servers: table(
    { public: true },
    {
      id: t.string().primaryKey(),
      name: t.string(),
      command: t.string(),
      args: t.string(), // JSON array
      env: t.string(), // JSON object
      enabled: t.bool(),
      agentId: t.string().optional(), // NULL/empty means global
      createdAt: t.u64(),
      updatedAt: t.u64(),
    }
  ),

  // -- Settings --
  settings: table(
    { public: true },
    {
      key: t.string().primaryKey(),
      value: t.string(),
      keyType: t.string().default("api_key"),
      createdAt: t.u64(),
      updatedAt: t.u64(),
    }
  ),

  // -- Work Plans (Kanban Board) --
  workPlans: table(
    { public: true },
    {
      id: t.string().primaryKey(),
      agentId: t.string(),
      conversationId: t.string(),
      parentPlanId: t.string().default(''),
      title: t.string(),
      status: t.string(), // 'active', 'completed', 'cancelled'
      createdAt: t.u64(),
      updatedAt: t.u64(),
      completedAt: t.u64().optional(),
    }
  ),

  // -- Work Items --
  workItems: table(
    { public: true },
    {
      id: t.string().primaryKey(),
      planId: t.string(),
      title: t.string(),
      status: t.string(), // 'new', 'in_progress', 'done', 'blocked'
      ordinal: t.u32(),
      contextSnapshot: t.string().default('{}'), // JSON object
      notes: t.string().default('[]'), // JSON array of strings
      filesChanged: t.string().default('[]'), // JSON array
      startedAt: t.u64().optional(),
      completedAt: t.u64().optional(),
      createdAt: t.u64(),
      updatedAt: t.u64(),
      description: t.string().default(''), // execution context: codebase, file paths, approach
    }
  ),

  // -- Prompt Fragments --
  prompt_fragments: table(
    { public: true },
    {
      id: t.string().primaryKey(),
      name: t.string(),
      display_name: t.string(),
      category: t.string(),
      content: t.string(),
      description: t.string().default(''),
      is_active: t.bool().default(true),
      is_system: t.bool().default(false),
      summary: t.string().default(''),
      tier: t.string().default('standard'),
      task_triggers: t.string().default('[]'), // JSON array
      token_estimate: t.u32().default(0),
      created_at: t.u64(),
      updated_at: t.u64(),
    }
  ),

  // -- Prompt Fragment Versions --
  prompt_fragment_versions: table(
    { public: true },
    {
      id: t.string().primaryKey(),
      fragment_id: t.string(),
      version: t.u32(),
      content: t.string(),
      change_reason: t.string().default(''),
      changed_by: t.string().default('user'),
      created_at: t.u64(),
    }
  ),

  // -- Agent Prompt Fragment Attachments --
  agent_prompt_fragments: table(
    { public: true },
    {
      id: t.string().primaryKey(),
      agent_id: t.string(),
      fragment_id: t.string(),
      rank: t.u32().default(0),
      enabled: t.bool().default(true),
      created_at: t.u64(),
    }
  ),

  // -- Prompt Templates --
  prompt_templates: table(
    { public: true },
    {
      id: t.string().primaryKey(),
      name: t.string(),
      display_name: t.string(),
      category: t.string(),
      content: t.string(),
      variables: t.string().default('[]'), // JSON array
      description: t.string().default(''),
      is_active: t.bool().default(true),
      created_at: t.u64(),
      updated_at: t.u64(),
    }
  ),

  // -- Prompt Template Versions --
  prompt_template_versions: table(
    { public: true },
    {
      id: t.string().primaryKey(),
      template_id: t.string(),
      version: t.u32(),
      content: t.string(),
      change_reason: t.string().default(''),
      changed_by: t.string().default('user'),
      created_at: t.u64(),
    }
  ),

  // ─── Deployment Agent Tables (Design Doc 039) ─────────────────────────────

  // -- Deployment Environments --
  deployment_environments: table(
    { public: true },
    {
      name: t.string().primaryKey(),        // 'dev', 'qa', 'staging', 'uat', 'prod'
      display_name: t.string(),             // 'Development', 'QA', etc.
      order: t.u32(),                       // promotion order (1 = first)
      is_active: t.bool(),                  // soft delete

      // Deployment settings
      max_script_timeout: t.u32(),          // seconds (default 600)
      health_check_interval: t.u32(),       // seconds (default 300)

      // Deployment window (empty string = no restrictions)
      window_days: t.string().default('[]'),    // JSON array: '["mon","tue","wed","thu","fri"]'
      window_start: t.string().default(''),     // "06:00" or ""
      window_end: t.string().default(''),       // "22:00" or ""
      window_timezone: t.string().default(''),  // "America/New_York"

      // Approvals
      required_approvals: t.u32().default(1),  // how many approvers needed

      created_at: t.u64(),
      updated_at: t.u64(),
    }
  ),

  // -- Deployment Environment Approvers --
  deployment_environment_approvers: table(
    { public: true },
    {
      id: t.string().primaryKey(),           // ULID
      environment_name: t.string(),
      user_id: t.string(),                   // Bond user ID or GitHub username
      added_at: t.u64(),
      added_by: t.string(),
    }
  ),

  // -- Deployment Environment History --
  deployment_environment_history: table(
    { public: true },
    {
      id: t.string().primaryKey(),           // ULID
      environment_name: t.string(),
      action: t.string(),                    // 'created', 'updated', 'deactivated', 'reactivated'
      changed_by: t.string(),
      changed_at: t.u64(),
      before_state: t.string().default(''), // JSON snapshot
      after_state: t.string(),              // JSON snapshot
    }
  ),

  // -- Deployment Promotions --
  deployment_promotions: table(
    { public: true },
    {
      id: t.string().primaryKey(),           // ULID
      script_id: t.string(),
      script_version: t.string(),
      script_sha256: t.string(),
      environment_name: t.string(),

      // Status: 'not_promoted', 'awaiting_approvals', 'promoted',
      //         'deploying', 'success', 'failed', 'rolled_back'
      status: t.string(),

      initiated_by: t.string(),
      initiated_at: t.u64(),
      promoted_at: t.u64().default(0n),
      deployed_at: t.u64().default(0n),
      receipt_id: t.string().default(''),
    }
  ),

  // -- Deployment Approvals --
  deployment_approvals: table(
    { public: true },
    {
      id: t.string().primaryKey(),           // ULID
      promotion_id: t.string(),              // FK → deployment_promotions.id
      script_id: t.string(),
      script_version: t.string(),
      environment_name: t.string(),
      user_id: t.string(),
      approved_at: t.u64(),
    }
  ),

  // -- System Events (background task completions, notifications) --
  system_events: table(
    { public: true },
    {
      id: t.string().primaryKey(),
      conversationId: t.string(),
      agentId: t.string(),
      eventType: t.string(),     // "coding_agent_done", "coding_agent_failed", etc.
      summary: t.string(),       // human-readable summary
      metadata: t.string(),      // JSON string with structured data
      consumed: t.bool(),
      createdAt: t.u64(),
    }
  ),
});

export default spacetimedb;

// ===============================================================
// Reducers
// ===============================================================

// -- Models --

export const addModel = spacetimedb.reducer(
  {
    id: t.string(),
    provider: t.string(),
    modelId: t.string(),
    displayName: t.string(),
    contextWindow: t.u32(),
    isEnabled: t.bool(),
  },
  (ctx, model) => {
    ctx.db.llm_models.insert(model);
  }
);

// -- Agents --

export const addAgent = spacetimedb.reducer(
  {
    id: t.string(),
    name: t.string(),
    displayName: t.string(),
    systemPrompt: t.string(),
    model: t.string(),
    utilityModel: t.string(),
    tools: t.string(),
    sandboxImage: t.string(),
    maxIterations: t.u32(),
    isActive: t.bool(),
    isDefault: t.bool(),
  },
  (ctx, agent) => {
    ctx.db.agents.insert({
      ...agent,
      createdAt: BigInt(Date.now()),
    });
  }
);

export const addAgentMount = spacetimedb.reducer(
  {
    id: t.string(),
    agentId: t.string(),
    hostPath: t.string(),
    mountName: t.string(),
    containerPath: t.string(),
    readonly: t.bool(),
  },
  (ctx, mount) => {
    ctx.db.agent_workspace_mounts.insert(mount);
  }
);

// -- Conversations --

export const createConversation = spacetimedb.reducer(
  {
    id: t.string(),
    agentId: t.string(),
    channel: t.string(),
    title: t.string(),
  },
  (ctx, conv) => {
    const now = BigInt(Date.now());
    ctx.db.conversations.insert({
      ...conv,
      isActive: true,
      messageCount: 0,
      rollingSummary: '',
      summaryCoversto: 0,
      recentToolsUsed: '[]',
      createdAt: now,
      updatedAt: now,
    });
  }
);

export const updateConversation = spacetimedb.reducer(
  {
    id: t.string(),
    title: t.string(),
  },
  (ctx, args) => {
    const conv = ctx.db.conversations.id.find(args.id);
    if (!conv) return;
    ctx.db.conversations.id.update({
      ...conv,
      title: args.title,
      updatedAt: BigInt(Date.now()),
    });
  }
);

export const deleteConversation = spacetimedb.reducer(
  {
    id: t.string(),
  },
  (ctx, args) => {
    const conv = ctx.db.conversations.id.find(args.id);
    if (conv) {
      ctx.db.conversations.id.delete(args.id);
    }
    // Delete all messages for this conversation
    for (const msg of ctx.db.conversationMessages.iter()) {
      if (msg.conversationId === args.id) {
        ctx.db.conversationMessages.id.delete(msg.id);
      }
    }
  }
);

// -- Conversation Messages --

export const addConversationMessage = spacetimedb.reducer(
  {
    id: t.string(),
    conversationId: t.string(),
    role: t.string(),
    content: t.string(),
    toolCalls: t.string(),
    toolCallId: t.string(),
    tokenCount: t.u32(),
    status: t.string(),
  },
  (ctx, msg) => {
    ctx.db.conversationMessages.insert({
      ...msg,
      createdAt: BigInt(Date.now()),
    });
    // Update conversation message count and timestamp
    const conv = ctx.db.conversations.id.find(msg.conversationId);
    if (conv) {
      ctx.db.conversations.id.update({
        ...conv,
        messageCount: conv.messageCount + 1,
        updatedAt: BigInt(Date.now()),
      });
    }
  }
);

export const deleteConversationMessage = spacetimedb.reducer(
  {
    id: t.string(),
    conversationId: t.string(),
  },
  (ctx, args) => {
    const msg = ctx.db.conversationMessages.id.find(args.id);
    if (msg) {
      ctx.db.conversationMessages.id.delete(args.id);
      // Decrement message count
      const conv = ctx.db.conversations.id.find(args.conversationId);
      if (conv && conv.messageCount > 0) {
        ctx.db.conversations.id.update({
          ...conv,
          messageCount: conv.messageCount - 1,
          updatedAt: BigInt(Date.now()),
        });
      }
    }
  }
);

// -- Worker Messages (shadow writes) --

export const saveMessage = spacetimedb.reducer(
  {
    id: t.string(),
    agentId: t.string(),
    sessionId: t.string(),
    role: t.string(),
    content: t.string(),
    metadata: t.string(),
  },
  (ctx, msg) => {
    ctx.db.messages.insert({
      ...msg,
      createdAt: BigInt(Date.now()),
    });
  }
);

// -- Tool Logs --

export const logTool = spacetimedb.reducer(
  {
    id: t.string(),
    agentId: t.string(),
    sessionId: t.string(),
    toolName: t.string(),
    input: t.string(),
    output: t.string(),
    duration: t.u32(),
  },
  (ctx, log) => {
    ctx.db.tool_logs.insert({
      ...log,
      createdAt: BigInt(Date.now()),
    });
  }
);

// -- Bulk Import (for data migration) --

export const importConversation = spacetimedb.reducer(
  {
    id: t.string(),
    agentId: t.string(),
    channel: t.string(),
    title: t.string(),
    isActive: t.bool(),
    messageCount: t.u32(),
    rollingSummary: t.string(),
    summaryCoversto: t.u32(),
    recentToolsUsed: t.string(),
    createdAt: t.u64(),
    updatedAt: t.u64(),
  },
  (ctx, conv) => {
    ctx.db.conversations.insert(conv);
  }
);

export const importConversationMessage = spacetimedb.reducer(
  {
    id: t.string(),
    conversationId: t.string(),
    role: t.string(),
    content: t.string(),
    toolCalls: t.string(),
    toolCallId: t.string(),
    tokenCount: t.u32(),
    status: t.string(),
    createdAt: t.u64(),
  },
  (ctx, msg) => {
    ctx.db.conversationMessages.insert(msg);
  }
);

// -- MCP Servers --

export const addMcpServer = spacetimedb.reducer(
  {
    id: t.string(),
    name: t.string(),
    command: t.string(),
    args: t.string(),
    env: t.string(),
    agentId: t.string().optional(),
  },
  (ctx, server) => {
    const now = BigInt(Date.now());
    ctx.db.mcp_servers.insert({
      ...server,
      enabled: true,
      createdAt: now,
      updatedAt: now,
    });
  }
);

export const updateMcpServer = spacetimedb.reducer(
  {
    id: t.string(),
    name: t.string(),
    command: t.string(),
    args: t.string(),
    env: t.string(),
    enabled: t.bool(),
    agentId: t.string().optional(),
  },
  (ctx, server) => {
    const existing = ctx.db.mcp_servers.id.find(server.id);
    if (!existing) return;
    ctx.db.mcp_servers.id.update({
      ...server,
      createdAt: existing.createdAt,
      updatedAt: BigInt(Date.now()),
    });
  }
);

export const deleteMcpServer = spacetimedb.reducer(
  {
    id: t.string(),
  },
  (ctx, args) => {
    ctx.db.mcp_servers.id.delete(args.id);
  }
);

// -- Settings --

export const setSetting = spacetimedb.reducer(
  {
    key: t.string(),
    value: t.string(),
    keyType: t.string().default("api_key"),
  },
  (ctx, args) => {
    const now = BigInt(Date.now());
    const existing = ctx.db.settings.key.find(args.key);
    if (existing) {
      ctx.db.settings.key.update({
        ...args,
        createdAt: existing.createdAt,
        updatedAt: now,
      });
    } else {
      ctx.db.settings.insert({
        ...args,
        createdAt: now,
        updatedAt: now,
      });
    }
  }
);

export const deleteSetting = spacetimedb.reducer(
  {
    key: t.string(),
  },
  (ctx, args) => {
    ctx.db.settings.key.delete(args.key);
  }
);

// -- Work Plans --

export const createWorkPlan = spacetimedb.reducer(
  {
    id: t.string(),
    agentId: t.string(),
    conversationId: t.string(),
    parentPlanId: t.string().default(''),
    title: t.string(),
  },
  (ctx, plan) => {
    const now = BigInt(Date.now());
    ctx.db.workPlans.insert({
      ...plan,
      status: 'active',
      createdAt: now,
      updatedAt: now,
      completedAt: undefined,
    });
  }
);

export const updateWorkPlanStatus = spacetimedb.reducer(
  {
    id: t.string(),
    status: t.string(),
  },
  (ctx, args) => {
    const plan = ctx.db.workPlans.id.find(args.id);
    if (!plan) return;
    const now = BigInt(Date.now());
    ctx.db.workPlans.id.update({
      ...plan,
      status: args.status,
      updatedAt: now,
      completedAt: args.status === 'completed' ? now : plan.completedAt,
    });
  }
);

export const deleteWorkPlan = spacetimedb.reducer(
  { id: t.string() },
  (ctx, args) => {
    // Delete all items belonging to this plan
    for (const item of ctx.db.workItems.iter()) {
      if (item.planId === args.id) {
        ctx.db.workItems.id.delete(item.id);
      }
    }
    ctx.db.workPlans.id.delete(args.id);
  }
);

// -- Work Items --

export const addWorkItem = spacetimedb.reducer(
  {
    id: t.string(),
    planId: t.string(),
    title: t.string(),
    ordinal: t.u32(),
    description: t.string(),
  },
  (ctx, item) => {
    const now = BigInt(Date.now());
    ctx.db.workItems.insert({
      ...item,
      status: 'new',
      contextSnapshot: '{}',
      notes: '[]',
      filesChanged: '[]',
      startedAt: undefined,
      completedAt: undefined,
      createdAt: now,
      updatedAt: now,
    });
    // Bump parent plan's updatedAt so sorting always reflects latest activity
    const plan = ctx.db.workPlans.id.find(item.planId);
    if (plan) ctx.db.workPlans.id.update({ ...plan, updatedAt: now });
  }
);

export const renameWorkItem = spacetimedb.reducer(
  {
    id: t.string(),
    title: t.string(),
  },
  (ctx, args) => {
    const item = ctx.db.workItems.id.find(args.id);
    if (!item) return;
    const now = BigInt(Date.now());
    ctx.db.workItems.id.update({ ...item, title: args.title, updatedAt: now });
    const plan = ctx.db.workPlans.id.find(item.planId);
    if (plan) ctx.db.workPlans.id.update({ ...plan, updatedAt: now });
  }
);

export const updateWorkItem = spacetimedb.reducer(
  {
    id: t.string(),
    status: t.string(),
    notes: t.string().optional(),
    filesChanged: t.string().optional(),
    description: t.string().optional(),
  },
  (ctx, args) => {
    const item = ctx.db.workItems.id.find(args.id);
    if (!item) return;
    const now = BigInt(Date.now());
    ctx.db.workItems.id.update({
      ...item,
      status: args.status,
      notes: args.notes ?? item.notes,
      filesChanged: args.filesChanged ?? item.filesChanged,
      description: args.description ?? item.description,
      updatedAt: now,
    });
    // Bump parent plan's updatedAt so sorting always reflects latest activity
    const plan = ctx.db.workPlans.id.find(item.planId);
    if (plan) ctx.db.workPlans.id.update({ ...plan, updatedAt: now });
  }
);

// -- Import (Data Sync) --

export const importWorkItem = spacetimedb.reducer(
  {
    id: t.string(),
    planId: t.string(),
    title: t.string(),
    status: t.string(),
    ordinal: t.u32(),
    contextSnapshot: t.string().default('{}'),
    notes: t.string().default('[]'),
    filesChanged: t.string().default('[]'),
    startedAt: t.u64().optional(),
    completedAt: t.u64().optional(),
    createdAt: t.u64(),
    updatedAt: t.u64(),
    description: t.string().default(''),
  },
  (ctx, item) => {
    const existing = ctx.db.workItems.id.find(item.id);
    if (existing) {
      ctx.db.workItems.id.update({
        ...existing,
        ...item,
        title: item.title === "" ? existing.title : item.title,
        ordinal: item.ordinal === 0 ? existing.ordinal : item.ordinal,
        createdAt: item.createdAt === 0n ? existing.createdAt : item.createdAt,
      });
    } else {
      ctx.db.workItems.insert(item);
    }
  }
);

export const importWorkPlan = spacetimedb.reducer(
  {
    id: t.string(),
    agentId: t.string(),
    conversationId: t.string(),
    parentPlanId: t.string().default(''),
    title: t.string(),
    status: t.string(),
    createdAt: t.u64(),
    updatedAt: t.u64(),
    completedAt: t.u64().optional(),
  },
  (ctx, plan) => {
    const existing = ctx.db.workPlans.id.find(plan.id);
    if (existing) {
      ctx.db.workPlans.id.update({
        ...existing,
        ...plan,
        createdAt: plan.createdAt === 0n ? existing.createdAt : plan.createdAt,
      });
    } else {
      ctx.db.workPlans.insert(plan);
    }
  }
);

// -- Providers --

export const addProvider = spacetimedb.reducer(
  {
    id: t.string(),
    displayName: t.string(),
    litellmPrefix: t.string(),
    apiBaseUrl: t.string().optional(),
    modelsEndpoint: t.string().optional(),
    modelsFetchMethod: t.string(),
    authType: t.string(),
    isEnabled: t.bool(),
    config: t.string(),
    createdAt: t.u64(),
    updatedAt: t.u64(),
  },
  (ctx, provider) => {
    ctx.db.providers.insert(provider);
  }
);

export const updateProvider = spacetimedb.reducer(
  {
    id: t.string(),
    displayName: t.string().optional(),
    litellmPrefix: t.string().optional(),
    apiBaseUrl: t.string().optional(),
    modelsEndpoint: t.string().optional(),
    modelsFetchMethod: t.string().optional(),
    authType: t.string().optional(),
    isEnabled: t.bool().optional(),
    config: t.string().optional(),
    updatedAt: t.u64(),
  },
  (ctx, updates) => {
    const existing = ctx.db.providers.id.find(updates.id);
    if (!existing) {
      return;
    }
    // Merge only defined fields
    const merged = { ...existing };
    if (updates.displayName !== undefined) merged.displayName = updates.displayName;
    if (updates.litellmPrefix !== undefined) merged.litellmPrefix = updates.litellmPrefix;
    if (updates.apiBaseUrl !== undefined) merged.apiBaseUrl = updates.apiBaseUrl;
    if (updates.modelsEndpoint !== undefined) merged.modelsEndpoint = updates.modelsEndpoint;
    if (updates.modelsFetchMethod !== undefined) merged.modelsFetchMethod = updates.modelsFetchMethod;
    if (updates.authType !== undefined) merged.authType = updates.authType;
    if (updates.isEnabled !== undefined) merged.isEnabled = updates.isEnabled;
    if (updates.config !== undefined) merged.config = updates.config;
    merged.updatedAt = updates.updatedAt;
    ctx.db.providers.id.update(merged);
  }
);

export const deleteProvider = spacetimedb.reducer(
  { id: t.string() },
  (ctx, { id }) => {
    const existing = ctx.db.providers.id.find(id);
    if (existing) {
      ctx.db.providers.id.delete(id);
    }
  }
);

// -- Provider API Keys --

export const setProviderApiKey = spacetimedb.reducer(
  {
    providerId: t.string(),
    encryptedValue: t.string(),
    keyType: t.string(),
    createdAt: t.u64(),
    updatedAt: t.u64(),
  },
  (ctx, key) => {
    const existing = ctx.db.provider_api_keys.providerId.find(key.providerId);
    if (existing) {
      ctx.db.provider_api_keys.providerId.update({
        ...existing,
        ...key,
        createdAt: key.createdAt === 0n ? existing.createdAt : key.createdAt,
      });
    } else {
      ctx.db.provider_api_keys.insert(key);
    }
  }
);

export const deleteProviderApiKey = spacetimedb.reducer(
  { providerId: t.string() },
  (ctx, { providerId }) => {
    const existing = ctx.db.provider_api_keys.providerId.find(providerId);
    if (existing) {
      ctx.db.provider_api_keys.providerId.delete(providerId);
    }
  }
);

// -- Provider Aliases --

export const setProviderAlias = spacetimedb.reducer(
  {
    alias: t.string(),
    providerId: t.string(),
  },
  (ctx, { alias, providerId }) => {
    const existing = ctx.db.provider_aliases.alias.find(alias);
    if (existing) {
      ctx.db.provider_aliases.alias.update({ alias, providerId });
    } else {
      ctx.db.provider_aliases.insert({ alias, providerId });
    }
  }
);

export const deleteProviderAlias = spacetimedb.reducer(
  { alias: t.string() },
  (ctx, { alias }) => {
    const existing = ctx.db.provider_aliases.alias.find(alias);
    if (existing) {
      ctx.db.provider_aliases.alias.delete(alias);
    }
  }
);

// -- Prompt Fragments --

export const addPromptFragment = spacetimedb.reducer(
  {
    id: t.string(),
    name: t.string(),
    display_name: t.string(),
    category: t.string(),
    content: t.string(),
    description: t.string().default(''),
    is_active: t.bool().default(true),
    is_system: t.bool().default(false),
    summary: t.string().default(''),
    tier: t.string().default('standard'),
    task_triggers: t.string().default('[]'),
    token_estimate: t.u32().default(0),
    created_at: t.u64(),
    updated_at: t.u64(),
  },
  (ctx, fragment) => {
    const existing = ctx.db.prompt_fragments.id.find(fragment.id);
    if (existing) {
      ctx.db.prompt_fragments.id.update({
        ...existing,
        ...fragment,
        created_at: fragment.created_at === 0n ? existing.created_at : fragment.created_at,
      });
    } else {
      ctx.db.prompt_fragments.insert(fragment);
    }
  }
);

export const deletePromptFragment = spacetimedb.reducer(
  { id: t.string() },
  (ctx, { id }) => {
    const existing = ctx.db.prompt_fragments.id.find(id);
    if (existing) {
      ctx.db.prompt_fragments.id.delete(id);
    }
  }
);

// -- Prompt Fragment Versions --

export const addPromptFragmentVersion = spacetimedb.reducer(
  {
    id: t.string(),
    fragment_id: t.string(),
    version: t.u32(),
    content: t.string(),
    change_reason: t.string().default(''),
    changed_by: t.string().default('user'),
    created_at: t.u64(),
  },
  (ctx, version) => {
    const existing = ctx.db.prompt_fragment_versions.id.find(version.id);
    if (existing) {
      ctx.db.prompt_fragment_versions.id.update({
        ...existing,
        ...version,
        created_at: version.created_at === 0n ? existing.created_at : version.created_at,
      });
    } else {
      ctx.db.prompt_fragment_versions.insert(version);
    }
  }
);

// -- Agent Prompt Fragment Attachments --

export const addAgentPromptFragment = spacetimedb.reducer(
  {
    id: t.string(),
    agent_id: t.string(),
    fragment_id: t.string(),
    rank: t.u32().default(0),
    enabled: t.bool().default(true),
    created_at: t.u64(),
  },
  (ctx, attachment) => {
    const existing = ctx.db.agent_prompt_fragments.id.find(attachment.id);
    if (existing) {
      ctx.db.agent_prompt_fragments.id.update({
        ...existing,
        ...attachment,
        created_at: attachment.created_at === 0n ? existing.created_at : attachment.created_at,
      });
    } else {
      ctx.db.agent_prompt_fragments.insert(attachment);
    }
  }
);

export const deleteAgentPromptFragment = spacetimedb.reducer(
  { id: t.string() },
  (ctx, { id }) => {
    const existing = ctx.db.agent_prompt_fragments.id.find(id);
    if (existing) {
      ctx.db.agent_prompt_fragments.id.delete(id);
    }
  }
);

// -- Prompt Templates --

export const addPromptTemplate = spacetimedb.reducer(
  {
    id: t.string(),
    name: t.string(),
    display_name: t.string(),
    category: t.string(),
    content: t.string(),
    variables: t.string().default('[]'),
    description: t.string().default(''),
    is_active: t.bool().default(true),
    created_at: t.u64(),
    updated_at: t.u64(),
  },
  (ctx, template) => {
    const existing = ctx.db.prompt_templates.id.find(template.id);
    if (existing) {
      ctx.db.prompt_templates.id.update({
        ...existing,
        ...template,
        created_at: template.created_at === 0n ? existing.created_at : template.created_at,
      });
    } else {
      ctx.db.prompt_templates.insert(template);
    }
  }
);

export const deletePromptTemplate = spacetimedb.reducer(
  { id: t.string() },
  (ctx, { id }) => {
    const existing = ctx.db.prompt_templates.id.find(id);
    if (existing) {
      ctx.db.prompt_templates.id.delete(id);
    }
  }
);

// -- Prompt Template Versions --

export const addPromptTemplateVersion = spacetimedb.reducer(
  {
    id: t.string(),
    template_id: t.string(),
    version: t.u32(),
    content: t.string(),
    change_reason: t.string().default(''),
    changed_by: t.string().default('user'),
    created_at: t.u64(),
  },
  (ctx, version) => {
    const existing = ctx.db.prompt_template_versions.id.find(version.id);
    if (existing) {
      ctx.db.prompt_template_versions.id.update({
        ...existing,
        ...version,
        created_at: version.created_at === 0n ? existing.created_at : version.created_at,
      });
    } else {
      ctx.db.prompt_template_versions.insert(version);
    }
  }
);

// ─── Deployment Reducers (Design Doc 039) ──────────────────────────────────

// -- Deployment Environments --

export const create_deployment_environment = spacetimedb.reducer(
  {
    name: t.string(),
    display_name: t.string(),
    order: t.u32(),
    max_script_timeout: t.u32(),
    health_check_interval: t.u32(),
    window_days: t.string().default('[]'),
    window_start: t.string().default(''),
    window_end: t.string().default(''),
    window_timezone: t.string().default(''),
    required_approvals: t.u32().default(1),
    history_id: t.string(),
    changed_by: t.string(),
  },
  (ctx, args) => {
    const now = BigInt(Date.now());
    ctx.db.deployment_environments.insert({
      name: args.name,
      display_name: args.display_name,
      order: args.order,
      is_active: true,
      max_script_timeout: args.max_script_timeout,
      health_check_interval: args.health_check_interval,
      window_days: args.window_days,
      window_start: args.window_start,
      window_end: args.window_end,
      window_timezone: args.window_timezone,
      required_approvals: args.required_approvals,
      created_at: now,
      updated_at: now,
    });
    ctx.db.deployment_environment_history.insert({
      id: args.history_id,
      environment_name: args.name,
      action: 'created',
      changed_by: args.changed_by,
      changed_at: now,
      before_state: '',
      after_state: JSON.stringify({ name: args.name, display_name: args.display_name }),
    });
  }
);

export const update_deployment_environment = spacetimedb.reducer(
  {
    name: t.string(),
    display_name: t.string().optional(),
    order: t.u32().optional(),
    max_script_timeout: t.u32().optional(),
    health_check_interval: t.u32().optional(),
    window_days: t.string().optional(),
    window_start: t.string().optional(),
    window_end: t.string().optional(),
    window_timezone: t.string().optional(),
    required_approvals: t.u32().optional(),
    is_active: t.bool().optional(),
    history_id: t.string(),
    changed_by: t.string(),
  },
  (ctx, args) => {
    const existing = ctx.db.deployment_environments.name.find(args.name);
    if (!existing) return;
    const now = BigInt(Date.now());
    const before = JSON.stringify(existing);
    const updated = {
      ...existing,
      display_name: args.display_name ?? existing.display_name,
      order: args.order ?? existing.order,
      max_script_timeout: args.max_script_timeout ?? existing.max_script_timeout,
      health_check_interval: args.health_check_interval ?? existing.health_check_interval,
      window_days: args.window_days ?? existing.window_days,
      window_start: args.window_start ?? existing.window_start,
      window_end: args.window_end ?? existing.window_end,
      window_timezone: args.window_timezone ?? existing.window_timezone,
      required_approvals: args.required_approvals ?? existing.required_approvals,
      is_active: args.is_active ?? existing.is_active,
      updated_at: now,
    };
    ctx.db.deployment_environments.name.update(updated);
    ctx.db.deployment_environment_history.insert({
      id: args.history_id,
      environment_name: args.name,
      action: args.is_active === false ? 'deactivated' : args.is_active === true ? 'reactivated' : 'updated',
      changed_by: args.changed_by,
      changed_at: now,
      before_state: before,
      after_state: JSON.stringify(updated),
    });
  }
);

export const add_deployment_approver = spacetimedb.reducer(
  {
    id: t.string(),
    environment_name: t.string(),
    user_id: t.string(),
    added_by: t.string(),
  },
  (ctx, args) => {
    ctx.db.deployment_environment_approvers.insert({
      id: args.id,
      environment_name: args.environment_name,
      user_id: args.user_id,
      added_at: BigInt(Date.now()),
      added_by: args.added_by,
    });
  }
);

export const remove_deployment_approver = spacetimedb.reducer(
  {
    id: t.string(),
  },
  (ctx, args) => {
    const existing = ctx.db.deployment_environment_approvers.id.find(args.id);
    if (existing) {
      ctx.db.deployment_environment_approvers.id.delete(args.id);
    }
  }
);

// -- Deployment Promotions --

export const initiate_promotion = spacetimedb.reducer(
  {
    id: t.string(),
    script_id: t.string(),
    script_version: t.string(),
    script_sha256: t.string(),
    environment_name: t.string(),
    status: t.string(),
    initiated_by: t.string(),
  },
  (ctx, args) => {
    const now = BigInt(Date.now());
    ctx.db.deployment_promotions.insert({
      id: args.id,
      script_id: args.script_id,
      script_version: args.script_version,
      script_sha256: args.script_sha256,
      environment_name: args.environment_name,
      status: args.status,
      initiated_by: args.initiated_by,
      initiated_at: now,
      promoted_at: 0n,
      deployed_at: 0n,
      receipt_id: '',
    });
  }
);

export const record_approval = spacetimedb.reducer(
  {
    id: t.string(),
    promotion_id: t.string(),
    script_id: t.string(),
    script_version: t.string(),
    environment_name: t.string(),
    user_id: t.string(),
  },
  (ctx, args) => {
    ctx.db.deployment_approvals.insert({
      id: args.id,
      promotion_id: args.promotion_id,
      script_id: args.script_id,
      script_version: args.script_version,
      environment_name: args.environment_name,
      user_id: args.user_id,
      approved_at: BigInt(Date.now()),
    });
  }
);

export const update_promotion_status = spacetimedb.reducer(
  {
    id: t.string(),
    status: t.string(),
    promoted_at: t.u64().optional(),
    deployed_at: t.u64().optional(),
    receipt_id: t.string().optional(),
  },
  (ctx, args) => {
    const existing = ctx.db.deployment_promotions.id.find(args.id);
    if (!existing) return;
    ctx.db.deployment_promotions.id.update({
      ...existing,
      status: args.status,
      promoted_at: args.promoted_at ?? existing.promoted_at,
      deployed_at: args.deployed_at ?? existing.deployed_at,
      receipt_id: args.receipt_id ?? existing.receipt_id,
    });
  }
);

// -- System Events --

export const enqueueSystemEvent = spacetimedb.reducer(
  {
    id: t.string(),
    conversationId: t.string(),
    agentId: t.string(),
    eventType: t.string(),
    summary: t.string(),
    metadata: t.string(),
  },
  (ctx, evt) => {
    ctx.db.system_events.insert({
      ...evt,
      consumed: false,
      createdAt: BigInt(Date.now()),
    });
  }
);

export const consumeSystemEvent = spacetimedb.reducer(
  { id: t.string() },
  (ctx, { id }) => {
    const evt = ctx.db.system_events.id.find(id);
    if (evt) {
      ctx.db.system_events.id.delete(id);
    }
  }
);
