/**
 * Discovery Scripts — execute discovery scripts on remote servers over SSH.
 *
 * Design Doc 044 §2 — Remote Application Discovery
 */

import { spawn } from "node:child_process";

// ── Types ───────────────────────────────────────────────────────────────────

export interface SshScriptResult {
  exit_code: number;
  stdout: string;
  stderr: string;
}

// ── SSH Script Execution ────────────────────────────────────────────────────

/**
 * Execute a script on a remote host over SSH by piping the script content via stdin.
 */
export function executeSshScript(
  host: string,
  port: number,
  user: string,
  scriptContent: string,
  env?: Record<string, string>,
  keyPath?: string,
  timeoutSeconds = 60,
): Promise<SshScriptResult> {
  return new Promise((resolve) => {
    const envPrefix = env
      ? Object.entries(env).map(([k, v]) => `export ${k}=${shellEscape(v)}`).join("; ") + "; "
      : "";

    const remoteCmd = `${envPrefix}bash -s`;

    const args = [
      "-o", "StrictHostKeyChecking=no",
      "-o", "BatchMode=yes",
      "-o", `ConnectTimeout=${Math.min(timeoutSeconds, 30)}`,
      "-p", String(port),
    ];
    if (keyPath) args.push("-i", keyPath);
    args.push(`${user}@${host}`, remoteCmd);

    let stdout = "";
    let stderr = "";
    const proc = spawn("ssh", args, { stdio: ["pipe", "pipe", "pipe"] });

    const timer = setTimeout(() => {
      proc.kill("SIGTERM");
      resolve({ exit_code: -1, stdout, stderr: stderr + "\nTimeout after " + timeoutSeconds + "s" });
    }, timeoutSeconds * 1000);

    proc.stdout.on("data", (d: Buffer) => { stdout += d.toString(); });
    proc.stderr.on("data", (d: Buffer) => { stderr += d.toString(); });

    // Pipe the script content to stdin
    proc.stdin.write(scriptContent);
    proc.stdin.end();

    proc.on("close", (code) => {
      clearTimeout(timer);
      resolve({ exit_code: code ?? -1, stdout, stderr });
    });

    proc.on("error", (err) => {
      clearTimeout(timer);
      resolve({ exit_code: -1, stdout, stderr: err.message });
    });
  });
}

function shellEscape(s: string): string {
  return "'" + s.replace(/'/g, "'\\''") + "'";
}
