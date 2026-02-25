/**
 * Backend client — HTTP bridge from gateway to Python FastAPI backend.
 *
 * Calls the backend's agent turn endpoint and returns the response.
 */

export interface AgentTurnRequest {
  message: string;
  history?: Array<{ role: string; content: string }>;
  stream?: boolean;
}

export interface AgentTurnResponse {
  response: string;
}

export class BackendClient {
  private baseUrl: string;

  constructor(baseUrl: string) {
    this.baseUrl = baseUrl;
  }

  async agentTurn(req: AgentTurnRequest): Promise<AgentTurnResponse> {
    const res = await fetch(`${this.baseUrl}/api/v1/agent/turn`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(req),
    });

    if (!res.ok) {
      const text = await res.text();
      throw new Error(`Backend error ${res.status}: ${text}`);
    }

    return (await res.json()) as AgentTurnResponse;
  }

  async healthCheck(): Promise<boolean> {
    try {
      const res = await fetch(`${this.baseUrl}/api/v1/health`);
      return res.ok;
    } catch {
      return false;
    }
  }
}
