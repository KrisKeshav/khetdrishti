"""Browser check of the running app with your installed Chrome (no browser download).
Usage: start the server, then  python scripts/ui_check.py [base_url]
Saves screenshots to ./ui_check_out/ and fails on any JavaScript error."""
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8000"
OUT = Path("ui_check_out")
OUT.mkdir(exist_ok=True)

with sync_playwright() as p:
    browser = p.chromium.launch(channel="chrome", headless=True)
    page = browser.new_page(viewport={"width": 1440, "height": 900})
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.on("console", lambda m: m.type == "error" and errors.append(m.text))
    page.goto(f"{BASE}/?lat=20.065&lon=74.13&crop=onion&sow=2026-09-15")
    page.wait_for_selector("#report:not([hidden])", timeout=90_000)
    page.wait_for_timeout(2000)
    page.screenshot(path=OUT / "report.png", full_page=True)
    print("risk cards:", page.locator(".risk").count())
    print("status bar:", page.inner_text("#status").replace("\n", " "))
    page.click("text=Ask (voice)")
    page.screenshot(path=OUT / "ask.png")
    browser.close()

print("JS errors:", errors or "none")
sys.exit(1 if errors else 0)
