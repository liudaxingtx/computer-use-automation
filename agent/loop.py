"""Agent loop — observe → decide → act, running one discovery pass.

This is the "LLM appears exactly once" stage: the model drives a real, live
browser session to *discover* how to accomplish a task. The transcript it
produces is the raw material that Phase 3 distills into a Capability artifact.
"""
from . import llm
from .observe import observe, screenshot_b64

SYSTEM_PROMPT = """You are a computer-use agent discovering how to accomplish a task on a legacy web application.

At each step you are shown:
- the page URL and title
- an accessibility tree of the page (roles, names, structure)
- a numbered menu of interactive elements (buttons, links, inputs)

Decide the single next action. Respond with ONLY a JSON object (no markdown, no prose):

{
  "thought": "short reasoning",
  "action": {
    "kind": "click" | "type" | "select" | "navigate" | "wait" | "done" | "fail",
    "index": 3,
    "value": "text",
    "url": "http://..."
  },
  "expect": "what the page should show after this action (used later for replay assertions)",
  "goal_reached": false,
  "outputs": {},
  "reason": ""
}

Field rules:
- "index" is required for click/type/select — it is the number from the menu.
- "value" is required for type (the text to enter) and select (the option).
- "url" is required for navigate.
- "expect" describes the expected outcome of this action (e.g. "member detail table visible") — write it for every click/type/navigate.
- When the goal is satisfied: kind="done", goal_reached=true, and put the extracted results in "outputs".
- When the page shows an error, denial, or unexpected state: kind="fail" and explain in "reason".
- Take the smallest correct step. Type into a field before clicking its button.
- Never invent an index that is not in the menu."""


def _user_message(task: str, obs: dict, history: list[str], vision_note: str = "") -> str:
    parts = [
        f"TASK: {task}",
        "",
        f"URL: {obs['url']}",
        f"TITLE: {obs['title']}",
        "",
        "ACCESSIBILITY TREE:",
        obs["aria_tree"],
        "",
        "INTERACTIVE ELEMENTS (use these indices):",
        obs["menu"],
    ]
    if vision_note:
        parts += [
            "",
            "VISION FALLBACK (accessibility tree had no interactive elements) — "
            "what the screenshot shows:",
            vision_note,
        ]
    if history:
        parts += ["", "HISTORY OF ACTIONS TAKEN:", *history]
    parts += ["", "Return your decision as a single JSON object."]
    return "\n".join(parts)


def _vision_prompt(task: str) -> str:
    return (
        "You are the visual fallback for a computer-use agent. The accessibility "
        "tree for this page came back empty (canvas or image-only surface).\n"
        f"The task the agent is trying to accomplish: {task}\n"
        "Look at the screenshot and describe concretely: (1) what screen this is, "
        "(2) every interactive control visible (buttons, links, inputs) and what "
        "each one does, (3) where each control sits on screen. Be specific."
    )


def _act(page, decision: dict, obs: dict) -> dict:
    """Execute a single decided action. Returns a structured record
    {action, target, value, desc} — the raw material for the artifact."""
    action = dict(decision.get("action", {}))
    kind = str(action.get("kind", "")).lower()
    idx = action.get("index")

    if kind in ("click", "type", "select") and idx is None:
        raise ValueError(f"decision kind={kind!r} is missing a required 'index'")

    def _target(el) -> dict:
        # The locator strategy is role+name (accessibility), with an ordinal for
        # the empty-name fallback. This is what the artifact freezes so replay can
        # re-resolve the control without the LLM.
        return {
            "strategy": "accessibility",
            "role": el["role"],
            "name": el["name"],
            "ordinal": el.get("ordinal"),
        }

    if kind == "click":
        el = obs["elements"][idx - 1]
        el["locator"].click()
        return {
            "action": "click",
            "target": _target(el),
            "value": None,
            "desc": f"click [{idx}] {el['role']} \"{el['name']}\"",
        }

    if kind == "type":
        el = obs["elements"][idx - 1]
        val = action.get("value") or action.get("text") or action.get("input") or ""
        el["locator"].fill(str(val))
        return {
            "action": "type",
            "target": _target(el),
            "value": str(val),
            "desc": f"type [{idx}] {el['role']} \"{el['name']}\" = {val!r}",
        }

    if kind == "select":
        el = obs["elements"][idx - 1]
        val = action.get("value") or action.get("text") or ""
        el["locator"].select_option(str(val))
        return {
            "action": "select",
            "target": _target(el),
            "value": str(val),
            "desc": f"select [{idx}] {el['role']} = {val!r}",
        }

    if kind == "navigate":
        url = action.get("url", "")
        page.goto(url)
        return {"action": "navigate", "target": None, "value": url, "desc": f"navigate to {url}"}

    if kind == "wait":
        page.wait_for_timeout(2000)
        return {"action": "wait", "target": None, "value": None, "desc": "wait 2s"}

    return {"action": kind, "target": None, "value": None, "desc": f"{kind} (no-op)"}


def run_discovery(page, task: str, max_steps: int = 20, verbose: bool = True) -> dict:
    """Run the observe→decide→act loop until done/fail or step budget runs out."""
    history: list[str] = []
    steps: list[dict] = []

    for step_no in range(1, max_steps + 1):
        obs = observe(page)
        vision_note = ""
        if not obs["elements"]:
            # Non-semantic surface: no interactive elements in the a11y tree.
            # Fall back to K3 vision so the decision model still understands the page.
            img = screenshot_b64(page)
            vision_note = llm.kimi_vision(img, _vision_prompt(task))
            if verbose:
                print(f"[step {step_no}] (vision fallback) {vision_note[:140]}")
        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": _user_message(task, obs, history, vision_note)},
        ]
        decision = llm.deepseek_decide(messages)

        thought = decision.get("thought", "")

        act = _act(page, decision, obs)
        desc = act["desc"]
        kind = act["action"]
        history.append(f"step {step_no}: {desc}")

        steps.append({
            "step": step_no,
            "action": kind,
            "target": act["target"],
            "value": act["value"],
            "expect": decision.get("expect", ""),
            "thought": thought,
            "after_url": page.url,
        })

        if verbose:
            print(f"[step {step_no}] {thought}\n    → {desc}")

        if kind == "done":
            return {
                "status": "success",
                "goal_reached": True,
                "outputs": decision.get("outputs", {}),
                "reason": decision.get("reason", ""),
                "steps": steps,
            }
        if kind == "fail":
            return {
                "status": "failure",
                "goal_reached": False,
                "outputs": {},
                "reason": decision.get("reason", ""),
                "steps": steps,
            }

    return {
        "status": "step_budget_exceeded",
        "goal_reached": False,
        "outputs": {},
        "reason": f"did not finish within {max_steps} steps",
        "steps": steps,
    }
