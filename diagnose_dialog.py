"""
Dialog Interaction Diagnostic Script
Helps identify why the bot can't interact with the Kendo dialog.
"""

from playwright.sync_api import sync_playwright
import os
import json

BYTECURVE_URL = "https://app.bytecurve360.com/portal/core/#/login"
USERNAME = os.getenv("BYTECURVE_USER", "")
PASSWORD = os.getenv("BYTECURVE_PASS", "")

def diagnose_dialog():
    """Run diagnostics on the dialog interaction."""
    
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False, channel="chrome")
        context = browser.new_context()
        page = context.new_page()
        
        print("[INFO] Navigating to ByteCurve...")
        page.goto(BYTECURVE_URL)
        page.wait_for_load_state("networkidle")
        
        # Login
        try:
            cookie_btn = page.locator("a.cc-allow").first
            cookie_btn.click(timeout=5000, force=True)
        except:
            pass
        
        page.get_by_role("textbox", name="USER NAME").fill(USERNAME)
        page.get_by_role("textbox", name="PASSWORD").fill(PASSWORD)
        page.get_by_role("button", name="Sign-In").click()
        page.wait_for_load_state("networkidle")
        
        print("[INFO] Logged in. Navigate to Verify Hours and trigger a dialog...")
        page.pause()  # Manual pause to let you trigger the dialog
        
        # Diagnostics
        print("\n[DIAGNOSTICS] Checking dialog presence...")
        
        # Check 1: Dialog exists
        dialog = page.locator("div[role='dialog'][aria-modal='true']")
        if dialog.count() > 0:
            print("✓ Dialog found with role='dialog'")
        else:
            print("✗ Dialog NOT found with role='dialog'")
        
        # Check 2: OK button exists
        ok_btn = page.locator("button[data-testid='bulk-update-ok-btn']")
        if ok_btn.count() > 0:
            print("✓ OK button found with data-testid selector")
            
            # Check 3: Button visibility
            try:
                ok_btn.wait_for(state="visible", timeout=2000)
                print("✓ OK button is visible")
            except:
                print("✗ OK button NOT visible")
            
            # Check 4: Button enabled
            try:
                is_enabled = ok_btn.is_enabled()
                print(f"  Button enabled: {is_enabled}")
            except Exception as e:
                print(f"✗ Could not check if button enabled: {e}")
            
            # Check 5: Button bounding box
            try:
                bbox = ok_btn.bounding_box()
                print(f"✓ Button bounding box: {bbox}")
                if bbox is None:
                    print("  WARNING: Button has no bounding box (may not be rendered)")
            except Exception as e:
                print(f"✗ Could not get button bounding box: {e}")
            
            # Check 6: Parent dialog visibility
            try:
                parent_dialog = ok_btn.locator("xpath=ancestor::div[@role='dialog']").first
                parent_visible = parent_dialog.is_visible()
                print(f"✓ Parent dialog visible: {parent_visible}")
            except:
                print("✗ Could not check parent dialog visibility")
        else:
            print("✗ OK button NOT found with data-testid selector")
        
        # Check 7: Alternative selectors
        print("\n[ALTERNATIVES] Trying alternative selectors...")
        
        # Try by class
        alt_btn = page.locator("button.k-button-solid-primary")
        print(f"  Buttons with class 'k-button-solid-primary': {alt_btn.count()}")
        
        # Try by text
        text_btn = page.locator("text='Ok'")
        print(f"  Buttons with text 'Ok': {text_btn.count()}")
        
        # Check for kendo-dialog-actions
        actions = page.locator("kendo-dialog-actions")
        print(f"  kendo-dialog-actions elements: {actions.count()}")
        
        # Check 8: Modal backdrop
        backdrop = page.locator("div.k-overlay")
        print(f"\n[MODAL] Modal backdrops found: {backdrop.count()}")
        
        # Check 9: Event listeners (via JavaScript)
        print("\n[EVENTS] Checking button event listeners...")
        try:
            result = page.evaluate("""() => {
                const btn = document.querySelector("button[data-testid='bulk-update-ok-btn']");
                if (!btn) return "Button not found";
                
                const info = {
                    displayed: btn.offsetParent !== null,
                    offsetHeight: btn.offsetHeight,
                    offsetWidth: btn.offsetWidth,
                    computedStyle: {
                        display: window.getComputedStyle(btn).display,
                        visibility: window.getComputedStyle(btn).visibility,
                        pointerEvents: window.getComputedStyle(btn).pointerEvents
                    },
                    hasClickListener: btn.onclick !== null,
                    disabled: btn.disabled,
                    ariaHidden: btn.getAttribute('aria-hidden')
                };
                return info;
            }""")
            print(json.dumps(result, indent=2))
        except Exception as e:
            print(f"✗ Could not evaluate button state: {e}")
        
        # Check 10: Try clicking with different methods
        print("\n[CLICK TESTS] Attempting different click methods...")
        
        try:
            ok_btn = page.locator("button[data-testid='bulk-update-ok-btn']")
            if ok_btn.count() > 0:
                print("  Testing click(force=True)...")
                ok_btn.click(force=True, timeout=2000, delay=100)
                print("  ✓ Click executed successfully")
                page.wait_for_timeout(2000)
            else:
                print("  ✗ Button not found for click test")
        except Exception as e:
            print(f"  ✗ Click failed: {e}")
        
        print("\n[INFO] Diagnostics complete. Check output above.")
        page.pause()
        browser.close()

if __name__ == "__main__":
    diagnose_dialog()
