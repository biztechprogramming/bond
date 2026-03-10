/**
 * Permission Broker — command executor via child_process.
 */

import { execFile } from "node:child_process";
import type { ExecResult } from "./types.js";

const MAX_BUFFER = 10 * 1024 * 1024; // 10 MB

export interface ExecuteOptions {
  cwd?: string;
  timeout?: number; // seconds
  env?: Record<string, string>;
}

export function executeCommand(command: string, options: ExecuteOptions = {}): Promise<ExecResult> {
  const timeout = (options.timeout ?? 60) * 1000;
  const start = Date.now();

  return new Promise((resolve) => {
    execFile(
      "sh",
      ["-c", command],
      {
        cwd: options.cwd,
        timeout,
        maxBuffer: MAX_BUFFER,
        env: options.env ? { ...process.env, ...options.env } : undefined,
      },
      (error, stdout, stderr) => {
        const duration_ms = Date.now() - start;

        if (error && "killed" in error && error.killed) {
          resolve({
            exit_code: -1,
            stdout: stdout || "",
            stderr: stderr || "Command timed out",
            duration_ms,
          });
          return;
        }

        resolve({
          exit_code: error?.code !== undefined ? (typeof error.code === "number" ? error.code : 1) : 0,
          stdout: stdout || "",
          stderr: stderr || "",
          duration_ms,
        });
      },
    );
  });
}
