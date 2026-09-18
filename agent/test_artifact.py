"""End-to-end: run a discovery pass, distill it into a Capability, inspect the JSON."""
from playwright.sync_api import sync_playwright

from agent.artifact import serialize, to_json
from agent.loop import run_discovery

TASK = "Search for member 1001, view their detail, then deactivate the account."

with sync_playwright() as p:
    b = p.chromium.launch(headless=True)
    page = b.new_page()
    page.goto("http://localhost:9000/")
    discovery = run_discovery(page, TASK)
    b.close()

cap = serialize(
    discovery,
    name="deactivate_member",
    description="Look up a member by ID and deactivate their account.",
    inputs=[{"name": "member_id", "type": "str", "required": True,
             "description": "the member ID to deactivate"}],
    outputs=[{"name": "status", "type": "str", "source": "result page"}],
    checkpoint="result page shows SUCCESS for the member",
)

print("=== Capability JSON (reviewable, diffable) ===")
print(to_json(cap))
print()

# Structural checks
assert cap.meta.name == "deactivate_member"
assert cap.meta.version == "1.0.0"
assert cap.meta.surface == "browser"
assert len(cap.inputs) == 1
assert cap.checkpoint != ""
assert len(cap.steps) >= 4, "expected >=4 replayable steps (done/fail dropped)"

for s in cap.steps:
    assert s.action not in ("done", "fail"), "terminal markers must not reach the artifact"
    if s.action in ("click", "type", "select"):
        assert s.target is not None, f"{s.action} step missing target"
        assert s.target.strategy == "accessibility"
        assert s.target.role, "target missing role"

print("OK — Capability serialized and structurally valid")
