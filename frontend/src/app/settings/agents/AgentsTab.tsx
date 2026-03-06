import React, { useEffect, useState, useCallback } from "react";
import DirBrowser from "@/components/shared/DirBrowser";

const API_BASE = "http://localhost:18790/api/v1/agents";

interface WorkspaceMount {
  id?: string;
  host_path: string;
  mount_name: string;
  container_path: string;
  readonly: boolean;
}

interface ChannelConfig {
  id?: string;
  channel: string;
  enabled: boolean;
  sandbox_override: string | null;
}

interface ToolInfo {
  name: string;
  description: string;
}

interface Agent {
  id: string;
  name: string;
  display_name: string;
  system_prompt: string;
  model: string;
  utility_model: string;
  sandbox_image: string | null;
  tools: string[];
  mcp_servers: string[];
  max_iterations: number;
  auto_rag: boolean;
  auto_rag_limit: number;
  is_default: boolean;
  is_active: boolean;
  workspace_mounts: WorkspaceMount[];
  channels: ChannelConfig[];
  tool_access_mode: "allow" | "deny";
  channel_access_mode: "allow" | "deny";
  mcp_access_mode: "allow" | "deny";
}

const ALL_CHANNELS = ["webchat", "signal", "telegram", "discord", "whatsapp", "email", "slack"];

