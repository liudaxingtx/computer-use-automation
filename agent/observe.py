"""Observation — accessibility tree (primary) + numbered interactive-element menu.

The accessibility tree gives the decision LLM *structure and context*; the
numbered menu gives it *precise handles* to act through. Each menu entry keeps
its Playwright locator so `act` can turn an index straight into a click/type.

When the surface is non-semantic (canvas / image-only UI), the menu comes back
empty and the loop falls back to a vision model via `screenshot_b64`.
"""
import base64
from typing import Any, cast

from playwright.sync_api import Page

# Roles we consider actionable. Order matters: buttons/links first (the common
# case), then inputs, then selection controls.
INTERACTIVE_ROLES = [
    "button", "link", "textbox", "combobox", "checkbox", "radio", "tab",
    "menuitem", "option", "switch",
]


def _element_name(el) -> str:
    """Best-effort accessible name for a control, mirroring what a human reads."""
    for attr in ("aria-label", "placeholder", "title", "value"):
        try:
            v = el.get_attribute(attr)
            if v and v.strip():
                return v.strip()
        except Exception:
            pass
    try:
        t = el.inner_text().strip()
        if t:
            return t
    except Exception:
        pass
    return ""


def _field_name(el) -> str:
    """The HTML `name` attribute, when present — used to name the replay input
    parameter (e.g. username / password / email on a form)."""
    try:
        return (el.get_attribute("name") or "").strip()
    except Exception:
        return ""


def collect_interactive(page: Page) -> list[dict]:
    """Enumerate every visible interactive element with a stable role+name locator."""
    items = []
    name_seen: dict[tuple[str, str], int] = {}
    for role in INTERACTIVE_ROLES:
        loc = page.get_by_role(cast(Any, role))
        n = loc.count()
        for i in range(n):
            el = loc.nth(i)
            try:
                if not el.is_visible():
                    continue
            except Exception:
                continue
            # `ordinal` = this element's 1-based position *within its role*, so
            # replay can fall back to `get_by_role(role).nth(ordinal-1)` when the
            # accessible name is empty (see DESIGN decision log — empty-name fallback).
            # `name_ordinal` = 1-based position *within role+name* — disambiguates
            # repeated same-name controls (e.g. six "Add to cart" buttons on a
            # real storefront), which `ordinal` cannot do.
            name = _element_name(el)
            key = (role, name)
            name_seen[key] = name_seen.get(key, 0) + 1
            items.append({
                "role": role,
                "name": name,
                "field": _field_name(el),
                "ordinal": i + 1,
                "name_ordinal": name_seen[key],
                "locator": el,
            })
    for idx, it in enumerate(items, start=1):
        it["index"] = idx
    return items


def menu_text(items: list[dict]) -> str:
    lines = []
    for it in items:
        label = f'"{it["name"]}"' if it["name"] else "(no name)"
        lines.append(f'[{it["index"]}] {it["role"]} {label}')
    return "\n".join(lines) if lines else "(no interactive elements)"


def observe(page: Page) -> dict:
    """Gather everything the decision LLM needs for one step."""
    aria = page.locator("body").aria_snapshot()
    items = collect_interactive(page)
    return {
        "title": page.title(),
        "url": page.url,
        "aria_tree": aria,
        "menu": menu_text(items),
        "elements": items,
    }


def screenshot_b64(page: Page) -> str:
    """PNG screenshot of the current page, base64-encoded for a vision model."""
    return base64.b64encode(page.screenshot()).decode()
