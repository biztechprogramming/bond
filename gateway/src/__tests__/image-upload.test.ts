/**
 * Tests for the media router — image upload and serving endpoints.
 * Design Doc 104: Agent Image Delivery
 */

import { describe, it, expect, beforeAll, afterAll } from "vitest";
import express from "express";
import { createServer, type Server } from "http";
import { join } from "node:path";
import { mkdirSync, rmSync, existsSync, readFileSync } from "node:fs";
import { createMediaRouter } from "../media/router.js";

const TEST_DIR = join(import.meta.dirname || __dirname, "__test_images__");

// Minimal 1x1 PNG
const PNG_DATA = Buffer.from(
  "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg==",
  "base64",
);

let server: Server;
let baseUrl: string;

beforeAll(async () => {
  process.env.BOND_HOME = TEST_DIR;
  mkdirSync(join(TEST_DIR, "images"), { recursive: true });

  const app = express();
  app.use(createMediaRouter());

  await new Promise<void>((resolve) => {
    server = createServer(app).listen(0, () => {
      const addr = server.address();
      baseUrl = `http://127.0.0.1:${typeof addr === "object" ? addr!.port : 0}`;
      resolve();
    });
  });
});

afterAll(() => {
  server?.close();
  if (existsSync(TEST_DIR)) {
    rmSync(TEST_DIR, { recursive: true, force: true });
  }
});

function makeFormData(fields: Record<string, string>, fileData?: Buffer, fileName?: string): FormData {
  const fd = new FormData();
  for (const [k, v] of Object.entries(fields)) fd.append(k, v);
  if (fileData) {
    fd.append("file", new Blob([fileData], { type: "image/png" }), fileName || "test.png");
  }
  return fd;
}

describe("POST /api/v1/images/upload", () => {
  it("uploads an image and returns metadata", async () => {
    const fd = makeFormData(
      { agent_id: "test-agent", conversation_id: "conv-123", filename: "test.png", prompt: "a test image" },
      PNG_DATA,
    );

    const resp = await fetch(`${baseUrl}/api/v1/images/upload`, { method: "POST", body: fd });

    expect(resp.status).toBe(201);
    const json = await resp.json();
    expect(json.id).toMatch(/^img_[0-9a-f]{12}$/);
    expect(json.url).toMatch(/^\/api\/v1\/images\/img_[0-9a-f]{12}\.png$/);
    expect(json.agent_id).toBe("test-agent");
    expect(json.conversation_id).toBe("conv-123");

    // Verify sidecar JSON was written
    const metaPath = join(TEST_DIR, "images", `${json.id}.json`);
    expect(existsSync(metaPath)).toBe(true);
    const meta = JSON.parse(readFileSync(metaPath, "utf-8"));
    expect(meta.prompt).toBe("a test image");
  });

  it("returns 400 when agent_id is missing", async () => {
    const fd = makeFormData({ conversation_id: "conv-123" }, PNG_DATA);
    const resp = await fetch(`${baseUrl}/api/v1/images/upload`, { method: "POST", body: fd });
    expect(resp.status).toBe(400);
  });

  it("returns 400 when no file is provided", async () => {
    const fd = makeFormData({ agent_id: "test-agent", conversation_id: "conv-123" });
    const resp = await fetch(`${baseUrl}/api/v1/images/upload`, { method: "POST", body: fd });
    expect(resp.status).toBe(400);
  });
});

describe("GET /api/v1/images/:filename", () => {
  let uploadedUrl: string;

  beforeAll(async () => {
    const fd = makeFormData(
      { agent_id: "test-agent", conversation_id: "conv-456" },
      PNG_DATA,
      "serve-test.png",
    );
    const resp = await fetch(`${baseUrl}/api/v1/images/upload`, { method: "POST", body: fd });
    expect(resp.status).toBe(201);
    const json = await resp.json();
    uploadedUrl = json.url;
    expect(uploadedUrl).toBeDefined();
  });

  it("serves an uploaded image with correct headers", async () => {
    const resp = await fetch(`${baseUrl}${uploadedUrl}`);
    expect(resp.status).toBe(200);
    expect(resp.headers.get("content-type")).toBe("image/png");
    expect(resp.headers.get("cache-control")).toBe("public, max-age=31536000, immutable");
  });

  it("returns 404 for non-existent images", async () => {
    const resp = await fetch(`${baseUrl}/api/v1/images/img_nonexistent.png`);
    expect(resp.status).toBe(404);
  });
});
