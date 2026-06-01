from playwright.sync_api import sync_playwright
import os

# Configuration (Matching your existing setup)
BYTECURVE_URL = "https://app.bytecurve360.com/portal/core/#/login"
USERNAME = os.getenv("BYTECURVE_USER", "")
PASSWORD = os.getenv("BYTECURVE_PASS", "")

def run_inspector():
    """
    Automates the login to ByteCurve and opens the Playwright Inspector.
    Use this to identify selectors for 'Extra Work', 'SST', and 'Home to School' rows.
    """
    print("[INFO] Starting Playwright Inspector...")
    
    # We set PWDEBUG=1 to ensure the inspector window opens immediately
    os.environ["PWDEBUG"] = "1"

    with sync_playwright() as p:
        # Headless must be False to interact with the UI
        browser = p.chromium.launch(headless=False, channel="chrome", args=["--start-maximized"])
        context = browser.new_context(no_viewport=True)
        page = context.new_page()


        print(f"[INFO] Navigating to {BYTECURVE_URL}...")
        page.goto(BYTECURVE_URL)
        page.wait_for_load_state("networkidle")

        # Handle cookie banner that may block login fields
        try:
            # Use force=True to interact with the banner even if it's off-screen or hidden
            cookie_btn = page.locator("a.cc-allow").first
            cookie_btn.scroll_into_view_if_needed()
            cookie_btn.click(timeout=5000, force=True)
            print("[INFO] Cookie banner accepted.")
        except Exception:
            pass

        print("[INFO] Pausing for manual interaction/recording...")
        print("[HELP] You can now perform the login manually or click 'Resume' to let the script fill credentials.")
        # This opens the Inspector/Debugger GUI before the login process
        page.pause()

        # Automate the login boilerplate
        print("[INFO] Filling credentials...")
        page.wait_for_selector("#username")
        page.fill("#username", USERNAME)
        page.fill("#password", PASSWORD)
        page.click("button[type='submit']")

        browser.close()

if __name__ == "__main__":
    run_inspector()