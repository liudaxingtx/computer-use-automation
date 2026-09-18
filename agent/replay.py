"""Deterministic replay — the engine that consumes a Capability and re-runs it
with NO LLM in the loop. This is load-bearing wall #2.

Replay is act → assert → branch. It re-resolves each locator strategy against
the live page, acts, then classifies the resulting page into one of three
states (success / business_outcome / failure) using deterministic text signals
— never by asking a model to look again.
"""
from datetime import datetime, timezone
from typing import Any, Literal, Optional, cast

from playwright.sync_api import Page
from pydantic import BaseModel

from .artifact import Capability, LocatorStrategy, Step

Result = Literal["success", "business_outcome", "failure"]


class ReplayRun(BaseModel):
    """The record produced by every replay (see DESIGN §7)."""
    capability: str
    version: str
    inputs: dict = {}
    result: Result
    outputs: dict = {}
    diagnostic: str = ""           # on failure: which step, expected vs observed
    steps: list[dict] = []         # per-step execution log
    screenshot: Optional[str] = None   # path to the failure screenshot
    started_at: datetime
    duration_ms: int


def _resolve_locator(page: Page, target: LocatorStrategy):
    """Turn a LocatorStrategy into a Playwright locator, with a fallback chain.

    accessibility: role + name, falling back to role + ordinal when name is empty.
    """
    if target.strategy == "accessibility":
        role = cast(Any, target.role)
        if target.name:
            return page.get_by_role(role, name=target.name)
        if target.ordinal is not None:
            # Empty-name fallback (see DESIGN decision log): nth control of this role.
            return page.get_by_role(role).nth(target.ordinal - 1)
        return page.get_by_role(role)
    if target.strategy == "text":
        return page.get_by_text(target.value or "")
    if target.strategy == "css":
        return page.locator(target.value or "")
    if target.strategy == "xpath":
        return page.locator(f"xpath={target.value or ''}")
    raise ValueError(f"unsupported locator strategy: {target.strategy}")


def _interpolate(value: Optional[str], inputs: dict) -> str:
    """Substitute {param} placeholders in a step value with real inputs."""
    if value is None:
        return ""
    s = str(value)
    for k, v in inputs.items():
        s = s.replace("{" + k + "}", str(v))
    return s


def _page_text(page: Page) -> str:
    try:
        return page.inner_text("body")
    except Exception:
        return ""


def _classify(page: Page, cap: Capability) -> Optional[tuple[Result, str]]:
    """Match the current page against the capability's outcome patterns.

    Returns (result, label) or None. Failure takes precedence over business
    outcomes; both take precedence over continuing to the next step.
    """
    text = _page_text(page)
    for p in cap.failure_patterns:
        if p.text in text:
            return ("failure", p.label or p.text)
    for p in cap.business_outcomes:
        if p.text in text:
            return ("business_outcome", p.label or p.text)
    return None


def _execute(page: Page, step: Step, inputs: dict):
    """Perform a single step's action. Raises on locator/timeout failures."""
    action = step.action
    if action == "click":
        _resolve_locator(page, step.target).click()
    elif action == "type":
        _resolve_locator(page, step.target).fill(_interpolate(step.value, inputs))
    elif action == "select":
        _resolve_locator(page, step.target).select_option(_interpolate(step.value, inputs))
    elif action == "navigate":
        page.goto(_interpolate(step.value, inputs))
    elif action == "wait":
        page.wait_for_timeout(2000)
    # "read" is declared in the schema but not yet produced by discovery.
    else:
        raise ValueError(f"unsupported action: {action}")


def _max_attempts(step: Step) -> int:
    """Bounded retries for recoverable conditions (design §6): a step declared
    `on_error.then = retry` gets a small number of attempts, otherwise one shot."""
    if step.on_error and step.on_error.then == "retry":
        return 3
    return 1


def replay(page: Page, cap: Capability, inputs: dict,
           screenshot_dir: Optional[str] = None) -> ReplayRun:
    """Deterministically re-run a Capability. Returns a ReplayRun record."""
    started = datetime.now(timezone.utc)
    steps_log: list[dict] = []

    def _finish(result: Result, diagnostic: str = "", outputs: dict | None = None,
                screenshot: Optional[str] = None) -> ReplayRun:
        duration = int((datetime.now(timezone.utc) - started).total_seconds() * 1000)
        return ReplayRun(
            capability=cap.meta.name,
            version=cap.meta.version,
            inputs=inputs,
            result=result,
            outputs=outputs or {},
            diagnostic=diagnostic,
            steps=steps_log,
            screenshot=screenshot,
            started_at=started,
            duration_ms=duration,
        )

    for i, step in enumerate(cap.steps, start=1):
        # Execute with bounded retries for recoverable conditions.
        attempts = _max_attempts(step)
        last_err: Optional[Exception] = None
        for attempt in range(1, attempts + 1):
            try:
                _execute(page, step, inputs)
                page.wait_for_load_state("domcontentloaded")
                last_err = None
                break
            except Exception as e:
                last_err = e
                if attempt < attempts:
                    page.wait_for_timeout(1000)
        if last_err is not None:
            steps_log.append({"step": i, "action": step.action, "ok": False,
                              "error": str(last_err), "attempts": attempts})
            shot = _save_screenshot(page, screenshot_dir, cap.meta.name)
            return _finish("failure", f"step {i} ({step.action}) failed after "
                                      f"{attempts} attempt(s): {last_err}", screenshot=shot)

        outcome = _classify(page, cap)
        steps_log.append({"step": i, "action": step.action, "ok": True,
                          "outcome": outcome[0] if outcome else None})
        if outcome:
            result, label = outcome
            shot = _save_screenshot(page, screenshot_dir, cap.meta.name) if result == "failure" else None
            return _finish(result, diagnostic=label, screenshot=shot)

    # All steps executed without an early outcome — check the success checkpoint.
    text = _page_text(page)
    if cap.checkpoint_text and cap.checkpoint_text in text:
        return _finish("success")
    diag = (f"checkpoint not reached; expected page text containing "
            f"{cap.checkpoint_text!r}, got page of {len(text)} chars")
    shot = _save_screenshot(page, screenshot_dir, cap.meta.name)
    return _finish("failure", diagnostic=diag, screenshot=shot)


def _save_screenshot(page: Page, directory: Optional[str], name: str) -> Optional[str]:
    if not directory:
        return None
    import os
    os.makedirs(directory, exist_ok=True)
    path = os.path.join(directory, f"{name}_failure.png")
    page.screenshot(path=path)
    return path
