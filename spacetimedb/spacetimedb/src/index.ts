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
      isActive: t.bool(),
      isDefault: t.bool(),
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
      createdAt: t.u64(),
      updatedAt: t.u64(),
    }
  ),
});

export default spacetimedb;

// ===============================================================
// Reducers
// ===============================================================

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
  },
  (ctx, agent) => {
    ctx.db.agents.insert({
      ...agent,
      isActive: true,
      isDefault: false,
      createdAt: BigInt(Date.now()),
    });
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
