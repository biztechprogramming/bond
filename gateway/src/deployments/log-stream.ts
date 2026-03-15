/**
 * Deployment Log Streaming — appends deployment output to log files
 * and supports tailing via byte offset.
 *
 * Logs are stored at ~/.bond/deployments/logs/{env}/deploy-{YYYY-MM-DD}.log
 *
 * Design Doc 039 — Phase 4 Hardening
 */

import fs from "node:fs";
import path from "node:path";
import { homedir } from "node:os";

const DEPLOYMENTS_DIR = path.join(homedir(), ".bond", "deployments");

function getLogsDir(env: string): string {
  return path.join(DEPLOYMENTS_DIR, "logs", env);
}

function todayDate(): string {
  return new Date().toISOString().slice(0, 10);
}

function getLogFilePath(env: string, date: string): string {
  return path.join(getLogsDir(env), `deploy-${date}.log`);
}

/**
 * Append a line of deployment output to the log file.
 */
export function appendLog(env: string, line: string): void {
  const dir = getLogsDir(env);
  fs.mkdirSync(dir, { recursive: true });
  const logPath = getLogFilePath(env, todayDate());
  fs.appendFileSync(logPath, line + "\n", "utf8");
}

/**
 * Write a deployment execution block to the log.
 * Includes header with metadata and stdout/stderr output.
 */
export function writeDeployLog(
  env: string,
  scriptId: string,
  version: string,
  agentId: string,
  stdout: string,
  stderr: string,
  exitCode: number,
  durationMs: number,
): void {
  const dir = getLogsDir(env);
  fs.mkdirSync(dir, { recursive: true });
  const logPath = getLogFilePath(env, todayDate());

  const timestamp = new Date().toISOString();
  const header = `\n=== DEPLOY ${scriptId}@${version} | agent=${agentId} | ${timestamp} ===\n`;
  const body = [
    `exit_code=${exitCode} duration=${durationMs}ms`,
    stdout ? `--- stdout ---\n${stdout}` : "",
    stderr ? `--- stderr ---\n${stderr}` : "",
    `=== END ${scriptId}@${version} ===\n`,
  ].filter(Boolean).join("\n");

  fs.appendFileSync(logPath, header + body + "\n", "utf8");
}

/**
 * List available log dates for an environment.
 */
export function listLogDates(env: string): string[] {
  const dir = getLogsDir(env);
  if (!fs.existsSync(dir)) return [];

  return fs.readdirSync(dir)
    .filter(f => f.startsWith("deploy-") && f.endsWith(".log"))
    .map(f => f.replace("deploy-", "").replace(".log", ""))
    .sort()
    .reverse();
}

/**
 * Read log content from a given byte offset for tailing.
 * Returns the content and the new offset (end of file position).
 */
export function readLog(
  env: string,
  date: string,
  offset = 0,
): { content: string; offset: number; size: number } | null {
  const logPath = getLogFilePath(env, date);
  if (!fs.existsSync(logPath)) return null;

  const stat = fs.statSync(logPath);
  const size = stat.size;

  if (offset >= size) {
    return { content: "", offset: size, size };
  }

  const fd = fs.openSync(logPath, "r");
  const buf = Buffer.alloc(size - offset);
  fs.readSync(fd, buf, 0, buf.length, offset);
  fs.closeSync(fd);

  return {
    content: buf.toString("utf8"),
    offset: size,
    size,
  };
}
