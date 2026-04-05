/**
 * BranchManager — persists branch preferences and manages git branch operations.
 *
 * The gateway owns all branch management. The worker follows orders.
 */

import { homedir } from "os";
import { join, dirname } from "path";
import { readFileSync, writeFileSync, mkdirSync, existsSync } from "fs";
import { execSync } from "child_process";

export interface BranchInfo {
  name: string;
  lastCommit: string; // ISO date
}

export interface BranchPreferences {
  [containerId: string]: string; // container_id -> branch name
}

export interface WorkerStatus {
  online: boolean;
  branch: string | null;
  activeTurns: number | null;
  pendingReload: boolean;
}

export class BranchManager {
  private prefsPath: string;
  private repoRoot: string;
  private workerUrl: string;
  private prefs: BranchPreferences;

  constructor(workerUrl = "http://localhost:18790") {
    this.prefsPath = join(homedir(), ".bond", "data", "container-branches.json");
    this.repoRoot = this.findRepoRoot();
    this.workerUrl = workerUrl;
    this.prefs = this.loadPrefs();
  }

  private findRepoRoot(): string {
    // Walk up from cwd looking for bond.json
    let dir = process.cwd();
    for (let i = 0; i < 10; i++) {
      if (existsSync(join(dir, "bond.json"))) return dir;
      const parent = dirname(dir);
      if (parent === dir) break;
      dir = parent;
    }
    // Fallback to cwd
    return process.cwd();
  }

  private loadPrefs(): BranchPreferences {
    try {
      if (existsSync(this.prefsPath)) {
        const raw = readFileSync(this.prefsPath, "utf-8");
        const parsed = JSON.parse(raw);
        if (typeof parsed === "object" && parsed !== null) return parsed;
      }
    } catch (err) {
      console.warn("[branches] Failed to load preferences, using defaults:", (err as Error).message);
    }
    return { default: "main" };
  }

  private savePrefs(): void {
    try {
      const dir = dirname(this.prefsPath);
      if (!existsSync(dir)) mkdirSync(dir, { recursive: true });
      writeFileSync(this.prefsPath, JSON.stringify(this.prefs, null, 2), "utf-8");
    } catch (err) {
      console.error("[branches] Failed to save preferences:", (err as Error).message);
    }
  }

  /**
   * List available branches: main, dev (if exists), + 5 most recently pushed.
   */
  async listBranches(): Promise<BranchInfo[]> {
    try {
      execSync("git fetch origin --prune", {
        cwd: this.repoRoot,
        timeout: 30_000,
        stdio: "pipe",
      });
    } catch (err) {
      console.warn("[branches] git fetch failed:", (err as Error).message);
    }

    try {
      const raw = execSync(
        "git branch -r --sort=-committerdate --format='%(refname:short) %(committerdate:iso8601)'",
        { cwd: this.repoRoot, timeout: 10_000, encoding: "utf-8" },
      );

      const lines = raw.trim().split("\n").filter(Boolean);
      const branches: BranchInfo[] = [];
      const seen = new Set<string>();

      for (const line of lines) {
        // Format: "origin/branch-name 2026-03-20 12:00:00 +0000"
        const spaceIdx = line.indexOf(" ");
        if (spaceIdx === -1) continue;
        const fullRef = line.slice(0, spaceIdx);
        const dateStr = line.slice(spaceIdx + 1).trim();

        // Strip "origin/" prefix
        const name = fullRef.replace(/^origin\//, "");
        if (name === "HEAD" || seen.has(name)) continue;
        seen.add(name);
        branches.push({ name, lastCommit: dateStr });
      }

      // Ensure main and dev are at the top
      const result: BranchInfo[] = [];
      const mainBranch = branches.find((b) => b.name === "main");
      if (mainBranch) result.push(mainBranch);

      const devBranch = branches.find((b) => b.name === "dev");
      if (devBranch) result.push(devBranch);

      // Add up to 5 more recent branches (excluding main/dev)
      let count = 0;
      for (const b of branches) {
        if (b.name === "main" || b.name === "dev") continue;
        if (count >= 5) break;
        result.push(b);
        count++;
      }

      return result;
    } catch (err) {
      console.error("[branches] Failed to list branches:", (err as Error).message);
      return [{ name: "main", lastCommit: new Date().toISOString() }];
    }
  }

  /**
   * Get stored branch preference for a container.
   */
  getPreference(containerId = "default"): string {
    return this.prefs[containerId] || "main";
  }

  /**
   * Set branch preference. Notifies worker and destroys/schedules destruction of the container.
   *
   * Flow:
   *   1. Save preference to disk (survives restarts).
   *   2. Notify the worker via /reload — the worker will shut itself down
   *      (immediately if idle, after the current turn if busy).
   *   3. If the worker is idle (not deferred), proactively destroy the
   *      container via the backend API so it's cleaned up right away.
   *
   * Returns { deferred, activeTurns } indicating whether the switch was deferred.
   */
  async setPreference(
    containerId = "default",
    branch: string,
    workerUrl?: string,
    backendUrl?: string,
    agentId?: string,
  ): Promise<{ deferred: boolean; activeTurns: number | null }> {
    this.prefs[containerId] = branch;
    this.savePrefs();

    // If we don't have a worker URL, we can't notify the worker.
    // Just destroy any lingering container and save preference for next startup.
    if (!workerUrl) {
      if (backendUrl && agentId) {
        await this.destroyContainer(backendUrl, agentId);
      }
      return { deferred: false, activeTurns: null };
    }

    // Try to notify the worker
    try {
      const status = await this.getWorkerStatus(workerUrl);
      if (!status.online) {
        // Worker offline — destroy any lingering container
        if (backendUrl && agentId) {
          await this.destroyContainer(backendUrl, agentId);
        }
        return { deferred: false, activeTurns: null };
      }

      const resp = await fetch(`${workerUrl}/reload`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ branch }),
      });

