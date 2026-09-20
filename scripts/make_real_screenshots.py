"""Generate per-page screenshots for the *real-site* tasks (SauceDemo, The Internet).

Unlike the mock app, these pages are stateful (login + cart + checkout), so we
drive the browser through each flow and capture the page at each meaningful
stage — login, products, cart, form, overview, confirmation — plus the planted
error states. Run from the repo root (needs network to the public sites):

    .venv/bin/python scripts/make_real_screenshots.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from playwright.sync_api import sync_playwright

OUT = Path("dashboard/screenshots")
OUT.mkdir(parents=True, exist_ok=True)


def shot(page, pid):
    page.screenshot(path=OUT / f"{pid}.png")
    print(f"{pid}.png")


def wait(page, ms=400):
    page.wait_for_timeout(ms)


def main() -> int:
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)

        # ---- SauceDemo: login task ----
        pg = browser.new_page(viewport={"width": 860, "height": 520})
        pg.goto("https://www.saucedemo.com/", wait_until="domcontentloaded")
        shot(pg, "sauce_login_form")

        # invalid credentials (fresh page, wrong password)
        pg2 = browser.new_page(viewport={"width": 860, "height": 520})
        pg2.goto("https://www.saucedemo.com/", wait_until="domcontentloaded")
        pg2.get_by_role("textbox", name="Username").fill("standard_user")
        pg2.get_by_role("textbox", name="Password").fill("wrong_password")
        pg2.get_by_role("button", name="Login").click()
        wait(pg2)
        shot(pg2, "sauce_login_invalid")

        # valid login -> products
        pg.get_by_role("textbox", name="Username").fill("standard_user")
        pg.get_by_role("textbox", name="Password").fill("secret_sauce")
        pg.get_by_role("button", name="Login").click()
        wait(pg, 600)
        shot(pg, "sauce_login_success")

        # ---- SauceDemo: checkout task (continues from logged-in state) ----
        shot(pg, "sauce_checkout_products")
        pg.get_by_role("button", name="Add to cart").nth(0).click()
        wait(pg)
        pg.locator("a.shopping_cart_link").click()
        wait(pg, 600)
        shot(pg, "sauce_checkout_cart")
        pg.get_by_role("button", name="Checkout").click()
        wait(pg, 600)
        pg.get_by_role("textbox", name="First Name").fill("John")
        pg.get_by_role("textbox", name="Last Name").fill("Doe")
        pg.get_by_role("textbox", name="Zip/Postal Code").fill("73112")
        wait(pg)
        shot(pg, "sauce_checkout_form")
        pg.get_by_role("button", name="Continue").click()
        wait(pg, 600)
        shot(pg, "sauce_checkout_overview")
        pg.get_by_role("button", name="Finish").click()
        wait(pg, 800)
        shot(pg, "sauce_checkout_complete")
        pg.close(); pg2.close()

        # ---- The Internet: login task ----
        pg = browser.new_page(viewport={"width": 860, "height": 520})
        pg.goto("https://the-internet.herokuapp.com/login", wait_until="domcontentloaded")
        shot(pg, "inet_login_form")

        pg3 = browser.new_page(viewport={"width": 860, "height": 520})
        pg3.goto("https://the-internet.herokuapp.com/login", wait_until="domcontentloaded")
        pg3.get_by_role("textbox").nth(0).fill("tomsmith")
        pg3.get_by_role("textbox").nth(1).fill("bad_password")
        pg3.get_by_role("button", name="Login").click()
        wait(pg3)
        shot(pg3, "inet_login_invalid")

        pg.get_by_role("textbox").nth(0).fill("tomsmith")
        pg.get_by_role("textbox").nth(1).fill("SuperSecretPassword!")
        pg.get_by_role("button", name="Login").click()
        wait(pg, 600)
        shot(pg, "inet_login_success")
        pg.close(); pg3.close()

        browser.close()
    print("\nDone ->", OUT)
    return 0


if __name__ == "__main__":
    sys.exit(main())
