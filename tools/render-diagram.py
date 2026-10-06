#!/usr/bin/env python3
"""Renders tools/architecture.html (hand-drawn lab diagram) to the part-1 image.

    .venv/bin/python tools/render-diagram.py

Fetches rough.js (MIT) and the Kalam font (OFL) into tools/vendor/ on first
run; renders with the installed Google Chrome through Playwright.
"""
import os
import urllib.request

from playwright.sync_api import sync_playwright

HERE = os.path.dirname(os.path.abspath(__file__))
VENDOR = os.path.join(HERE, "vendor")
OUT = os.path.join(HERE, "..", "content", "ai-lab", "01-intro", "architecture.png")
DEPS = {
    "rough.js": "https://unpkg.com/roughjs@4.6.6/bundled/rough.js",
    "Kalam-Regular.ttf": "https://github.com/google/fonts/raw/main/ofl/kalam/Kalam-Regular.ttf",
    "Kalam-Bold.ttf": "https://github.com/google/fonts/raw/main/ofl/kalam/Kalam-Bold.ttf",
}


def main():
    os.makedirs(VENDOR, exist_ok=True)
    for name, url in DEPS.items():
        path = os.path.join(VENDOR, name)
        if not os.path.exists(path):
            urllib.request.urlretrieve(url, path)
    with sync_playwright() as p:
        browser = p.chromium.launch(channel="chrome", headless=True)
        page = browser.new_page(device_scale_factor=2)
        page.goto("file://" + os.path.join(HERE, "architecture.html"))
        page.wait_for_function("document.fonts.ready.then(() => true)")
        page.wait_for_timeout(300)
        page.locator("#d").screenshot(path=os.path.normpath(OUT))
        browser.close()
    print(os.path.normpath(OUT))


if __name__ == "__main__":
    main()
