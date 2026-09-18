"""Artifact schema — the Capability. This is load-bearing wall #1.

A Capability is the typed, versioned, reviewable distillate of one discovery
run: *what* to do (steps), *how to point at* each control (locator strategy),
*what to expect* after each step (assertion), and *how to classify* failures
(error handling). It is deliberately decoupled from the raw LLM transcript —
replay (Phase 4) consumes this, never the model's monologue.

Design principles (see DESIGN.md §5):
  1. Intent, not coordinates — we record *how to locate* a control, never pixels.
  2. Decoupled from the transcript — distilled, typed steps, human-reviewable.
  3. Every step carries an assertion — we never assume a click worked.
  4. Errors are first-class — each step declares its failure modes.
"""
from datetime import datetime, timezone
from typing import Literal, Optional

from pydantic import BaseModel, Field

Action = Literal["click", "type", "select", "navigate", "wait", "read"]
Strategy = Literal["accessibility", "text", "css", "xpath", "visual"]


class LocatorStrategy(BaseModel):
    """How to point at a control. `strategy` is the only place a surface leaks in."""
    strategy: Strategy = "accessibility"
    role: Optional[str] = None          # accessibility strategy
    name: Optional[str] = None          # accessibility strategy
    value: Optional[str] = None         # css/xpath expression, or text for 'text' strategy
    reasoning: str = ""                 # why this locator was chosen — for review + repair


class Assertion(BaseModel):
    """Per-step checkpoint: what we expect after this action, and what to do if it's absent."""
    expect: str
    on_mismatch: Literal["fail", "retry", "ignore"] = "fail"


class ErrorHandling(BaseModel):
    """How to classify and react to a failure at this step."""
    outcome: Literal["business", "recoverable", "hard"] = "hard"
    then: Literal["fail", "retry", "escalate"] = "fail"


class Step(BaseModel):
    action: Action
    target: Optional[LocatorStrategy] = None   # None for navigate/wait
    value: Optional[str] = None                # input for type/select
    assertion: Optional[Assertion] = None
    on_error: Optional[ErrorHandling] = None


class InputSpec(BaseModel):
    name: str
    type: str = "str"
    required: bool = True
    description: str = ""


class OutputSpec(BaseModel):
    name: str
    type: str = "str"
    source: str = ""       # which step/page the value is read from


class CapabilityMeta(BaseModel):
    name: str
    version: str = "1.0.0"
    description: str = ""
    surface: Literal["browser", "desktop"] = "browser"
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class Capability(BaseModel):
    """A reusable, versioned description of one flow."""
    meta: CapabilityMeta
    inputs: list[InputSpec] = []
    outputs: list[OutputSpec] = []
    checkpoint: str = ""        # how we know the goal was reached
    steps: list[Step]


def serialize(
    discovery: dict,
    name: str,
    description: str = "",
    inputs: Optional[list[dict]] = None,
    outputs: Optional[list[dict]] = None,
    checkpoint: str = "",
    version: str = "1.0.0",
) -> Capability:
    """Distill a discovery run into a Capability.

    `discovery` is the dict returned by `run_discovery`. Terminal markers
    (`done` / `fail`) are not replayable actions and are dropped from `steps`;
    the goal condition is captured in `checkpoint` instead.
    """
    steps: list[Step] = []
    for s in discovery.get("steps", []):
        action = s.get("action")
        if action in ("done", "fail", None):
            continue

        target = None
        if s.get("target"):
            t = s["target"]
            target = LocatorStrategy(
                strategy=t.get("strategy", "accessibility"),
                role=t.get("role"),
                name=t.get("name"),
                reasoning=s.get("thought", ""),
            )

        assertion = Assertion(expect=s["expect"]) if s.get("expect") else None

        steps.append(Step(
            action=action,
            target=target,
            value=s.get("value"),
            assertion=assertion,
        ))

    # If no checkpoint was supplied, fall back to the terminal step's reasoning.
    if not checkpoint and discovery.get("steps"):
        last = discovery["steps"][-1]
        if last.get("thought"):
            checkpoint = last["thought"]

    return Capability(
        meta=CapabilityMeta(name=name, description=description, version=version),
        inputs=[InputSpec(**i) for i in (inputs or [])],
        outputs=[OutputSpec(**o) for o in (outputs or [])],
        checkpoint=checkpoint,
        steps=steps,
    )


def to_json(cap: Capability) -> str:
    """Human-readable, diffable JSON rendering of a Capability."""
    return cap.model_dump_json(indent=2)
