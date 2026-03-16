import { spawn } from "node:child_process";
import { execSync } from "node:child_process";

const WEBHOOK_EVENTS = ["push", "pull_request", "check_run", "check_suite", "issue_comment"];
const MAX_ATTEMPTS = 3;

export type RepoWebhookState = "pending" | "registered" | "failed" | "gave_up";

export interface RepoWebhookStatus {
  state: RepoWebhookState;
  attempts: number;
  lastError?: string;
}

export interface WebhookRegistrarOptions {
  externalUrl?: string;
  webhookSecret?: string;
  /** Explicit repos override (skips SpacetimeDB discovery). */
  repos?: string[];
  /** SpacetimeDB connection details for mount-based discovery. */
  spacetimedb?: {
    url: string;
    module: string;
    token: string;
  };
}

/**
 * Runs a `gh` CLI command, optionally writing data to stdin.
 * Resolves with stdout on success; rejects on non-zero exit or spawn error.
 * The rejection error has `code === "ENOENT"` when `gh` is not installed.
 */
export function execGh(args: string[], stdinData?: string): Promise<string> {
  return new Promise((resolve, reject) => {
    let proc: ReturnType<typeof spawn>;
    try {
      proc = spawn("gh", args, { stdio: ["pipe", "pipe", "pipe"] });
    } catch (err) {
      return reject(err);
    }

    let stdout = "";
    let stderr = "";
    proc.stdout!.on("data", (chunk: Buffer) => { stdout += chunk.toString(); });
    proc.stderr!.on("data", (chunk: Buffer) => { stderr += chunk.toString(); });
    proc.on("error", reject);
    proc.on("close", (code) => {
      if (code === 0) {
        resolve(stdout);
      } else {
        const err = new Error(`gh ${args[0] ?? ""} exited with code ${code}: ${stderr.trim()}`);
        (err as any).exitCode = code;
        (err as any).stderr = stderr;
        reject(err);
      }
    });

    if (stdinData !== undefined) {
      proc.stdin!.write(stdinData);
    }
    proc.stdin!.end();
  });
}

/**
 * Extract the GitHub owner/repo from a local git repository path.
 * Tries `git remote get-url origin` and parses the result.
 * Returns null if not a git repo or no GitHub remote found.
 */
export function resolveGitHubRepo(hostPath: string): string | null {
  try {
    const url = execSync(`git -C "${hostPath}" remote get-url origin`, {
      encoding: "utf8",
      timeout: 5000,
      stdio: ["pipe", "pipe", "pipe"],
    }).trim();

    // Match SSH: git@github.com:owner/repo.git
    const sshMatch = url.match(/github\.com[:/]([^/]+\/[^/.]+?)(?:\.git)?$/);
    if (sshMatch) return sshMatch[1];

    // Match HTTPS: https://github.com/owner/repo.git
    const httpsMatch = url.match(/github\.com\/([^/]+\/[^/.]+?)(?:\.git)?$/);
    if (httpsMatch) return httpsMatch[1];

    return null;
  } catch {
    return null;
  }
}

/**
 * Registers GitHub webhooks for repos discovered from SpacetimeDB agent workspace mounts.
 *
 * Uses the `gh` CLI (already authenticated on the host) instead of requiring
 * a GITHUB_TOKEN env var. Tracks per-repo state to avoid re-registering
 * successful repos and to stop retrying repos that consistently fail
 * (after MAX_ATTEMPTS failures).
 */
export class WebhookRegistrar {
  private externalUrl?: string;
  private webhookSecret?: string;
  private configuredRepos: string[];
  private spacetimedbConfig?: { url: string; module: string; token: string };
  /** Per-repo webhook registration state. */
  private repoStatus: Map<string, RepoWebhookStatus> = new Map();

  constructor(opts: WebhookRegistrarOptions = {}) {
    this.externalUrl = opts.externalUrl;
    this.webhookSecret = opts.webhookSecret;
    this.configuredRepos = opts.repos ?? [];
    this.spacetimedbConfig = opts.spacetimedb;
  }

  /**
   * Get the current status of all tracked repos.
   */
  getRepoStatuses(): Map<string, RepoWebhookStatus> {
    return new Map(this.repoStatus);
  }

  /**
   * Get the status of a specific repo.
   */
  getRepoStatus(repo: string): RepoWebhookStatus | undefined {
    return this.repoStatus.get(repo);
  }

  /**
   * Reset failed/gave_up repos so they can be retried.
   * If repo is specified, resets only that repo. Otherwise resets all.
   */
  reset(repo?: string): void {
    if (repo) {
      const status = this.repoStatus.get(repo);
      if (status && (status.state === "failed" || status.state === "gave_up")) {
        this.repoStatus.set(repo, { state: "pending", attempts: 0 });
        console.log(`[registrar] Reset webhook state for ${repo}`);
      }
    } else {
      for (const [r, status] of this.repoStatus) {
        if (status.state === "failed" || status.state === "gave_up") {
          this.repoStatus.set(r, { state: "pending", attempts: 0 });
        }
      }
      console.log("[registrar] Reset all failed/gave_up webhook states");
    }
  }

  /**
   * Discover repos from SpacetimeDB agent_workspace_mounts table.
   * Resolves each mount's hostPath to a GitHub owner/repo via git remote.
   */
  async discoverReposFromMounts(): Promise<string[]> {
    if (!this.spacetimedbConfig) {
      console.warn("[registrar] SpacetimeDB not configured — cannot discover repos from mounts");
      return [];
    }

    const { url, module, token } = this.spacetimedbConfig;
    try {
      const res = await fetch(`${url}/v1/database/${module}/sql`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "Authorization": `Bearer ${token}`,
        },
        body: "SELECT DISTINCT host_path FROM agent_workspace_mounts",
      });

