/**
 * TurnExecutor — calls the backend to execute an LLM turn and streams the response.
 * Reuses BackendClient.conversationTurnStream().
 */

import type { PipelineMessage, PipelineContext, PipelineHandler } from "../types.js";
import type { BackendClient } from "../../backend/client.js";

export class TurnExecutor implements PipelineHandler {
  name = "turn-executor";

  constructor(private backendClient: BackendClient) {}

  async handle(message: PipelineMessage, context: PipelineContext, next: () => Promise<void>): Promise<void> {
    if (!message.conversationId) {
      await context.abort("No conversation ID resolved");
      return;
    }

    const channelType = message.channelType === "webchat" ? undefined : message.channelType;
    const planId = message.metadata.planId as string | undefined;

    let agentName = "";
    message.response = "";

    for await (const event of this.backendClient.conversationTurnStream(
      message.conversationId,
      message.content,
      message.agentId,
      planId,
      channelType,
    )) {
      if (context.aborted) break;

      switch (event.event) {
        case "status":
          if (!agentName && event.data.agent_name) agentName = event.data.agent_name as string;
          await context.emit("status", {
            agentStatus: event.data.state,
            agentName,
            conversationId: message.conversationId,
          });
          break;

        case "chunk": {
          const content = event.data.content as string;
          message.response += content;
          await context.streamChunk(content);
          break;
        }

        case "tool_call":
          await context.emit("tool_call", {
            content: JSON.stringify(event.data),
            conversationId: message.conversationId,
          });
          break;

        case "plan_created":
          await context.emit("plan_created", {
            planId: event.data.plan_id,
            planTitle: event.data.title,
            planStatus: "active",
            conversationId: message.conversationId,
          });
          break;

        case "item_created":
          await context.emit("item_updated", {
            planId: event.data.plan_id,
            itemId: event.data.item_id,
            itemStatus: "new",
            itemTitle: event.data.title || "",
            conversationId: message.conversationId,
          });
          break;

        case "item_updated":
          await context.emit("item_updated", {
            planId: event.data.plan_id,
            itemId: event.data.item_id,
            itemStatus: event.data.status,
            itemTitle: event.data.title || "",
            conversationId: message.conversationId,
          });
          break;

        case "plan_completed":
          await context.emit("plan_completed", {
            planId: event.data.plan_id,
            planStatus: event.data.status,
            conversationId: message.conversationId,
          });
          break;

        case "coding_agent_started":
          await context.emit("coding_agent_started", {
            agent_type: event.data.agent_type,
            conversationId: message.conversationId,
          });
          break;

        case "done":
          message.metadata.responseMessageId = event.data.message_id || "";
          break;

        case "error":
          await context.emit("error", {
            error: event.data.message,
            conversationId: message.conversationId,
          });
          break;
      }
    }

    message.agentName = agentName;
    await next();
  }
}
