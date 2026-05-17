import { describe, it, expect } from "vitest";
import type { ValidationError } from "@/lib/types";
import {
  collectMissingColumnCandidates,
  extractColumnCandidate,
} from "@/lib/validation-autocomplete";

const makeError = (overrides: Partial<ValidationError>): ValidationError => ({
  step_name: "filter_sales",
  field: "column",
  message: "Column 'ammount' not found",
  suggestion: null,
  ...overrides,
});

describe("validation-autocomplete helpers", () => {
  it("extracts quoted column names from validation messages", () => {
    expect(extractColumnCandidate(makeError({ message: "Column 'ammount' not found" }))).toBe(
      "ammount"
    );
  });

  it("returns null when message has no column token", () => {
    expect(extractColumnCandidate(makeError({ message: "Invalid join type: sideway" }))).toBeNull();
  });

  it("collects unique missing columns without existing suggestions", () => {
    const candidates = collectMissingColumnCandidates([
      makeError({ message: "Column 'ammount' not found", suggestion: null }),
      makeError({ message: "Column 'ammount' not found", suggestion: null }),
      makeError({ message: "Column 'stauts' not found", suggestion: null }),
      makeError({ message: "Column 'region' not found", suggestion: "Did you mean 'Region'?" }),
    ]);

    expect(candidates).toEqual(["ammount", "stauts"]);
  });
});
