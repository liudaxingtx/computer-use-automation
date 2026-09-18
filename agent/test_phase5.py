"""Phase 5 end-to-end: encryption at rest, allowlist enforcement, and handoff."""
from typing import Any, cast

from playwright.sync_api import sync_playwright

from agent import crypto
from agent.artifact import serialize
from agent.handoff import Handoff
from agent.loop import run_discovery
from agent.replay import replay
from agent.safety import SafetyPolicy, check_action

TASK = "Search for member 1001, view their detail, then deactivate the account."

# --- 1. allowlist ---
policy = SafetyPolicy(allowed_domains=["localhost"])
assert check_action("click", None, policy) == (True, "")
assert check_action("navigate", "https://evil.com/", policy)[0] is False, "external domain must be blocked"
assert check_action("navigate", "http://localhost:9000/", policy)[0] is True
print("1. allowlist: external domain blocked, localhost allowed")

# --- 2. encryption roundtrip ---
k = crypto.get_key("tenant-a")
assert crypto.decrypt(crypto.encrypt("9999", k), k) == "9999"
print("2. encryption roundtrip OK")

with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)

    page = browser.new_page()
    page.goto("http://localhost:9000/")
    discovery = run_discovery(page, TASK)
    page.close()

    common = cast(Any, dict(
        discovery=discovery,
        name="deactivate_member",
        description="Look up and deactivate a member.",
        inputs=[{"name": "member_id", "type": "str", "required": True}],
        checkpoint_text="SUCCESS",
        business_outcomes=[{"text": "NO SUCH MEMBER", "label": "member not found"}],
        failure_patterns=[{"text": "ACCESS DENIED", "label": "permission denied"}],
    ))

    # --- 3. encryption at rest: the type value must be ciphertext, no plaintext leak ---
    cap_enc = serialize(**common, encrypt_values=True, tenant_id="tenant-a")
    type_step = next(s for s in cap_enc.steps if s.action == "type")
    assert crypto.is_encrypted(type_step.value), "type value must be encrypted at rest"
    assert "1001" not in type_step.value, "plaintext must not leak into the artifact"
    print("3. artifact value encrypted at rest:", type_step.value[:30] + "...")

    # --- 4. replay decrypts only at the moment of use → success ---
    page = browser.new_page()
    page.goto("http://localhost:9000/")
    run = replay(page, cap_enc, inputs={}, tenant_id="tenant-a")
    page.close()
    assert run.result == "success", f"expected success, got {run.result}"
    print("4. replay decrypts at use → success")

    # --- 5. handoff: a hard failure pauses to a human operator ---
    cap_param = serialize(**common, value_params={"1001": "member_id"})
    h = Handoff()
    page = browser.new_page()
    page.goto("http://localhost:9000/")
    run = replay(page, cap_param, inputs={"member_id": "1002"}, handoff=h)
    page.close()
    assert run.result == "failure", f"expected failure, got {run.result}"
    assert h.who() == "PAUSED", f"handoff should pause on failure, got {h.who()}"
    print(f"5. replay hard failure → handoff paused (reason: {h.reason!r})")

    h.cede("operator-1")
    assert h.who() == "HUMAN"
    h.resume()
    assert h.who() == "AUTOMATION"
    print("6. handoff: cede → HUMAN, resume → AUTOMATION")

    browser.close()

print("\nOK — Phase 5: encryption at rest, allowlist, and handoff all verified")
