"use client";

import React, { useEffect, useState, useCallback } from "react";

const API_BASE = "http://localhost:18790/api/v1/mcp";

interface MCPServer {
  name: string;
  command: string;
  args: string[];
  env: Record<string, string>;
  enabled: boolean;
}

export default function MCPTab() {
  const [servers, setServers] = useState<MCPServer[]>([]);
  const [loading, setLoading] = useState(false);
  const [syncing, setSyncing] = useState(false);
  const [msg, setMsg] = useState("");

  const fetchServers = useCallback(async () => {
    setLoading(true);
    try {
      const res = await fetch(`${API_BASE}/servers`);
      if (res.ok) {
        setServers(await res.json());
      }
    } catch (err) {
      console.error("Failed to fetch MCP servers", err);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchServers();
  }, [fetchServers]);

  const handleSync = async () => {
    setSyncing(true);
    setMsg("Syncing from Claude...");
    try {
      const res = await fetch(`${API_BASE}/sync/claude`, { method: "POST" });
      if (res.ok) {
        setMsg("Successfully synced from Claude!");
        fetchServers();
      } else {
        setMsg("Failed to sync from Claude.");
      }
    } catch (err) {
      setMsg("Error syncing from Claude.");
    } finally {
      setSyncing(false);
      setTimeout(() => setMsg(""), 3000);
    }
  };

  return (
    <div className="space-y-6">
      <div className="flex justify-between items-center">
        <div>
          <h2 className="text-xl font-semibold">MCP Servers</h2>
          <p className="text-sm text-gray-500">Manage Model Context Protocol servers for your agents.</p>
        </div>
        <button
          onClick={handleSync}
          disabled={syncing}
          className="bg-blue-600 hover:bg-blue-700 text-white px-4 py-2 rounded-md text-sm font-medium disabled:opacity-50 transition-colors"
        >
          {syncing ? "Syncing..." : "Sync from Claude"}
        </button>
      </div>

      {msg && (
        <div className={`p-3 rounded-md text-sm ${msg.includes("Error") || msg.includes("Failed") ? "bg-red-100 text-red-700" : "bg-green-100 text-green-700"}`}>
          {msg}
        </div>
      )}

      <div className="bg-white shadow overflow-hidden sm:rounded-md border border-gray-200">
        <ul className="divide-y divide-gray-200">
          {servers.length === 0 ? (
            <li className="px-6 py-12 text-center text-gray-500">
              {loading ? "Loading servers..." : "No MCP servers configured yet."}
            </li>
          ) : (
            servers.map((server) => (
              <li key={server.name} className="px-6 py-4">
                <div className="flex items-center justify-between">
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center space-x-3">
                      <h3 className="text-sm font-medium text-gray-900 truncate">{server.name}</h3>
                      <span className={`px-2 inline-flex text-xs leading-5 font-semibold rounded-full ${server.enabled ? "bg-green-100 text-green-800" : "bg-gray-100 text-gray-800"}`}>
                        {server.enabled ? "Enabled" : "Disabled"}
                      </span>
                    </div>
                    <div className="mt-1">
                      <code className="text-xs bg-gray-50 p-1 rounded border border-gray-100 text-gray-600">
                        {server.command} {server.args.join(" ")}
                      </code>
                    </div>
                  </div>
                </div>
              </li>
            ))
          )}
        </ul>
      </div>
      
      <div className="bg-blue-50 p-4 rounded-md border border-blue-100">
        <h4 className="text-sm font-medium text-blue-800">Pro-tip: Simplified Access</h4>
        <p className="text-xs text-blue-700 mt-1">
          Once a server is added here, you can enable it for specific agents in the <strong>Agents</strong> tab. 
          Use the "Allow-only" or "Deny-only" toggles to control exactly what each agent can access.
        </p>
      </div>
    </div>
  );
}