      if (!res.ok) {
        const body = await res.text();
        console.warn(`[registrar] SpacetimeDB query failed (${res.status}): ${body}`);
        return [];
      }

      const data = await res.json() as any[];
      if (!data || !Array.isArray(data) || data.length === 0) return [];

      const resultSet = data[0];
      if (!resultSet?.rows) return [];

      const hostPaths: string[] = resultSet.rows.map((row: any[]) => row[0]).filter(Boolean);

      // Resolve each hostPath to a GitHub repo
      const repos = new Set<string>();
      for (const hostPath of hostPaths) {
        const repo = resolveGitHubRepo(hostPath);
        if (repo) {
          repos.add(repo);
        } else {
          console.log(`[registrar] Mount ${hostPath} — not a GitHub repo, skipping`);
        }
      }

      return Array.from(repos);
    } catch (err: any) {
      console.warn("[registrar] Failed to discover repos from mounts:", err.message ?? String(err));
      return [];
    }
  }

  /**
   * Ensure webhooks exist for all configured/discovered repos.
   * Designed to be called without await from startup — failures are logged,
   * not propagated.
   */
  async ensureWebhooks(): Promise<void> {
    if (!this.externalUrl) {
      console.warn("[registrar] GATEWAY_EXTERNAL_URL not set — skipping webhook registration");
      return;
    }

    let repos: string[];
    if (this.configuredRepos.length > 0) {
      repos = this.configuredRepos;
      console.log(`[registrar] Using ${repos.length} configured repo(s) from bond.json`);
    } else {
      repos = await this.discoverReposFromMounts();
    }

    if (repos.length === 0) {
      console.log("[registrar] No repos to register webhooks for");
      return;
    }

    // Filter out repos that are already registered or gave up
    const actionable = repos.filter((repo) => {
      const status = this.repoStatus.get(repo);
      if (!status) return true; // new repo
      if (status.state === "registered") {
        console.log(`[registrar] ${repo} — already registered, skipping`);
        return false;
      }
      if (status.state === "gave_up") {
        console.log(`[registrar] ${repo} — gave up after ${MAX_ATTEMPTS} failures, skipping (use reset() to retry)`);
        return false;
      }
      return true; // pending or failed (under MAX_ATTEMPTS)
    });

    if (actionable.length === 0) {
      console.log(`[registrar] All ${repos.length} repo(s) already registered or gave up — nothing to do`);
      return;
    }

    console.log(`[registrar] Ensuring webhooks for ${actionable.length} repo(s) (${repos.length - actionable.length} skipped)...`);
    const results = await Promise.allSettled(
      actionable.map((repo) => this.ensureWebhookForRepo(repo))
    );

    const succeeded = results.filter((r) => r.status === "fulfilled").length;
    const failed = results.filter((r) => r.status === "rejected").length;
    const gaveUp = Array.from(this.repoStatus.values()).filter((s) => s.state === "gave_up").length;

    console.log(`[registrar] Results: ${succeeded} succeeded, ${failed} failed, ${gaveUp} gave up`);
  }

  private async ensureWebhookForRepo(repo: string): Promise<void> {
    const webhookUrl = `${this.externalUrl}/webhooks/github`;

    // List existing hooks for this repo
    let hooks: any[];
    try {
      const stdout = await execGh(["api", `/repos/${repo}/hooks`]);
      hooks = JSON.parse(stdout);
    } catch (err: any) {
      if (err.code === "ENOENT") {
        console.warn("[registrar] gh CLI not found");
        return;
      }
      this.recordFailure(repo, err.message ?? String(err));
      return;
    }

    // Idempotency check — skip if a hook with our URL already exists
    if (Array.isArray(hooks) && hooks.some((h: any) => h.config?.url === webhookUrl)) {
      const existing = hooks.find((h: any) => h.config?.url === webhookUrl);
      console.log(`[registrar] Webhook already registered for ${repo} (id=${existing?.id})`);
      this.repoStatus.set(repo, { state: "registered", attempts: 0 });
      return;
    }

    // Create webhook
    const body: Record<string, any> = {
      name: "web",
      active: true,
      events: WEBHOOK_EVENTS,
      config: {
        url: webhookUrl,
        content_type: "json",
        insecure_ssl: "0",
        ...(this.webhookSecret ? { secret: this.webhookSecret } : {}),
      },
    };

    try {
      await execGh(
        ["api", "--method", "POST", `/repos/${repo}/hooks`, "--input", "-"],
        JSON.stringify(body)
      );
      console.log(`[registrar] Created webhook for ${repo} → ${webhookUrl}`);
      this.repoStatus.set(repo, { state: "registered", attempts: 0 });
    } catch (err: any) {
      if (err.code === "ENOENT") {
        console.warn("[registrar] gh CLI not found");
        return;
      }
      this.recordFailure(repo, err.message ?? String(err));
    }
  }

  /**
   * Record a failure for a repo, incrementing the attempt count.
   * After MAX_ATTEMPTS, transitions to "gave_up" state.
   */
  private recordFailure(repo: string, error: string): void {
    const current = this.repoStatus.get(repo) ?? { state: "pending" as RepoWebhookState, attempts: 0 };
    const attempts = current.attempts + 1;

    if (attempts >= MAX_ATTEMPTS) {
      console.warn(`[registrar] ${repo} — failed ${attempts}/${MAX_ATTEMPTS} times, giving up: ${error}`);
      this.repoStatus.set(repo, { state: "gave_up", attempts, lastError: error });
    } else {
      console.warn(`[registrar] ${repo} — failed (attempt ${attempts}/${MAX_ATTEMPTS}): ${error}`);
      this.repoStatus.set(repo, { state: "failed", attempts, lastError: error });
    }
  }
}
