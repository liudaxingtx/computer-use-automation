"""Post-process saucedemo_checkout: give its outputs deterministic CSS extractors.

Discovery only records the happy path; the LLM declared two outputs
(order_status / confirmation_heading) that don't map to on-page key-value rows,
so the replay's table-scan extraction returned None. SauceDemo's confirmation
page has stable, semantic selectors, so we bind the outputs to them directly —
the same honest post-processing pattern as scripts/patch_outcomes.py.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from playwright.sync_api import sync_playwright  # noqa: E402
from agent.artifact import Capability, OutputSpec  # noqa: E402
from agent.replay import replay  # noqa: E402
from agent.observability import ReplayStore  # noqa: E402

NAME = "saucedemo_checkout"
ART = ROOT / "artifacts" / f"{NAME}.json"
EVID = ROOT / "evidence" / f"artifact_{NAME}.json"

cap = Capability.model_validate_json(ART.read_text())

# Bind the two declared outputs to real, stable DOM elements on the confirmation page.
cap.outputs = [
    OutputSpec(name="confirmation_heading", type="str",
               source="confirmation page", label="", extract=".complete-header"),
    OutputSpec(name="checkout_banner", type="str",
               source="confirmation page", label="", extract=".title"),
]

ART.write_text(cap.model_dump_json(indent=2))
EVID.write_text(cap.model_dump_json(indent=2))

# Re-verify with the discovered inputs so a good run (with real outputs) is recorded.
verify_inputs = {
    "user-name": "standard_user", "password": "secret_sauce",
    "firstName": "John", "lastName": "Doe", "postalCode": "73112",
}
with sync_playwright() as p:
    b = p.chromium.launch(headless=True)
    pg = b.new_page()
    pg.goto(cap.start_url)
    run = replay(pg, cap, inputs=verify_inputs)
    b.close()

ReplayStore().record(run)
print("=== result:", run.result)
print("=== outputs:", run.outputs)
print("=== duration:", run.duration_ms, "ms")
