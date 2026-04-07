import { describe, expect, it } from "vitest";
import {
  DEFAULT_PIPELINE_YAML,
  extractPipelineName,
  hasNonEmptyFileId,
  removeFileIdLines,
  upsertFileIdInFirstLoadStep,
} from "@/lib/pipeline-yaml";

describe("pipeline-yaml helpers", () => {
  it("extracts top-level pipeline name", () => {
    expect(extractPipelineName(DEFAULT_PIPELINE_YAML)).toBe("my_pipeline");
  });

  it("returns null when pipeline name is missing", () => {
    expect(extractPipelineName("pipeline:\n  steps:\n    - name: load_file")).toBeNull();
  });

  it("detects non-empty file_id values", () => {
    expect(hasNonEmptyFileId('pipeline:\n  steps:\n    - name: load\n      file_id: ""')).toBe(
      false,
    );
    expect(
      hasNonEmptyFileId(
        'pipeline:\n  steps:\n    - name: load\n      file_id: "00000000-0000-0000-0000-000000000001"',
      ),
    ).toBe(true);
  });

  it("inserts file_id under first load step when missing", () => {
    const updated = upsertFileIdInFirstLoadStep(
      DEFAULT_PIPELINE_YAML,
      "00000000-0000-0000-0000-000000000001",
    );
    expect(updated).toContain('type: load\n      file_id: "00000000-0000-0000-0000-000000000001"');
  });

  it("replaces existing file_id when present", () => {
    const yamlWithFileId = `pipeline:
  name: p1
  steps:
    - name: load_file
      type: load
      file_id: "old-id"
    - name: save_file
      type: save
      input: load_file
      filename: output`;
    const updated = upsertFileIdInFirstLoadStep(
      yamlWithFileId,
      "00000000-0000-0000-0000-000000000002",
    );
    expect(updated).toContain('file_id: "00000000-0000-0000-0000-000000000002"');
    expect(updated).not.toContain('file_id: "old-id"');
  });

  it("removes all file_id lines", () => {
    const yamlWithFileIds = `pipeline:
  name: p1
  steps:
    - name: load_file
      type: load
      file_id: "old-id"
    - name: load_file_2
      type: load
      file_id: "another-id"`;
    const updated = removeFileIdLines(yamlWithFileIds);
    expect(updated).not.toContain("file_id:");
  });
});
