import { spawn } from "node:child_process";

const WEBHOOK_EVENTS = ["push", "pull_request", "check_run", "check_suite", "issue_comment"];

export interface WebhookRegistrarOptions {
  externalUrl?: string;
  webhookSecret?: string;
  repos?: string[];
  autoDiscover?: boolean;
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
 * Registers GitHub webhooks for repos at gateway startup.
 *
 * Uses the `gh` CLI (already authenticated on the host) instead of requiring
 * a GITHUB_TOKEN env var. Idempotent — skips repos that already have a
 * matching webhook pointing to our URL.
 */
export class WebhookRegistrar {
  private externalUrl?: string;
  private webhookSecret?: string;
  private configuredRepos: string[];
  private autoDiscover: boolean;

  constructor(opts: WebhookRegistrarOptions = {}) {
    this.externalUrl = opts.externalUrl;
    this.webhookSecret = opts.webhookSecret;
    this.configuredRepos = opts.repos ?? [];
    this.autoDiscover = opts.autoDiscover ?? true;
  }

  /**
   * Discover repos for the authenticated `gh` user via `gh repo list`.
   */
  async discoverRepos(): Promise<string[]> {
    try {
      const stdout = await execGh([
        "repo", "list",
        "--json", "nameWithOwner",
        "--jq", ".[].nameWithOwner",
        "--limit", "100",
      ]);
      return stdout.trim().split("\n").filter(Boolean);
    } catch (err: any) {
      if (err.code === "ENOENT") {
        console.warn("[registrar] gh CLI not found — skipping repo discovery");
      } else {
        console.warn("[registrar] gh repo list failed:", err.message ?? String(err));
      }
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
    } else if (this.autoDiscover) {
      repos = await this.discoverRepos();
    } else {
      repos = [];
    }

    if (repos.length === 0) {
      console.log("[registrar] No repos to register webhooks for");
      return;
    }

    console.log(`[registrar] Ensuring webhooks for ${repos.length} repo(s)...`);
    const results = await Promise.allSettled(
      repos.map((repo) => this.ensureWebhookForRepo(repo))
    );

    const failed = results.filter((r) => r.status === "rejected");
    if (failed.length > 0) {
      console.warn(`[registrar] ${failed.length} webhook registration(s) failed`);
    }
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
      console.warn(`[registrar] Could not list hooks for ${repo}:`, err.message ?? String(err));
      return;
    }

    // Idempotency check — skip if a hook with our URL already exists
    if (Array.isArray(hooks) && hooks.some((h: any) => h.config?.url === webhookUrl)) {
      const existing = hooks.find((h: any) => h.config?.url === webhookUrl);
      console.log(`[registrar] Webhook already registered for ${repo} (id=${existing?.id})`);
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
    } catch (err: any) {
      if (err.code === "ENOENT") {
        console.warn("[registrar] gh CLI not found");
        return;
      }
      console.warn(`[registrar] Could not create webhook for ${repo}:`, err.message ?? String(err));
    }
  }
}
