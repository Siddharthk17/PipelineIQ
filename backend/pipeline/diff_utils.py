import re

def strip_diff_markers(text: str) -> str:
    """
    Remove unified diff markers (+, -) from the start of lines.
    Early-returns the original text if no diff markers are detected,
    preserving YAML indentation in non-diff content.
    """
    lines = text.splitlines()
    if not any(line.startswith(('+', '-', '@@')) for line in lines):
        return text

    result = []
    for line in lines:
        # Remove unified diff header lines
        if line.startswith(('---', '+++', '@@')):
            continue
        # Remove deletion lines entirely
        if line.startswith('-'):
            continue
        # Strip '+' marker from addition lines, keep remaining content
        if line.startswith('+'):
            result.append(line[1:])
        # Strip leading space from unified diff context lines
        elif line.startswith(' '):
            result.append(line[1:])
        else:
            result.append(line)
    return "\n".join(result)