export default function AgentsTab() {
  const [agents, setAgents] = useState<Agent[]>([]);
  const [tools, setTools] = useState<ToolInfo[]>([]);
  const [mcpServers, setMcpServers] = useState<{name: string}[]>([]);
  const [sandboxImages, setSandboxImages] = useState<string[]>([]);
  const [editing, setEditing] = useState<Agent | null>(null);
  const [isNew, setIsNew] = useState(false);
  const [msg, setMsg] = useState("");
  const [browsingMountIndex, setBrowsingMountIndex] = useState<number | null>(null);
  const [availableModels, setAvailableModels] = useState<{ id: string; name: string }[]>([]);

  const fetchData = useCallback(async () => {
    try {
      const [agentsRes, toolsRes, imagesRes, mcpRes] = await Promise.all([
        fetch(API_BASE),
        fetch(`${API_BASE}/tools`),
        fetch(`${API_BASE}/sandbox-images`),
        fetch(`http://localhost:18790/api/v1/mcp/servers`),
      ]);
      setAgents(await agentsRes.json());
      setTools(await toolsRes.json());
      setSandboxImages(await imagesRes.json());
      if (mcpRes.ok) setMcpServers(await mcpRes.json());
      
      try {
        const modelsRes = await fetch("http://localhost:18790/api/v1/settings/llm/models");
        if (modelsRes.ok) setAvailableModels(await modelsRes.json());
      } catch { /* models API not available */ }
    } catch { /* API not available */ }
  }, []);

  useEffect(() => {
    fetchData();
  }, [fetchData]);

  const newAgent = (): Agent => ({
    id: "",
    name: "",
    display_name: "",
    system_prompt: "",
    model: availableModels[0]?.id || "anthropic/claude-3-5-sonnet-20240620",
    utility_model: "claude-sonnet-4-6",
    sandbox_image: null,
    tools: [],
    mcp_servers: [],
    max_iterations: 25,
    auto_rag: true,
    auto_rag_limit: 5,
    is_default: false,
    is_active: true,
    workspace_mounts: [],
    channels: [],
    tool_access_mode: "allow",
    channel_access_mode: "allow",
    mcp_access_mode: "allow",
  });

  const saveAgent = async () => {
    if (!editing) return;
    setMsg("Saving...");
    try {
      const url = isNew ? API_BASE : `${API_BASE}/${editing.id}`;
      const method = isNew ? "POST" : "PATCH";
      const res = await fetch(url, {
        method,
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(editing),
      });
      if (!res.ok) throw new Error("Failed to save agent");
      setMsg("Saved!");
      setEditing(null);
      setIsNew(false);
      fetchData();
    } catch (err) {
      setMsg("Error saving agent.");
    } finally {
      setTimeout(() => setMsg(""), 3000);
    }
  };

  const deleteAgent = async (id: string) => {
    if (!confirm("Are you sure?")) return;
    try {
      const res = await fetch(`${API_BASE}/${id}`, { method: "DELETE" });
      if (res.ok) fetchData();
    } catch (err) {
      console.error("Delete failed", err);
    }
  };

  const toggleItem = (list: string[], item: string) => {
    return list.includes(item) ? list.filter((i) => i !== item) : [...list, item];
  };

  if (editing) {
    return (
      <div className="space-y-8 bg-white p-8 rounded-lg border border-gray-200 shadow-sm max-w-4xl mx-auto mb-12">
        <div className="flex justify-between items-center border-b pb-4">
          <h2 className="text-2xl font-bold text-gray-900">{isNew ? "Create Agent" : `Edit: ${editing.display_name}`}</h2>
          <div className="space-x-3">
            <button onClick={() => { setEditing(null); setIsNew(false); }} className="px-4 py-2 text-sm font-medium text-gray-700 hover:text-gray-900">Cancel</button>
            <button onClick={saveAgent} className="bg-blue-600 hover:bg-blue-700 text-white px-6 py-2 rounded-md text-sm font-medium shadow-sm transition-colors">Save Agent</button>
          </div>
        </div>

        <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
          <div className="space-y-4">
            <h3 className="text-lg font-semibold border-b pb-2">Basic Info</h3>
            <div>
              <label className="block text-sm font-medium text-gray-700">Display Name</label>
              <input type="text" value={editing.display_name} onChange={(e) => setEditing({ ...editing, display_name: e.target.value })} className="mt-1 block w-full border-gray-300 rounded-md shadow-sm focus:ring-blue-500 focus:border-blue-500 sm:text-sm" placeholder="e.g. My Helpful Assistant" />
            </div>
            <div>
              <label className="block text-sm font-medium text-gray-700">Internal Name (ID)</label>
              <input type="text" value={editing.name} onChange={(e) => setEditing({ ...editing, name: e.target.value })} className="mt-1 block w-full border-gray-300 rounded-md shadow-sm focus:ring-blue-500 focus:border-blue-500 sm:text-sm" placeholder="e.g. my-assistant" disabled={!isNew} />
            </div>
            <div>
              <label className="block text-sm font-medium text-gray-700">Model</label>
              <select value={editing.model} onChange={(e) => setEditing({ ...editing, model: e.target.value })} className="mt-1 block w-full border-gray-300 rounded-md shadow-sm focus:ring-blue-500 focus:border-blue-500 sm:text-sm">
                {availableModels.map((m) => <option key={m.id} value={m.id}>{m.name}</option>)}
              </select>
            </div>
          </div>

          <div className="space-y-4">
            <h3 className="text-lg font-semibold border-b pb-2">Capabilities</h3>
            <div className="space-y-4 p-4 bg-gray-50 rounded-md border border-gray-200">
              {/* Tools Access */}
              <div>
                <div className="flex justify-between items-center mb-2">
                  <label className="text-sm font-bold text-gray-700">Tools Access</label>
                  <select 
                    value={editing.tool_access_mode} 
                    onChange={(e) => setEditing({...editing, tool_access_mode: e.target.value as "allow" | "deny"})}
                    className="text-xs border-gray-300 rounded-md"
                  >
                    <option value="allow">Allow Only</option>
                    <option value="deny">Deny Only</option>
                  </select>
                </div>
                <div className="grid grid-cols-2 gap-2 max-h-40 overflow-y-auto p-2 bg-white border rounded">
                  {tools.map((t) => (
                    <label key={t.name} className="flex items-center space-x-2 text-xs">
                      <input type="checkbox" checked={editing.tools.includes(t.name)} onChange={() => setEditing({ ...editing, tools: toggleItem(editing.tools, t.name) })} className="rounded text-blue-600 focus:ring-blue-500" />
                      <span className="truncate" title={t.description}>{t.name}</span>
                    </label>
                  ))}
                </div>
              </div>

              {/* MCP Access */}
              <div>
                <div className="flex justify-between items-center mb-2">
                  <label className="text-sm font-bold text-gray-700">MCP Servers</label>
                  <select 
                    value={editing.mcp_access_mode} 
                    onChange={(e) => setEditing({...editing, mcp_access_mode: e.target.value as "allow" | "deny"})}
                    className="text-xs border-gray-300 rounded-md"
                  >
                    <option value="allow">Allow Only</option>
                    <option value="deny">Deny Only</option>
                  </select>
                </div>
                <div className="grid grid-cols-2 gap-2 max-h-32 overflow-y-auto p-2 bg-white border rounded">
                  {mcpServers.length === 0 ? <span className="text-xs text-gray-400 italic col-span-2">No servers found</span> : 
                    mcpServers.map((s) => (
                      <label key={s.name} className="flex items-center space-x-2 text-xs">
                        <input type="checkbox" checked={editing.mcp_servers.includes(s.name)} onChange={() => setEditing({ ...editing, mcp_servers: toggleItem(editing.mcp_servers, s.name) })} className="rounded text-blue-600 focus:ring-blue-500" />
                        <span className="truncate">{s.name}</span>
                      </label>
                    ))
                  }
                </div>
              </div>
            </div>
          </div>
        </div>

        <div className="space-y-4">
          <h3 className="text-lg font-semibold border-b pb-2">System Prompt</h3>
          <textarea value={editing.system_prompt} onChange={(e) => setEditing({ ...editing, system_prompt: e.target.value })} rows={8} className="block w-full border-gray-300 rounded-md shadow-sm focus:ring-blue-500 focus:border-blue-500 sm:text-sm font-mono" placeholder="You are a helpful assistant..." />
        </div>
      </div>
    );
  }

  return (
    <div className="space-y-6">
      <div className="flex justify-between items-center">
        <h2 className="text-xl font-semibold">Agents</h2>
        <button onClick={() => { setEditing(newAgent()); setIsNew(true); }} className="bg-blue-600 hover:bg-blue-700 text-white px-4 py-2 rounded-md text-sm font-medium transition-colors">Create Agent</button>
      </div>

      <div className="grid grid-cols-1 gap-6 sm:grid-cols-2 lg:grid-cols-3">
        {agents.map((agent) => (
          <div key={agent.id} className="bg-white rounded-lg border border-gray-200 shadow-sm hover:shadow-md transition-shadow flex flex-col">
            <div className="p-6 flex-1">
              <div className="flex items-center justify-between mb-2">
                <h3 className="text-lg font-bold text-gray-900 truncate">{agent.display_name}</h3>
                {agent.is_default && <span className="px-2 py-0.5 text-xs font-semibold bg-blue-100 text-blue-800 rounded-full">Default</span>}
              </div>
              <p className="text-sm text-gray-500 mb-4 line-clamp-2">{agent.system_prompt}</p>
              <div className="flex flex-wrap gap-2 mb-4">
                <span className="px-2 py-1 text-xs bg-gray-100 text-gray-600 rounded">Model: {agent.model.split("/").pop()}</span>
                <span className="px-2 py-1 text-xs bg-gray-100 text-gray-600 rounded">{agent.tools.length} Tools ({agent.tool_access_mode})</span>
              </div>
            </div>
            <div className="bg-gray-50 px-6 py-4 rounded-b-lg flex justify-between items-center border-t">
              <button onClick={() => setEditing(agent)} className="text-sm font-medium text-blue-600 hover:text-blue-800">Edit Settings</button>
              {!agent.is_default && <button onClick={() => deleteAgent(agent.id)} className="text-sm font-medium text-red-600 hover:text-red-800">Delete</button>}
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
