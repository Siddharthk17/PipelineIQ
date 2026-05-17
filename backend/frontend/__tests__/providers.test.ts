import { describe, expect, it } from "vitest";

import { shouldRetryQuery } from "@/app/providers";

describe("shouldRetryQuery", () => {
  it("does not retry on 4xx ApiError statuses", () => {
    expect(
      shouldRetryQuery(0, {
        status: 400,
      }),
    ).toBe(false);
    expect(
      shouldRetryQuery(0, {
        status: 429,
      }),
    ).toBe(false);
  });

  it("retries transient failures up to 3 attempts", () => {
    expect(shouldRetryQuery(0, new Error("boom"))).toBe(true);
    expect(shouldRetryQuery(1, new Error("boom"))).toBe(true);
    expect(shouldRetryQuery(2, new Error("boom"))).toBe(true);
    expect(shouldRetryQuery(3, new Error("boom"))).toBe(false);
  });
});
