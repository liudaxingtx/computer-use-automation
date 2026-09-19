"""Screenshot the mock app's key pages for visual review.

Run: .venv/bin/python scripts/shot_mock.py
"""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from playwright.sync_api import sync_playwright

OUT = Path("/tmp/mock_shots")
OUT.mkdir(exist_ok=True)

pages = [
    ("/", "1_home_search"),
    ("/search?member_id=1001", "2_member_detail"),
    ("/deactivate?member_id=1001", "3_confirm"),
    ("/do_deactivate?member_id=1001", "4_success"),
    ("/search?member_id=9999", "5_no_such_member"),
    ("/imgbutton", "6_image_gate"),
]

with sync_playwright() as p:
    browser = p.chromium.launch()
    page = browser.new_page(viewport={"width": 900, "height": 640})
    for path, name in pages:
        page.goto("http://localhost:9000" + path)
        page.wait_for_timeout(200)
        page.screenshot(path=str(OUT / f"{name}.png"))
        print(f"saved {OUT / (name + '.png')}")
    browser.close()

print(f"\n共 {len(pages)} 张截图，目录: {OUT}")
