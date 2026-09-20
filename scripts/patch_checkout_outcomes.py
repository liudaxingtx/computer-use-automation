"""Add the invalid-credentials business outcome to saucedemo_checkout (it starts
with a login step, so a wrong password is a legitimate "invalid credentials"
answer — matching saucedemo_login / theinternet_login), and record one such run
so the checkout task demonstrates the three-state contract too.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from playwright.sync_api import sync_playwright  # noqa: E402
from agent.artifact import Capability, OutcomePattern  # noqa: E402
from agent.replay import replay  # noqa: E402
from agent.observability import ReplayStore  # noqa: E402

NAME = "saucedemo_checkout"
ART = ROOT / "artifacts" / f"{NAME}.json"
EVID = ROOT / "evidence" / f"artifact_{NAME}.json"

cap = Capability.model_validate_json(ART.read_text())
if not any(p.text == "Epic sadface" for p in cap.business_outcomes):
    cap.business_outcomes.append(OutcomePattern(text="Epic sadface", label="invalid credentials"))
    ART.write_text(cap.model_dump_json(indent=2))
    EVID.write_text(cap.model_dump_json(indent=2))

# wrong password -> business_outcome at the login step
bad = {"user-name": "standard_user", "password": "wrong_password",
       "firstName": "John", "lastName": "Doe", "postalCode": "73112"}
with sync_playwright() as p:
    b = p.chromium.launch(headless=True)
    pg = b.new_page()
    pg.goto(cap.start_url)
    run = replay(pg, cap, inputs=bad)
    b.close()

ReplayStore().record(run)
print("=== result:", run.result)
print("=== diagnostic:", run.diagnostic)
print("=== duration:", run.duration_ms, "ms")
