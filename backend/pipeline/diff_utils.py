"""Helpers for recovering pipeline YAML pasted with unified-diff markers."""

_DIFF_HEADER_PREFIXES = ("diff --git", "diff --combined", "index ")
_HUNK_HEADER_PREFIX = "@@"


def _is_diff_header(line: str) -> bool:
    """True only for actual unified-diff header lines.

    A bare ``---`` document-start marker is valid YAML and must be kept;
    diff headers always carry a filename (``--- a/file``) or are hunk
    headers (``@@ ... @@``).
    """
    if line.startswith(_HUNK_HEADER_PREFIX):
        return True
    if line.startswith(_DIFF_HEADER_PREFIXES):
        return True
    return line.startswith(("--- ", "---\t", "+++ ", "+++\t"))


def strip_diff_markers(text: str) -> str:
    """Remove unified diff markers, recovering the 'after' state.

    Only activates when the text actually contains diff headers
    (``--- <file>`` / ``+++ <file>`` / ``@@`` / ``diff --git``). Plain
    YAML — including column-0 content lines such as list items
    (``- name: ...``) or a ``---`` document marker — is returned
    byte-for-byte unchanged (M-041).

    Within hunks (after the first ``@@`` header), leading ``+``/``-``/
    `` `` diff prefixes are resolved: additions keep their content,
    deletions are dropped, context lines are unprefixed. Outside hunks,
    only header lines are removed; everything else passes through.
    """
    lines = text.splitlines()
    if not any(_is_diff_header(line) for line in lines):
        return text

    result: list[str] = []
    in_hunk = False
    for line in lines:
        if _is_diff_header(line):
            # A hunk header opens the hunk region; file headers are dropped.
            if line.startswith(_HUNK_HEADER_PREFIX):
                in_hunk = True
            continue
        if line.startswith("\\"):
            # "\ No newline at end of file" marker.
            continue
        if in_hunk:
            if line.startswith("+"):
                result.append(line[1:])
            elif line.startswith("-"):
                continue
            elif line.startswith(" "):
                result.append(line[1:])
            else:
                # Not a diff-prefixed line (e.g. pasted YAML without a
                # marker prefix): keep it rather than corrupt content.
                result.append(line)
        else:
            result.append(line)
    return "\n".join(result)
