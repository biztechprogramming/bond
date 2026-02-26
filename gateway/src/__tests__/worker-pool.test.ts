import { describe, it, expect } from "vitest";
import { WorkerPool } from "../backend/worker-pool.js";

describe("WorkerPool", () => {
  it("caches client by url", () => {
    const pool = new WorkerPool();
    const client = pool.get("http://localhost:18793");
    expect(client).toBeDefined();
  });

  it("returns same client for same url", () => {
    const pool = new WorkerPool();
    const client1 = pool.get("http://localhost:18793");
    const client2 = pool.get("http://localhost:18793");
    expect(client1).toBe(client2);
  });

  it("returns different clients for different urls", () => {
    const pool = new WorkerPool();
    const client1 = pool.get("http://localhost:18793");
    const client2 = pool.get("http://localhost:18794");
    expect(client1).not.toBe(client2);
  });

  it("remove clears client", () => {
    const pool = new WorkerPool();
    const client1 = pool.get("http://localhost:18793");
    pool.remove("http://localhost:18793");
    const client2 = pool.get("http://localhost:18793");
    expect(client1).not.toBe(client2);
  });
});
