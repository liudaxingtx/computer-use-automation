"""Generate page screenshots for the dashboard's task detail view.

Each capability's replay path touches several mock pages; these screenshots let
the dashboard show *what each step's page looks like* (plus the planted error
states). Run from the repo root (mock must be running on localhost:9000):

    .venv/bin/python scripts/make_screenshots.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from playwright.sync_api import sync_playwright

OUT = Path("dashboard/screenshots")
OUT.mkdir(parents=True, exist_ok=True)

# (id, url, label) — the pages a deactivate_member replay can touch.
PAGES = [
    ("search", "http://localhost:9000/", "Search page"),
    ("detail", "http://localhost:9000/search?member_id=1001", "Member detail"),
    ("confirm", "http://localhost:9000/deactivate?member_id=1001", "Deactivation confirm"),
    ("success", "http://localhost:9000/do_deactivate?member_id=1001", "Result — success"),
    ("no_such_member", "http://localhost:9000/search?member_id=9999", "Result — no such member"),
    ("access_denied", "http://localhost:9000/do_deactivate?member_id=1002", "Result — access denied"),
]


def main() -> int:
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 860, "height": 480})
        for pid, url, label in PAGES:
            page.goto(url, wait_until="domcontentloaded")
            page.wait_for_timeout(200)
            path = OUT / f"{pid}.png"
            page.screenshot(path=path)
            print(f"{pid}.png  <-  {label}")
        browser.close()
    print(f"\n{len(PAGES)} screenshots -> {OUT}/")
    return 0


if __name__ == "__main__":
    sys.exit(main())
