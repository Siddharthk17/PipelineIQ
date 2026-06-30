"""Strict YAML input guards for user-supplied pipeline definitions."""

from __future__ import annotations

import yaml
from yaml.tokens import AliasToken, AnchorToken

from backend.config import settings


class UnsafeYAML(ValueError):
    """Raised when YAML input violates platform safety limits."""


def validate_yaml_input(
    yaml_text: str,
    *,
    max_bytes: int | None = None,
) -> None:
    """Reject oversized YAML and anchor/alias constructs before parsing."""
    if not isinstance(yaml_text, str):
        raise UnsafeYAML("YAML payload must be a string")

    limit = max_bytes or settings.MAX_PIPELINE_YAML_BYTES
    size = len(yaml_text.encode("utf-8"))
    if size > limit:
        raise UnsafeYAML(
            f"YAML payload exceeds the {limit} byte limit"
        )

    try:
        for token in yaml.scan(yaml_text):
            if isinstance(token, (AnchorToken, AliasToken)):
                raise UnsafeYAML("YAML anchors and aliases are not allowed")
    except UnsafeYAML:
        raise
    except yaml.YAMLError as exc:
        raise UnsafeYAML(f"Invalid YAML syntax: {exc}") from exc
