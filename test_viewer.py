"""Headless test of our viewer/index.html — confirms web-ifc loads the v2 IFC."""

from __future__ import annotations

import os
import time

from playwright.sync_api import sync_playwright

URL = os.environ.get(
    "VIEWER_URL", "https://yukihamada.github.io/farnsworth-ifc/viewer/"
)
OUT = os.path.join(os.path.dirname(__file__), "viewer", "preview.png")


def main():
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        ctx = browser.new_context(
            viewport={"width": 1600, "height": 1000},
            device_scale_factor=2,
        )
        page = ctx.new_page()

        msgs = []
        page.on("console", lambda m: msgs.append(f"[{m.type}] {m.text}"))
        page.on("pageerror", lambda e: msgs.append(f"[error] {e}"))

        print(f"Navigating to {URL}")
        page.goto(URL, wait_until="domcontentloaded", timeout=60000)

        # Wait for status to update to "Farnsworth House"
        deadline = time.time() + 90
        ready = False
        while time.time() < deadline:
            time.sleep(1)
            text = page.locator("#status .nm").text_content() or ""
            if "Farnsworth House" in text:
                ready = True
                break

        time.sleep(3)
        page.screenshot(path=OUT)
        print(f"Screenshot: {OUT}")

        info = page.evaluate("""() => {
            const out = {};
            for (const c of (document.querySelector('#viewer canvas') ? [1] : [])) {
                out.has_canvas = true;
            }
            const status = document.querySelector('#status .nm')?.textContent;
            const progress = document.querySelector('#status .progress')?.innerHTML;
            return { status, progress };
        }""")
        print(f"Status panel: {info}")
        print()
        print("--- console messages (last 30) ---")
        for m in msgs[-30:]:
            print(" ", m[:240])

        if not ready:
            print()
            print("⚠ viewer didn't reach 'Farnsworth House' state in 90s")

        browser.close()


if __name__ == "__main__":
    main()
