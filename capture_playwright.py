from playwright.sync_api import sync_playwright
import os

os.makedirs("reference", exist_ok=True)
os.makedirs("live", exist_ok=True)

REFERENCE_URL = "https://neouat.axismaxlife.com/investment-plans/rd-calculator"
LIVE_URL = "https://www.axismaxlife.com/investment-plans/rd-calculator"

with sync_playwright() as p:
    browser = p.chromium.launch()
    page = browser.new_page(viewport={"width": 1280, "height": 800})

    page.goto(REFERENCE_URL, wait_until="load")
    page.wait_for_load_state("networkidle")
    page.screenshot(path="reference/screen.png", full_page=False)
    print("Reference screenshot saved.")

    page.goto(LIVE_URL, wait_until="load")
    page.wait_for_load_state("networkidle")
    page.screenshot(path="live/screen.png", full_page=False)
    print("Live screenshot saved.")

    browser.close()