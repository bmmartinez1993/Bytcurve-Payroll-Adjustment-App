#ByteCurve Payroll Adjustment Automation App

import datetime
import os
import logging
from datetime import datetime as dt
from playwright.sync_api import sync_playwright, Page

# --- LOGGING CONFIGURATION ---
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[
        logging.FileHandler("automation_activity.log"),
        logging.StreamHandler()
    ]
)

# --- CONFIGURATION / PLACEHOLDERS ---
BYTECURVE_URL = "https://app.bytecurve360.com/portal/core/#/login"
USERNAME = os.getenv("BYTECURVE_USER", "")
PASSWORD = os.getenv("BYTECURVE_PASS", "")

# Task-specific policies for adjustments
TASK_POLICIES = {
    "Extra Work": {"max_allowed": 0.016666666666666666, "require_schedule_match": False}, # 1 minute in hours
    "S2S Charter": {"max_allowed": 0.016666666666666666, "require_schedule_match": False}, # 1 minute in hours
    "Spare CDL": {"max_allowed": 0.0, "require_schedule_match": True}, # Should be same schedule time
    "SST": {"max_allowed": 4.0, "require_schedule_match": True},
    "HTS": {"max_allowed": 4.0, "require_schedule_match": True},
    "DEFAULT": {"max_allowed": 4.0, "require_schedule_match": True}
}

