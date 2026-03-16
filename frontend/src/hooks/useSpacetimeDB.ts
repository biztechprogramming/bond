import { useEffect, useState } from 'react';
import { onDataChange, getConnection, getConversations, getWorkPlans, getWorkItems, getAgents, getAgentChannels, getAgentMounts, getAvailableModels, getSettings, getProviderApiKeys, getProviders, type Conversation, type WorkPlan, type WorkItem, type AgentRow, type AgentChannelRow, type AgentMountRow, type SettingRow, type ProviderApiKeyRow, type ProviderRow } from '@/lib/spacetimedb-client';

/**
 * useSpacetimeDB React hook.
 * 
 * Subscribes to the SpacetimeDB client data changes and triggers
 * a re-render in the component whenever table data is updated.
 * 
 * @param selector A function that selects data from the database.
 * @param deps Dependencies for the selector.
 */
export function useSpacetimeDB<T>(selector: (db: any) => T, deps: any[] = []): T {
  const [data, setData] = useState<T>(() => selector(getConnection()));

  useEffect(() => {
    const unsubscribe = onDataChange(() => {
      setData(selector(getConnection()));
    });
    
    // Initial fetch in case connection was established before mount
    setData(selector(getConnection()));
    
    return unsubscribe;
  }, [...deps]);

  return data;
}

/**
 * Hook to get conversations from SpacetimeDB.
 */
export function useConversations(): Conversation[] {
  return useSpacetimeDB(() => getConversations());
}

/**
 * Hook to get work plans from SpacetimeDB.
 */
export function useWorkPlans(): WorkPlan[] {
  return useSpacetimeDB(() => getWorkPlans());
}

/**
 * Hook to get work items for a specific plan from SpacetimeDB.
 */
export function useWorkItems(planId: string): WorkItem[] {
  return useSpacetimeDB(() => getWorkItems(planId), [planId]);
}

/**
 * Hook to get SpacetimeDB connection status.
 */
export function useSpacetimeConnection() {
  const [connected, setConnected] = useState(false);

  useEffect(() => {
    const check = () => {
      const conn = getConnection();
      setConnected(!!conn && conn.isActive);
    };

    check();
    return onDataChange(check);
  }, []);

  return { connected };
}

/**
 * Hook to get agents from SpacetimeDB with live updates.
 */
export function useAgents(): AgentRow[] {
  return useSpacetimeDB(() => getAgents());
}

/**
 * Hook to get channels for an agent from SpacetimeDB with live updates.
 */
export function useAgentChannels(agentId: string): AgentChannelRow[] {
  return useSpacetimeDB(() => getAgentChannels(agentId), [agentId]);
}

/**
 * Hook to get workspace mounts for an agent from SpacetimeDB with live updates.
 */
export function useAgentMounts(agentId: string): AgentMountRow[] {
  return useSpacetimeDB(() => getAgentMounts(agentId), [agentId]);
}

/**
 * Hook to get available LLM models from SpacetimeDB with live updates.
 */
export function useAvailableModels(): { id: string; name: string }[] {
  return useSpacetimeDB(() => getAvailableModels());
}

export function useSettings(): SettingRow[] {
  return useSpacetimeDB(() => getSettings());
}

export function useProviderApiKeys(): ProviderApiKeyRow[] {
  return useSpacetimeDB(() => getProviderApiKeys());
}

export function useProviders(): ProviderRow[] {
  return useSpacetimeDB(() => getProviders());
}

// ── Composite Hooks ──────────────────────────────────────────────────────
// These combine multiple STDB tables into the shapes components expect,
// eliminating repeated mapping code across pages.

/**
 * Legacy-compatible agent shape used by most components.
 * Maps camelCase STDB fields to the snake_case interface from the REST era.
 */
export interface AgentWithRelations {
  id: string;
  name: string;
  display_name: string;
  system_prompt: string;
  model: string;
  utility_model: string;
  sandbox_image: string | null;
  is_active: boolean;
  is_default: boolean;
  workspace_mounts: { id?: string; host_path: string; mount_name: string; container_path: string; readonly: boolean }[];
  channels: { channel: string; enabled: boolean; sandbox_override: string | null }[];
}

/**
 * Hook to get agents with their channels and workspace mounts bundled,
 * in the snake_case format that deployment/settings components expect.
 */
export function useAgentsWithRelations(): AgentWithRelations[] {
  return useSpacetimeDB(() => {
    const agents = getAgents();
    return agents.map(a => ({
      id: a.id,
      name: a.name,
      display_name: a.displayName,
      system_prompt: a.systemPrompt,
      model: a.model,
      utility_model: a.utilityModel,
      sandbox_image: a.sandboxImage || null,
      is_active: a.isActive,
      is_default: a.isDefault,
      workspace_mounts: getAgentMounts(a.id).map(m => ({
        id: m.id,
        host_path: m.hostPath,
        mount_name: m.mountName,
        container_path: m.containerPath,
        readonly: m.readonly,
      })),
      channels: getAgentChannels(a.id).map(c => ({
        channel: c.channel,
        enabled: c.enabled,
        sandbox_override: c.sandboxOverride || null,
      })),
    }));
  });
}

/**
 * Hook to get settings as a key-value map (Record<string, string>).
 * Replaces the common pattern of fetching settings via REST and converting.
 */
export function useSettingsMap(): Record<string, string> {
  return useSpacetimeDB(() => {
    const settings = getSettings();
    return Object.fromEntries(settings.map(s => [s.key, s.value]));
  });
}

/**
 * Helper to call a SpacetimeDB reducer with connection null-check.
 * Returns true if the call was made, false if no connection.
 */
export function callReducer(fn: (conn: NonNullable<ReturnType<typeof getConnection>>) => void): boolean {
  const conn = getConnection();
  if (!conn) return false;
  fn(conn);
  return true;
}
