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
    ordinal: Optional[int] = None       # 1-based position within the role; fallback when name is empty
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
    label: str = ""        # on-page field name for key-value extraction (defaults to name.upper())
    extract: str = ""      # optional CSS selector to read the value directly


class OutcomePattern(BaseModel):
    """A deterministic, text-based signal for classifying a replay result."""
    text: str          # page text contains this → the outcome applies
    label: str = ""    # human-readable meaning, e.g. "member not found"


class CapabilityMeta(BaseModel):
    name: str
    version: str = "1.0.0"
    description: str = ""
    surface: Literal["browser", "desktop"] = "browser"
    domain: str = ""        # primary domain of the target system (e.g. "bank-a.com") — groups tasks per site
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class Capability(BaseModel):
    """A reusable, versioned description of one flow."""
    meta: CapabilityMeta
    inputs: list[InputSpec] = []
    outputs: list[OutputSpec] = []
    checkpoint: str = ""                        # human-readable goal description
    checkpoint_text: str = ""                   # deterministic success signal (page text contains this)
    business_outcomes: list[OutcomePattern] = []   # "no such member" — a legitimate answer
    failure_patterns: list[OutcomePattern] = []    # "access denied" — a hard stop
    steps: list[Step]


def serialize(
    discovery: dict,
    name: str,
    description: str = "",
    inputs: Optional[list[dict]] = None,
    outputs: Optional[list[dict]] = None,
    checkpoint: str = "",
    version: str = "1.0.0",
    checkpoint_text: str = "",
    business_outcomes: Optional[list[dict]] = None,
    failure_patterns: Optional[list[dict]] = None,
    value_params: Optional[dict] = None,
    encrypt_values: bool = False,
    tenant_id: str = "default",
    domain: str = "",
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
                ordinal=t.get("ordinal"),
                reasoning=s.get("thought", ""),
            )

        assertion = Assertion(expect=s["expect"]) if s.get("expect") else None

        # Parameterize the concrete input value into a {param} placeholder so the
        # artifact records *intent*, not the specific customer value (design
        # principle #1). Any concrete value that cannot be parameterized is
        # encrypted at rest (design principle #6) instead of stored in plaintext.
        from . import crypto
        value = s.get("value")
        if value_params and value in value_params:
            value = "{" + value_params[value] + "}"
        elif value and encrypt_values:
            value = crypto.encrypt(str(value), crypto.get_key(tenant_id))

        steps.append(Step(
            action=action,
            target=target,
            value=value,
            assertion=assertion,
        ))

    # If no checkpoint was supplied, fall back to the terminal step's reasoning.
    if not checkpoint and discovery.get("steps"):
        last = discovery["steps"][-1]
        if last.get("thought"):
            checkpoint = last["thought"]

    return Capability(
        meta=CapabilityMeta(name=name, description=description, version=version, domain=domain),
        inputs=[InputSpec(**i) for i in (inputs or [])],
        outputs=[OutputSpec(**o) for o in (outputs or [])],
        checkpoint=checkpoint,
        checkpoint_text=checkpoint_text,
        business_outcomes=[OutcomePattern(**o) for o in (business_outcomes or [])],
        failure_patterns=[OutcomePattern(**o) for o in (failure_patterns or [])],
        steps=steps,
    )


def to_json(cap: Capability) -> str:
    """Human-readable, diffable JSON rendering of a Capability."""
    return cap.model_dump_json(indent=2)


def auto_serialize(discovery: dict, name: str, description: str = "",
                   url: str = "") -> Capability:
    """Distill a discovery run into a Capability with NO hand-specified metadata.

    Everything is inferred from the discovery transcript:
      - checkpoint_text: the LLM's `done` decision declares the exact success
        text that appears on the page (see loop.SYSTEM_PROMPT).
      - outputs: the fields the LLM extracted at `done`, each keyed by name and
        matched back to the on-page label (normalized).
      - inputs: every `type` step is parameterized — its concrete value becomes
        a `{param}` placeholder. The param name is the extracted output whose
        value equals the typed value, else `param_N`.

    business_outcomes / failure_patterns are left empty: a discovery run only
    sees the happy path, so error-state signals are filled in later.
    """
    from urllib.parse import urlparse

    outputs = discovery.get("outputs", {}) or {}
    steps = discovery.get("steps", [])

    # --- outputs: field name -> on-page label ---
    output_specs = [
        {"name": str(k), "type": "str", "source": "result page", "label": str(k)}
        for k in outputs.keys()
    ]

    # --- inputs: parameterize each `type` value ---
    value_to_param = {str(v): str(k) for k, v in outputs.items()}
    input_specs: list[dict] = []
    value_params: dict[str, str] = {}
    seen: dict[str, str] = {}
    param_i = 0
    for s in steps:
        if s.get("action") != "type" or s.get("value") is None:
            continue
        val = str(s["value"])
        if val in value_to_param:
            pname = value_to_param[val]
        elif val in seen:
            pname = seen[val]
        else:
            param_i += 1
            pname = f"param_{param_i}"
            seen[val] = pname
        value_params[val] = pname
        if pname not in [i["name"] for i in input_specs]:
            input_specs.append({"name": pname, "type": "str", "required": True})

    checkpoint_text = discovery.get("checkpoint_text", "") or ""
    domain = urlparse(url).netloc or ""

    return serialize(
        discovery,
        name=name,
        description=description,
        inputs=input_specs,
        outputs=output_specs,
        checkpoint="goal reached",
        checkpoint_text=checkpoint_text,
        business_outcomes=[],
        failure_patterns=[],
        value_params=value_params,
        domain=domain,
    )
