import { describe, it, expect } from "vitest";
import { AllowList } from "../channels/allowlist.js";

describe("AllowList", () => {
  it("allows listed IDs (case-insensitive)", () => {
    const al = new AllowList(["User123", "user456"]);
    expect(al.isAllowed("user123")).toBe(true);
    expect(al.isAllowed("USER123")).toBe(true);
    expect(al.isAllowed("user456")).toBe(true);
    expect(al.isAllowed("unknown")).toBe(false);
  });

  it("supports wildcard", () => {
    const al = new AllowList(["*"]);
    expect(al.isAllowed("anyone")).toBe(true);
    expect(al.isAllowed("")).toBe(true);
  });

  it("add and remove", () => {
    const al = new AllowList([]);
    expect(al.isAllowed("user1")).toBe(false);
    al.add("User1");
    expect(al.isAllowed("user1")).toBe(true);
    al.remove("USER1");
    expect(al.isAllowed("user1")).toBe(false);
  });

  it("toArray returns all IDs lowercase", () => {
    const al = new AllowList(["Alice", "BOB"]);
    const arr = al.toArray();
    expect(arr).toContain("alice");
    expect(arr).toContain("bob");
    expect(arr.length).toBe(2);
  });

  it("defaults to empty list", () => {
    const al = new AllowList();
    expect(al.isAllowed("anyone")).toBe(false);
    expect(al.toArray()).toEqual([]);
  });
});
