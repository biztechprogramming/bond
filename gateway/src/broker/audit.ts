/**
 * Permission Broker — append-only JSONL audit logger.
 */

import fs from "node:fs";
import path from "node:path";
import type { AuditEntry } from "./types.js";

export class AuditLogger {
  private stream: fs.WriteStream;

  constructor(logDir: string) {
    fs.mkdirSync(logDir, { recursive: true });
    const logPath = path.join(logDir, "broker-audit.jsonl");
    this.stream = fs.createWriteStream(logPath, { flags: "a" });
  }

  log(entry: AuditEntry): void {
    this.stream.write(JSON.stringify(entry) + "\n");
  }

  close(): void {
    this.stream.end();
  }
}
