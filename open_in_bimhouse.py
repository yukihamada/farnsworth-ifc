"""Drive bim.house's client-side IFC uploader with our v2 IFC.

bim.house's cloud save only stores its simplified element format, so
IfcMember/IfcCurtainWall/IfcMaterialLayerSet etc cannot be persisted there.
But the in-browser upload uses web-ifc 0.0.66 which DOES read full IFC4
(IfcMember, IfcCurtainWall, IfcSlab, IfcDoor, IfcStair, IfcGrid, ...).

This script automates that upload and screenshots the result so we can
verify bim.house renders the complete BIM model.
"""

from __future__ import annotations

import os
import time

from playwright.sync_api import sync_playwright

ROOT = os.path.dirname(__file__)
IFC = os.path.join(ROOT, "farnsworth_house_v2.ifc")
OUT = os.path.join(ROOT, "bimhouse_v2_screenshot.png")
URL = "https://bim.house/"


def main():
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        ctx = browser.new_context(
            viewport={"width": 1600, "height": 1000},
            device_scale_factor=2,
        )
        page = ctx.new_page()

        console_msgs = []
        dialogs = []
        page.on("console", lambda msg: console_msgs.append(f"[{msg.type}] {msg.text}"))
        page.on("pageerror", lambda err: console_msgs.append(f"[error] {err}"))

        def on_dialog(d):
            dialogs.append(d.message)
            console_msgs.append(f"[dialog] {d.message}")
            d.accept()
        page.on("dialog", on_dialog)

        print(f"Navigating to {URL} …")
        page.goto(URL, wait_until="domcontentloaded", timeout=60000)
        page.wait_for_load_state("networkidle", timeout=30000)
        time.sleep(2)

        print(f"Uploading {IFC} via #bim-upload-input …")
        page.locator("#bim-upload-input").set_input_files(IFC)

        # web-ifc has to download wasm + parse — give it time (up to 3 min)
        deadline = time.time() + 180
        while time.time() < deadline:
            time.sleep(2)
            if dialogs:
                break
        # settle
        time.sleep(5)

        print("Recent console messages:")
        for m in console_msgs[-20:]:
            print("  ", m[:200])

        # Set the view to iso for the screenshot
        try:
            page.locator('button[data-v="iso"]').click()
            time.sleep(1)
        except Exception:
            pass

        page.screenshot(path=OUT, full_page=False)
        print(f"\nScreenshot: {OUT}")

        # Quick stats from the page
        info = page.evaluate("""() => {
            function countMeshes(o, acc) {
                if (!o) return acc;
                if (o.isMesh) acc.push(o.type || 'Mesh');
                (o.children || []).forEach(c => countMeshes(c, acc));
                return acc;
            }
            const all = [];
            countMeshes(window.scene, all);
            return {
                total_meshes_in_scene: all.length,
                wall_group_descendants: countMeshes(window.wallsGroup, []).length,
                roof_group_descendants: countMeshes(window.roofGroup, []).length,
                window_group_descendants: countMeshes(window.windowsGroup, []).length,
                scene_children: window.scene?.children?.length ?? null,
            };
        }""")
        print(f"\nScene info: {info}")
        print(f"Dialogs: {dialogs}")

        browser.close()


if __name__ == "__main__":
    main()
