export const DEFAULT_PIPELINE_YAML = `pipeline:
  name: my_pipeline
  steps:
    - name: load_file
      type: load
    - name: save_file
      type: save
      input: load_file
      filename: output_file
`;

export function extractPipelineName(yamlConfig: string): string | null {
  const lines = yamlConfig.split(/\r?\n/);
  let inPipeline = false;
  let pipelineIndent = 0;

  for (const line of lines) {
    if (!inPipeline) {
      const pipelineMatch = line.match(/^(\s*)pipeline:\s*$/);
      if (pipelineMatch) {
        inPipeline = true;
        pipelineIndent = pipelineMatch[1].length;
      }
      continue;
    }

    if (!line.trim()) {
      continue;
    }

    const indent = line.match(/^(\s*)/)?.[1].length ?? 0;
    if (indent <= pipelineIndent) {
      break;
    }

    const nameMatch = line.match(/^\s*name:\s*(.+?)\s*$/);
    if (!nameMatch) {
      continue;
    }

    const rawName = nameMatch[1].replace(/\s+#.*$/, "").trim();
    if (!rawName) {
      return null;
    }
    return rawName.replace(/^['"]|['"]$/g, "");
  }

  return null;
}

export function hasNonEmptyFileId(yamlConfig: string): boolean {
  const fileIdMatch = yamlConfig.match(/^\s*file_id:\s*["']?([^"'\n]*)["']?\s*$/m);
  return fileIdMatch ? fileIdMatch[1].trim().length > 0 : false;
}

export function upsertFileIdInFirstLoadStep(yamlConfig: string, fileId: string): string {
  const replacedExisting = yamlConfig.replace(
    /^(\s*)file_id:\s*.*$/m,
    (_match, indent: string) => `${indent}file_id: "${fileId}"`,
  );
  if (replacedExisting !== yamlConfig) {
    return replacedExisting;
  }

  const lines = yamlConfig.split(/\r?\n/);
  for (let index = 0; index < lines.length; index += 1) {
    const line = lines[index];
    if (!/^\s*type:\s*load\s*$/.test(line)) {
      continue;
    }

    const indent = line.match(/^(\s*)/)?.[1] ?? "";
    lines.splice(index + 1, 0, `${indent}file_id: "${fileId}"`);
    return lines.join("\n");
  }

  return yamlConfig;
}

export function removeFileIdLines(yamlConfig: string): string {
  return yamlConfig.replace(/^\s*file_id:\s*.*\n?/gm, "");
}
