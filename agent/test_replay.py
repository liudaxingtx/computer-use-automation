"""Phase 4 end-to-end: discovery → Capability → deterministic replay, exercising
all three result states against the mock's three planted conditions."""
from playwright.sync_api import sync_playwright

from agent.artifact import serialize
from agent.loop import run_discovery
from agent.replay import replay

TASK = "Search for member 1001, view their detail, then deactivate the account."

with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)

    # 1. discovery — the LLM appears exactly once.
    page = browser.new_page()
    page.goto("http://localhost:9000/")
    discovery = run_discovery(page, TASK)
    page.close()

    # 2. distill into a Capability with deterministic outcome signals + parameterization.
    cap = serialize(
        discovery,
        name="deactivate_member",
        description="Look up a member by ID and deactivate their account.",
        inputs=[{"name": "member_id", "type": "str", "required": True}],
        outputs=[{"name": "status", "type": "str", "source": "result page"}],
        checkpoint="result page shows SUCCESS",
        checkpoint_text="SUCCESS",
        business_outcomes=[{"text": "NO SUCH MEMBER", "label": "member not found"}],
        failure_patterns=[{"text": "ACCESS DENIED", "label": "permission denied"}],
        value_params={"1001": "member_id"},
    )

    # 3. replay three scenarios — no LLM in the loop, pure locator re-resolution.
    cases = [("1001", "success"), ("9999", "business_outcome"), ("1002", "failure")]
    for mid, expected in cases:
        page = browser.new_page()
        page.goto("http://localhost:9000/")
        run = replay(page, cap, inputs={"member_id": mid})
        print(f"\n=== replay member_id={mid} → {run.result} (expected {expected}) ===")
        print(f"  diagnostic: {run.diagnostic or '(none)'}")
        print(f"  steps run: {len(run.steps)}")
        assert run.result == expected, f"member {mid}: expected {expected}, got {run.result}"
        page.close()

    browser.close()

print("\nOK — deterministic replay correctly classifies all three result states")
