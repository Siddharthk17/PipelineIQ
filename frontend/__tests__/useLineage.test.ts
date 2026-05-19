import { describe, expect, it } from "vitest";

import { ApiError } from "@/lib/api";
import { shouldRetryLineageGraphQuery } from "@/hooks/useLineage";

describe("shouldRetryLineageGraphQuery", () => {
  it("retries transient lineage 404s a small number of times", () => {
    const err = new ApiError(404, "Lineage graph not found");
    expect(shouldRetryLineageGraphQuery(0, err)).toBe(true);
    expect(shouldRetryLineageGraphQuery(1, err)).toBe(true);
    expect(shouldRetryLineageGraphQuery(2, err)).toBe(false);
  });

  it("does not retry other failures", () => {
    expect(shouldRetryLineageGraphQuery(0, new ApiError(403, "Forbidden"))).toBe(false);
    expect(shouldRetryLineageGraphQuery(0, new Error("boom"))).toBe(false);
  });
});
