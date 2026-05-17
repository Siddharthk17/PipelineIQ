import { describe, it, expect, vi, beforeEach } from "vitest";
import { cn } from "@/lib/utils";
import { API_BASE_URL, API_V1 } from "@/lib/constants";

describe("cn() — classname merge utility", () => {
  it("merges simple class strings", () => {
    expect(cn("foo", "bar")).toBe("foo bar");
  });

  it("handles conditional classes", () => {
    expect(cn("base", false && "hidden", "end")).toBe("base end");
  });

  it("merges tailwind conflicts correctly", () => {
    const result = cn("p-4", "p-2");
    expect(result).toBe("p-2");
  });

  it("returns empty string with no args", () => {
    expect(cn()).toBe("");
  });

  it("handles undefined and null inputs", () => {
    expect(cn("a", undefined, null, "b")).toBe("a b");
  });
});

describe("constants", () => {
  it("API_BASE_URL is empty string (proxied)", () => {
    expect(API_BASE_URL).toBe("");
  });

  it("API_V1 is /api/v1", () => {
    expect(API_V1).toBe("/api/v1");
  });
});
