#ByteCurve Payroll Adjustment Automation App

import datetime
import os
from playwright.sync_api import sync_playwright, Page

# --- CONFIGURATION / PLACEHOLDERS ---
BYTECURVE_URL = "https://app.bytecurve360.com/portal/core/#/login"
USERNAME = os.getenv("BYTECURVE_USER", "")
PASSWORD = os.getenv("BYTECURVE_PASS", "")

# Task-specific policies for adjustments
TASK_POLICIES = {
    "Extra Work": {"max_allowed": 2.0, "require_schedule_match": True},
    "SST": {"max_allowed": 4.0, "require_schedule_match": True},
    "Home to School": {"max_allowed": 6.0, "require_schedule_match": False},
    "DEFAULT": {"max_allowed": 8.0, "require_schedule_match": True}
}

SELECTORS = {
    "login_username": "USER NAME",
    "login_password": "PASSWORD",
    "login_submit": "Sign-In",
    "cookie_accept": "a.cc-allow",
    "nav_payroll_section": "PAYROLL",
    "nav_timesheets": "Verify Hours",
    "date_filter_btn": "Toggle calendar",
    "timesheet_rows": "tr[role='row']", 
    "row_category": "td:nth-child(2)", # Adjust index based on Inspector find
    "row_scheduled_hours": "td:nth-child(4)", # New: Scheduled column
    "row_actual_hours": "td:nth-child(5)",    # Existing: Actual column
    "time_cell_clickable": "a.k-link",        # Selector for the clickable time link
    "time_picker_input": "#timepicker-3",     # From recording
    "update_btn": "Update",                   # From recording
    "checkbox_verify_all": "verify-all",
    "btn_verify": "Verify",
    "btn_bulk_ok": "bulk-update-ok-btn"
}

def get_previous_business_day() -> str:
    """Calculates the date for the previous business day."""
    today = datetime.date.today()
    if today.weekday() == 0: # Monday
        delta = 3
    elif today.weekday() == 6: # Sunday
        delta = 2
    else:
        delta = 1
    return (today - datetime.timedelta(days=delta)).strftime("%Y-%m-%d")

def login(page: Page):
    """Handles login authentication."""
    print(f"[INFO] Navigating to {BYTECURVE_URL}...")
    page.goto(BYTECURVE_URL)
    page.wait_for_load_state("networkidle")

    try:
        cookie_btn = page.locator(SELECTORS["cookie_accept"]).first
        cookie_btn.scroll_into_view_if_needed()
        cookie_btn.click(timeout=5000, force=True)
        print("[INFO] Cookie consent accepted.")
    except Exception:
        pass

    page.get_by_role("textbox", name=SELECTORS["login_username"]).fill(USERNAME)
    page.get_by_role("textbox", name=SELECTORS["login_password"]).fill(PASSWORD)
    page.get_by_role("button", name=SELECTORS["login_submit"]).click()
    page.wait_for_load_state("networkidle")
    print("[INFO] Logged in successfully.")

def navigate_to_payroll(page: Page):
    """Navigates to the timesheet section."""
    print("[INFO] Navigating to Timesheets...")
    page.get_by_role("link", name=SELECTORS["nav_payroll_section"]).click()
    page.get_by_role("link", name=SELECTORS["nav_timesheets"]).click()

def adjust_time_entry(page: Page, row, new_time_str: str):
    """Performs the UI steps to adjust a time entry in the grid."""
    try:
        # Click the specific time cell to open the editor
        time_link = row.locator(SELECTORS["time_cell_clickable"]).first
        time_link.click()
        
        # Interact with the time picker as per recording
        page.locator(SELECTORS["time_picker_input"]).fill(new_time_str)
        page.get_by_role("button", name=SELECTORS["update_btn"]).click()
        
        # Wait for the grid to refresh after update
        page.wait_for_load_state("networkidle")
        return True
    except Exception as e:
        print(f"[ERROR] Failed to adjust time: {e}")
        return False

def validate_and_process_rows(page: Page, target_date: str):
    """Validates records against baselines and approves or flags them."""
    print(f"[INFO] Filtering for date: {target_date}")
    
    # Using the recording logic for filtering
    page.get_by_role("button", name=SELECTORS["date_filter_btn"]).click()
    # Note: Logic to select the specific date from the calendar grid may 
    # vary depending on the month. For now, we try a text-based match.
    day = target_date.split("-")[-1].lstrip("0")
    page.get_by_role("gridcell", name=day).first.click()
    page.get_by_test_id("filter-submit-btn").click()
    
    page.wait_for_load_state("networkidle")

    rows = page.locator(SELECTORS["timesheet_rows"]).all()
    if not rows:
        print(f"[WARN] No records found.")
        return

    for row in rows:
        try:
            category = row.locator(SELECTORS["row_category"]).inner_text().strip()
            
            actual_text = row.locator(SELECTORS["row_actual_hours"]).inner_text().strip()
            actual_hours = float(actual_text) if actual_text and actual_text != "-" else 0.0
            
            scheduled_text = row.locator(SELECTORS["row_scheduled_hours"]).inner_text().strip()
            scheduled_hours = float(scheduled_text) if scheduled_text and scheduled_text != "-" else 0.0

            # Get policy for this task
            policy = TASK_POLICIES.get(category, TASK_POLICIES["DEFAULT"])
            is_valid = True
            
            # Logic: If actual exceeds scheduled or the policy maximum
            if actual_hours > policy["max_allowed"]:
                print(f"[FLAG] {category} exceeds policy limit ({actual_hours} > {policy['max_allowed']})")
                is_valid = False
            elif policy["require_schedule_match"] and actual_hours > scheduled_hours:
                print(f"[FLAG] {category} exceeds schedule ({actual_hours} > {scheduled_hours})")
                is_valid = False

            if is_valid:
                print(f"[OK] {category} ({actual_hours} hrs) is within limits.")
            else:
                print(f"[ACTION] Adjusting {category}...")
                # Example adjustment: set actual to match scheduled
                # Note: In a real scenario, you'd need the specific Clock-In/Out string
                # For now, we flag it for review to avoid incorrect automated writes
                print(f"[LOG] Manual adjustment needed for {category}. Skipping bulk verify.")
                return 

        except Exception as e:
            print(f"[ERROR] Row processing error: {e}")

    # If all rows validated, perform the recorded bulk verification
    print("[ACTION] All rows valid. Performing bulk verification...")
    page.get_by_role("checkbox", name=SELECTORS["checkbox_verify_all"]).check()
    page.get_by_role("button", name=SELECTORS["btn_verify"]).click()
    page.get_by_test_id(SELECTORS["btn_bulk_ok"]).click()

def run_automation():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, channel="chrome", args=["--start-maximized"])
        context = browser.new_context(viewport={"width": 1920, "height": 1080})
        page = context.new_page()

        try:
            target_date = get_previous_business_day()
            login(page)
            navigate_to_payroll(page)
            validate_and_process_rows(page, target_date)
            print("[INFO] Done.")
        except Exception as e:
            print(f"[CRITICAL] {e}")
        finally:
            browser.close()

if __name__ == "__main__":
    run_automation()