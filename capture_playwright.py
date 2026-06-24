from playwright.sync_api import sync_playwright
import os

os.makedirs("reference", exist_ok=True)
os.makedirs("live", exist_ok=True)

REFERENCE_URL = "https://neouat.axismaxlife.com/investment-plans/rd-calculator"   # replace when ready
LIVE_URL = "https://www.axismaxlife.com/investment-plans/rd-calculator"        # replace when ready

with sync_playwright() as p:
    browser = p.chromium.launch()
    page = browser.new_page(viewport={"width": 1280, "height": 800})

    page.goto(REFERENCE_URL)
    page.wait_for_load_state("networkidle")
    page.screenshot(path="reference/screen.png", full_page=True)
    print("Reference screenshot saved.")

    page.goto(LIVE_URL)
    page.wait_for_load_state("networkidle")
    page.screenshot(path="live/screen.png", full_page=True)
    print("Live screenshot saved.")

    browser.close()