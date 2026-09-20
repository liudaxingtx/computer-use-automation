"""One-off: discover + auto-record + verify the SauceDemo checkout flow."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from playwright.sync_api import sync_playwright  # noqa: E402
from agent.loop import run_discovery  # noqa: E402
from agent.artifact import auto_serialize, infer_params  # noqa: E402
from agent.replay import replay  # noqa: E402

URL = "https://www.saucedemo.com/"
TASK = ("Log in as standard_user with password secret_sauce, then add the first "
        "product (Sauce Labs Backpack) to the cart, open the shopping cart, click "
        "Checkout, fill the shipping form (First Name 'John', Last Name 'Doe', "
        "Zip/Postal Code '73112'), click Continue, then Finish to reach the "
        "order-complete confirmation page.")
NAME = "saucedemo_checkout"

with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    page = browser.new_page()
    page.goto(URL)
    discovery = run_discovery(page, TASK, max_steps=25)
    browser.close()

print("=== STATUS:", discovery.get("status"))
print("=== CHECKPOINT:", repr(discovery.get("checkpoint_text")))
print("=== OUTPUTS:", discovery.get("outputs"))
print("=== REASON:", discovery.get("reason"))
print("=== N_STEPS:", len(discovery.get("steps", [])))

Path("evidence").mkdir(exist_ok=True)
Path(f"evidence/discovery_{NAME}.json").write_text(json.dumps(discovery, indent=2, default=str))

if discovery.get("status") != "success" or not discovery.get("checkpoint_text"):
    print("!! discovery did not succeed — aborting before recording")
    sys.exit(1)

cap = auto_serialize(discovery, name=NAME, description=TASK, url=URL)
Path(f"artifacts/{NAME}.json").write_text(cap.model_dump_json(indent=2))
print("=== INPUTS:", json.dumps([i.model_dump() for i in cap.inputs], indent=2))
print("=== OUTPUT SPECS:", json.dumps([o.model_dump() for o in cap.outputs], indent=2))
print("=== CHECKPOINT_TEXT:", repr(cap.checkpoint_text))

_, value_params = infer_params(discovery)
verify_inputs = {pname: val for val, pname in value_params.items()}
print("=== VERIFY INPUTS:", verify_inputs)

with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    page = browser.new_page()
    page.goto(cap.start_url or URL)
    run = replay(page, cap, inputs=verify_inputs)
    print("=== VERIFY RESULT:", run.result)
    print("=== VERIFY DIAG:", run.diagnostic)
    print("=== VERIFY OUTPUTS:", run.outputs)
    print("=== VERIFY DURATION:", run.duration_ms, "ms")
    browser.close()
