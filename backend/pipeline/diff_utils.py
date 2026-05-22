import re

def strip_diff_markers(text: str) -> str:
    """
    Remove unified diff markers (+, -) from the start of lines.
    Only removes markers if the line starts with + or - followed by a space,
    or if the entire block looks like a diff.
    """
    lines = text.splitlines()
    cleaned_lines = []
    
    for line in lines:
        # Remove leading '+++ ' or '--- ' (header lines)
        if line.startswith('+++ ') or line.startswith('--- '):
            continue
        # Remove leading '@@ -' (hunk headers)
        if line.startswith('@@'):
            continue
        # Remove leading '+' or '-' if followed by space (actual changes)
        if line.startswith('+ ') or line.startswith('- '):
            cleaned_lines.append(line[1:].strip())
        else:
            cleaned_lines.append(line.strip())
            
    return "\n".join(cleaned_lines).strip()
