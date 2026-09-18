"""Safety guardrails — a configurable allowlist enforced in both the agent loop
and replay (design §9).

The allowlist is the *policy*; `check_action` is the *enforcement point*. Both
discovery and replay call it before acting, so the agent cannot act outside it.
"""
from typing import Optional
from urllib.parse import urlparse

from pydantic import BaseModel


class SafetyPolicy(BaseModel):
    """What the agent is allowed to do.

    - allowed_domains: hostnames the agent may navigate to / act on.
    - allowed_paths: path prefixes allowed within a domain (any path if empty).
    - allowed_actions: the action vocabulary the agent may perform.
    - risky_actions: actions that must be gated (blocked in replay, escalated
      to a human in discovery).
    """
    allowed_domains: list[str] = ["localhost", "127.0.0.1"]
    allowed_paths: list[str] = ["/"]
    allowed_actions: list[str] = ["click", "type", "select", "navigate", "wait", "read"]
    risky_actions: list[str] = []


def url_allowed(url: str, policy: SafetyPolicy) -> bool:
    parsed = urlparse(url)
    host = parsed.hostname or ""
    if host not in policy.allowed_domains:
        return False
    path = parsed.path or "/"
    return any(path.startswith(p) for p in policy.allowed_paths)


def action_allowed(action: str, policy: SafetyPolicy) -> bool:
    return action in policy.allowed_actions


def action_risky(action: str, policy: SafetyPolicy) -> bool:
    return action in policy.risky_actions


def check_action(action: str, url: Optional[str], policy: SafetyPolicy) -> tuple[bool, str]:
    """Enforce the allowlist before an action. Returns (ok, reason)."""
    if not action_allowed(action, policy):
        return False, f"action {action!r} is not in the allowlist"
    if action == "navigate" and url and not url_allowed(url, policy):
        return False, f"url {url!r} is outside the allowed domains"
    return True, ""


def check_risky(action: str, policy: SafetyPolicy) -> bool:
    """True if this action must be gated (blocked or escalated)."""
    return action_risky(action, policy)
