"""Checks on the init frame before a run is allowed to continue."""

from __future__ import annotations

from typing import Any, Iterable


class ToolListViolation(RuntimeError):
    """The agent can see a tool outside the allowed set."""


class WrongCredential(RuntimeError):
    """The child process is not using the workspace key."""


def unexpected_tools(init_data: dict[str, Any], allowed: Iterable[str]) -> list[str]:
    allowed_set = set(allowed)
    return sorted(t for t in init_data.get("tools", []) if t not in allowed_set)


def check_init(init_data: dict[str, Any], allowed: Iterable[str]) -> None:
    """Raises unless every visible tool is allowed and the key is ours."""
    extra = unexpected_tools(init_data, allowed)
    if extra:
        raise ToolListViolation(f"tools outside the allowed set: {', '.join(extra)}")
    source = init_data.get("apiKeySource")
    if source != "ANTHROPIC_API_KEY":
        raise WrongCredential(f"expected apiKeySource ANTHROPIC_API_KEY, got {source!r}")
