import type { ValidationError } from "@/lib/types";

const QUOTED_COLUMN_PATTERN = /column ['"`]([^'"`]+)['"`]/i;
const COLUMN_FALLBACK_PATTERN = /column ([a-zA-Z0-9_.-]+)/i;

export function extractColumnCandidate(error: ValidationError): string | null {
  const message = (error.message || "").trim();
  if (!message) {
    return null;
  }

  const quotedMatch = message.match(QUOTED_COLUMN_PATTERN);
  if (quotedMatch?.[1]) {
    return quotedMatch[1];
  }

  const fallbackMatch = message.match(COLUMN_FALLBACK_PATTERN);
  if (fallbackMatch?.[1]) {
    return fallbackMatch[1];
  }

  return null;
}

export function collectMissingColumnCandidates(
  errors: ValidationError[],
): string[] {
  const seen = new Set<string>();
  const candidates: string[] = [];

  for (const error of errors) {
    if (error.suggestion) {
      continue;
    }

    const candidate = extractColumnCandidate(error);
    if (!candidate) {
      continue;
    }

    const normalized = candidate.toLowerCase();
    if (seen.has(normalized)) {
      continue;
    }

    seen.add(normalized);
    candidates.push(candidate);
  }

  return candidates;
}