      if (resp.ok) {
        const data = await resp.json();
        const deferred = data.deferred || false;

        // If the worker is shutting down (idle case), proactively destroy the container
        if (!deferred && data.shutting_down && backendUrl && agentId) {
          // Give the worker a moment to exit cleanly before destroying
          setTimeout(() => this.destroyContainer(backendUrl!, agentId!), 3000);
        }

        return {
          deferred,
          activeTurns: data.active_turns ?? null,
        };
      }
    } catch {
      // Worker unreachable — preference saved for next startup.
      // Try to destroy the container anyway.
      if (backendUrl && agentId) {
        await this.destroyContainer(backendUrl, agentId);
      }
    }

    return { deferred: false, activeTurns: null };
  }

  /**
   * Destroy an agent's container via the backend API.
   */
  async destroyContainer(backendUrl: string, agentId: string): Promise<boolean> {
    try {
      const authHeaders: Record<string, string> = { "Content-Type": "application/json" };
      if (process.env.BOND_API_KEY) authHeaders["Authorization"] = `Bearer ${process.env.BOND_API_KEY}`;
      const resp = await fetch(`${backendUrl}/api/v1/agent/container/destroy`, {
        method: "POST",
        headers: authHeaders,
        body: JSON.stringify({ agent_id: agentId }),
      });
      if (resp.ok) {
        const data = await resp.json();
        console.log(`[branches] Container destroyed for agent ${agentId}: ${data.destroyed}`);
        return data.destroyed || false;
      }
    } catch (err) {
      console.warn(`[branches] Failed to destroy container for agent ${agentId}:`, (err as Error).message);
    }
    return false;
  }

  /**
   * Check worker health and current branch.
   */
  async getWorkerStatus(workerUrl?: string): Promise<WorkerStatus> {
    const targetUrl = workerUrl || this.workerUrl;
    try {
      const controller = new AbortController();
      const timeout = setTimeout(() => controller.abort(), 3000);

      const resp = await fetch(`${targetUrl}/branch`, {
        signal: controller.signal,
      });
      clearTimeout(timeout);

      if (resp.ok) {
        const data = await resp.json();
        return {
          online: true,
          branch: data.branch || null,
          activeTurns: data.active_turns ?? 0,
          pendingReload: data.pending_reload || false,
        };
      }
    } catch {
      // Worker unreachable
    }

    return { online: false, branch: null, activeTurns: null, pendingReload: false };
  }

  /**
   * Notify worker to reload (used by webhook handlers).
   */
  async notifyWorkerReload(branch?: string): Promise<boolean> {
    try {
      const resp = await fetch(`${this.workerUrl}/reload`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ branch: branch || this.getPreference() }),
      });
      return resp.ok;
    } catch {
      return false;
    }
  }
}