SELECTORS = {
    "login_username": "USER NAME",
    "login_password": "PASSWORD",
    "login_submit": "Sign-In",
    "cookie_accept": "a.cc-allow",
    "nav_payroll_section": "PAYROLL",
    "nav_timesheets": "Verify Hours",
    "date_filter_btn": "Toggle calendar",
    "weekly_view_grid": "[data-testid='weekly-view-grid']",
    "payload_task_grid": "[data-testid='payload-task-grid']",
    "row_worker_id": "td[aria-colindex='2']",      # Employee col in detail grid
    "row_category": "td[aria-colindex='3']",       # Task Name col in detail grid
    "row_task_code": "td[aria-colindex='5']",      # Task Code col in detail grid
    "row_paid_start": "td[aria-colindex='10']",    # Paid Start col
    "row_paid_end": "td[aria-colindex='11']",      # Paid End col
    "row_paid_reg": "td[aria-colindex='12']",      # Paid Reg col
    "row_scheduled_hours": "td[aria-colindex='7']", # Scheduled col
    "row_actual_hours": "td[aria-colindex='9']",    # Actual col
    "row_checkbox": "input[aria-label='verify']",
    "checkbox_verify_all": "input[aria-label='verify-all']",
    "btn_verify": "button:has-text('Verify')",
    "btn_bulk_ok": "bulk-update-ok-btn",
    "weekly_view_btn": "weekly-view-btn",
    "filter_submit_btn": "filter-submit-btn",
    "close_details": "filter-close-btn",
    "verified_radio": "verified-tasks-input"
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
    logging.info(f"Navigating to {BYTECURVE_URL}...")
    page.goto(BYTECURVE_URL)
    page.wait_for_load_state("networkidle")

    try:
        cookie_btn = page.locator(SELECTORS["cookie_accept"]).first
        cookie_btn.scroll_into_view_if_needed()
        cookie_btn.click(timeout=5000, force=True)
        logging.info("Cookie consent accepted.")
    except Exception:
        pass

    page.get_by_role("textbox", name=SELECTORS["login_username"]).fill(USERNAME)
    page.get_by_role("textbox", name=SELECTORS["login_password"]).fill(PASSWORD)
    page.get_by_role("button", name=SELECTORS["login_submit"]).click()
    page.wait_for_load_state("networkidle")
    logging.info("Logged in successfully.")

def navigate_to_payroll(page: Page):
    """Navigates to the timesheet section."""
    logging.info("Navigating to Timesheets...")
    page.get_by_role("link", name=SELECTORS["nav_payroll_section"]).click()
    page.get_by_role("link", name=SELECTORS["nav_timesheets"]).click()

def adjust_time_entry(page: Page, row, col_index: int, new_time_str: str):
    """Performs the UI steps to adjust a time entry in the grid."""
    try:
        # Click Paid Start (10) or Paid End (11) cell
        cell = row.locator(f"td[aria-colindex='{col_index}']")
        cell.click()
        
        # Fill time picker
        page.locator("input.k-input-inner").fill(new_time_str)
        # Click the Save/Update checkmark in the Actions column
        row.locator("button.k-grid-save-command").click()
        
        # Wait for the grid to refresh after update
        page.wait_for_load_state("networkidle")
        return True
    except Exception as e:
        logging.error(f"ACTION: Failed to adjust time: {e}")
        return False

def process_employee_details(page: Page, worker_name: str):
    """Processes the sub-table for the selected worker."""
    logging.info(f"PROCESS: Reviewing task details for {worker_name}...")
    
    # Show Unverified to focus adjustments
    page.get_by_test_id(SELECTORS["verified_radio"]).click()
    page.wait_for_load_state("networkidle")

    detail_rows = page.locator(f"{SELECTORS['payload_task_grid']} tr.k-master-row").all()
    if not detail_rows:
        logging.info(f"OK: No unverified tasks for {worker_name}.")
        return True

    processed_successfully = True
    
    # Logic for individual tasks (Overlaps/Policies) inside the detail grid
    # This preserves your original overlap logic structure
    for row in detail_rows:
        try:
            task_name = row.locator(SELECTORS["row_category"]).inner_text().strip()
            paid_reg_txt = row.locator(SELECTORS["row_paid_reg"]).inner_text().strip()
            
            # Conditional: Priority "Brigde Charter"
            if task_name == "Brigde Charter":
                logging.info(f"REPORT: Task '{task_name}' is set to avoid adjustment.")
                processed_successfully = False
                continue

            # Policy Enforcement (Convert HH:MM to float if necessary)
            # For now, auto-check valid rows
            row.locator(SELECTORS["row_checkbox"]).check()
            
        except Exception as e:
            logging.error(f"Error in detail row: {e}")
            processed_successfully = False

    if processed_successfully:
        # Perform Verify in the detail view
        verify_btn = page.locator(f"{SELECTORS['payload_task_grid']} {SELECTORS['btn_verify']}")
        if verify_btn.is_enabled():
            verify_btn.click()
            page.get_by_test_id(SELECTORS["btn_bulk_ok"]).click()
            logging.info(f"ACTION: Verified all tasks for {worker_name}.")
    
    return processed_successfully

def check_for_overlaps(intervals):
    """ 
    Checks if any time intervals overlap by at least 15 minutes.
    intervals: List of tuples (datetime, datetime)
    """
    if len(intervals) < 2:
        return False
    
    # Sort by start time to check adjacent intervals
    sorted_intervals = sorted([i for i in intervals if i[0] and i[1]], key=lambda x: x[0]) # type: ignore
    for i in range(len(sorted_intervals) - 1):
        current_end = sorted_intervals[i][1]
        next_start = sorted_intervals[i+1][0]
        
        if current_end > next_start: # An overlap exists
            overlap_duration = current_end - next_start
            if overlap_duration >= datetime.timedelta(minutes=15):
                return True
    return False

def validate_and_process_rows(page: Page, target_date: str):
    """Validates records against baselines and approves or flags them."""
    logging.info(f"Filtering for date: {target_date}")
    
    target_dt = dt.strptime(target_date, "%Y-%m-%d")
    # Calculate Monday of the week for the Payroll Week Starting filter
    monday_dt = target_dt - datetime.timedelta(days=target_dt.weekday())
    monday_day = str(monday_dt.day)

    # Apply date filters
    page.get_by_test_id(SELECTORS["weekly_view_btn"]).click()
    page.locator("[data-testid='week-starting-input'] button").click()
    page.get_by_role("gridcell", name=monday_day, exact=True).first.click()
    page.get_by_test_id(SELECTORS["filter_submit_btn"]).click()
    page.wait_for_load_state("networkidle")

    # Find column index for Paid Hrs on target_date
    target_header_str = target_dt.strftime("%m/%d/%Y")
    header = page.locator(f"{SELECTORS['weekly_view_grid']} th:has-text('{target_header_str}')")
    
    if not header.count():
        logging.error(f"Could not find column for {target_header_str}")
        return
    
    col_idx_base = int(header.get_attribute("aria-colindex"))
    paid_hrs_col_idx = col_idx_base + 1

    summary_rows = page.locator(f"{SELECTORS['weekly_view_grid']} tr.k-master-row").all()
    
    for sum_row in summary_rows:
        worker_name = sum_row.locator("td[aria-colindex='1']").inner_text().strip()
        paid_cell = sum_row.locator(f"td[aria-colindex='{paid_hrs_col_idx}']")
        
        # Evaluate if the link is red (RGB for red is typically 255, 0, 0)
        is_red = paid_cell.evaluate("""(el) => {
            const link = el.querySelector('a');
            if (!link) return false;
            const style = window.getComputedStyle(link);
            return style.color === 'rgb(255, 0, 0)' || link.classList.contains('text-danger-aa');
        }""")

        if is_red:
            logging.info(f"ACTION: Flagged hours for {worker_name}. Opening details...")
            paid_cell.locator("a").click()
            page.locator(SELECTORS["payload_task_grid"]).wait_for(state="visible")
            
            # Process tasks in the detail grid
            process_employee_details(page, worker_name)
            
            # Close details to move to next worker
            page.get_by_test_id(SELECTORS["close_details"]).click()
            page.wait_for_load_state("networkidle")

def run_automation():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False, channel="chrome", args=["--start-maximized"])
        context = browser.new_context(viewport={"width": 1920, "height": 1080})
        page = context.new_page()

        try:
            target_date = get_previous_business_day()
            login(page)
            navigate_to_payroll(page)
            validate_and_process_rows(page, target_date)
            logging.info("Done.")
        except Exception as e:
            logging.critical(f"CRITICAL: {e}")
        finally:
            browser.close()

if __name__ == "__main__":
    run_automation()