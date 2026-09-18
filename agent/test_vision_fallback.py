"""Verify the K3 vision fallback: on a non-semantic surface (image-only button),
the interactive-element menu is empty, and K3 must understand the page visually."""
from playwright.sync_api import sync_playwright

from agent import llm
from agent.loop import _vision_prompt
from agent.observe import observe, screenshot_b64

TASK = "Click the button to proceed."

with sync_playwright() as p:
    b = p.chromium.launch(headless=True)
    page = b.new_page()
    page.goto("http://localhost:9000/imgbutton")

    obs = observe(page)
    print("=== accessibility tree (should have no button/link) ===")
    print(obs["aria_tree"])
    print("=== interactive menu ===")
    print(obs["menu"])

    n = len(obs["elements"])
    print(f"=== interactive element count: {n} ===")
    assert n == 0, "expected zero interactive elements on the image-only surface"

    print("\n=== triggering K3 vision fallback ===")
    img = screenshot_b64(page)
    note = llm.kimi_vision(img, _vision_prompt(TASK))
    print(note)

    low = note.lower()
    assert "continue" in low, "K3 should have read the CONTINUE label"
    print("\nOK — K3 vision fallback correctly understood a non-semantic surface")
    b.close()
