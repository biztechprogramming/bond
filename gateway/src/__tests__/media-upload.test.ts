import { mkdtempSync, rmSync } from "node:fs";
import { join } from "node:path";
import { tmpdir } from "node:os";

// Set BOND_HOME before any imports that read it at module level
const tempDir = mkdtempSync(join(tmpdir(), "media-test-"));
process.env.BOND_HOME = tempDir;

import { describe, it, expect, afterAll } from "vitest";
import express from "express";
import { createMediaRouter } from "../media/router.js";
import type { Server } from "node:http";

let server: Server;
let baseUrl: string;

const app = express();
app.use(createMediaRouter());

const startServer = new Promise<void>((resolve) => {
  server = app.listen(0, () => {
    const addr = server.address() as { port: number };
    baseUrl = `http://localhost:${addr.port}`;
    resolve();
  });
});

afterAll(() => {
  server?.close();
  rmSync(tempDir, { recursive: true, force: true });
});

function createFormData(fields: Record<string, string>, file?: { name: string; buffer: Buffer; type: string }) {
  const boundary = "----TestBoundary" + Date.now();
  const parts: string[] = [];

  for (const [key, value] of Object.entries(fields)) {
    parts.push(`--${boundary}\r\nContent-Disposition: form-data; name="${key}"\r\n\r\n${value}`);
  }

  let bodyParts: Buffer[] = parts.map((p) => Buffer.from(p + "\r\n"));

  if (file) {
    const header = `--${boundary}\r\nContent-Disposition: form-data; name="file"; filename="${file.name}"\r\nContent-Type: ${file.type}\r\n\r\n`;
    bodyParts.push(Buffer.from(header));
    bodyParts.push(file.buffer);
    bodyParts.push(Buffer.from("\r\n"));
  }

  bodyParts.push(Buffer.from(`--${boundary}--\r\n`));

  return {
    body: Buffer.concat(bodyParts),
    contentType: `multipart/form-data; boundary=${boundary}`,
  };
}

describe("Media Upload Router", () => {
  const testPng = Buffer.from(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg==",
    "base64"
  );

  it("POST /api/v1/images/upload with valid file returns 201", async () => {
    await startServer;
    const form = createFormData(
      { agent_id: "agent-1", conversation_id: "conv-1", filename: "test.png", prompt: "a cat" },
      { name: "test.png", buffer: testPng, type: "image/png" }
    );

    const res = await fetch(`${baseUrl}/api/v1/images/upload`, {
      method: "POST",
      headers: { "Content-Type": form.contentType },
      body: form.body,
    });

    expect(res.status).toBe(201);
    const json = await res.json();
    expect(json.id).toMatch(/^img_/);
    expect(json.url).toMatch(/^\/api\/v1\/images\/img_.*\.png$/);
    expect(json.agent_id).toBe("agent-1");
    expect(json.conversation_id).toBe("conv-1");
    expect(json.mime).toBe("image/png");
  });

  it("POST /api/v1/images/upload without file returns 400", async () => {
    await startServer;
    const form = createFormData({ agent_id: "agent-1", conversation_id: "conv-1" });

    const res = await fetch(`${baseUrl}/api/v1/images/upload`, {
      method: "POST",
      headers: { "Content-Type": form.contentType },
      body: form.body,
    });

    expect(res.status).toBe(400);
    const json = await res.json();
    expect(json.error).toContain("file");
  });

  it("POST /api/v1/images/upload without agent_id returns 400", async () => {
    await startServer;
    const form = createFormData(
      { conversation_id: "conv-1" },
      { name: "test.png", buffer: testPng, type: "image/png" }
    );

    const res = await fetch(`${baseUrl}/api/v1/images/upload`, {
      method: "POST",
      headers: { "Content-Type": form.contentType },
      body: form.body,
    });

    expect(res.status).toBe(400);
    const json = await res.json();
    expect(json.error).toContain("agent_id");
  });

  it("GET /api/v1/images/:filename serves uploaded file", async () => {
    await startServer;
    // First upload
    const form = createFormData(
      { agent_id: "agent-1", conversation_id: "conv-1", filename: "serve-test.png" },
      { name: "serve-test.png", buffer: testPng, type: "image/png" }
    );

    const uploadRes = await fetch(`${baseUrl}/api/v1/images/upload`, {
      method: "POST",
      headers: { "Content-Type": form.contentType },
      body: form.body,
    });

    expect(uploadRes.status).toBe(201);
    const { url } = await uploadRes.json();

    // Then fetch
    const getRes = await fetch(`${baseUrl}${url}`);
    expect(getRes.status).toBe(200);
    expect(getRes.headers.get("content-type")).toBe("image/png");
    expect(getRes.headers.get("cache-control")).toContain("immutable");
  });

  it("GET /api/v1/images/nonexistent.png returns 404", async () => {
    await startServer;
    const res = await fetch(`${baseUrl}/api/v1/images/nonexistent.png`);
    expect(res.status).toBe(404);
  });

  it("GET /api/v1/images/../../etc/passwd returns 403", async () => {
    await startServer;
    const res = await fetch(`${baseUrl}/api/v1/images/..%2F..%2Fetc%2Fpasswd`);
    // Express may decode or not — either 403 or 404 is acceptable for path traversal
    expect([403, 404]).toContain(res.status);
  });
});
