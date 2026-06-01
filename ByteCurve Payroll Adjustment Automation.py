#ByteCurve Payroll Adjustment Automation App

import datetime
import os
from datetime import datetime as dt
from playwright.sync_api import sync_playwright, Page

# --- CONFIGURATION / PLACEHOLDERS ---
BYTECURVE_URL = "https://app.bytecurve360.com/portal/core/#/login"
USERNAME = os.getenv("BYTECURVE_USER", "")
PASSWORD = os.getenv("BYTECURVE_PASS", "")

# Task-specific policies for adjustments
TASK_POLICIES = {
    "Extra Work": {"max_allowed": 0.016666666666666666, "require_schedule_match": False}, # 1 minute in hours
    "S2S Charter": {"max_allowed": 0.016666666666666666, "require_schedule_match": False}, # 1 minute in hours
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
    "timesheet_rows": "tr[role='row']", 
    "row_worker_id": "td:nth-child(1)",    # Column index for Worker ID/Name
    "row_category": "td:nth-child(2)", # Adjust index based on Inspector find
    "row_schedule_start": "td:nth-child(6)", # Column index for Schedule Start Time
    "row_schedule_end": "td:nth-child(7)",   # Column index for Schedule End Time
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
    print(f"[INFO] Filtering for date: {target_date}")
    
    # Using the recording logic for filtering
    page.get_by_role("button", name=SELECTORS["date_filter_btn"]).click()
    # Note: Logic to select the specific date from the calendar grid may 
    # vary depending on the month. For now, we try a text-based match.
    day = target_date.split("-")[-1].lstrip("0")
    page.get_by_role("gridcell", name=day).first.click()
    page.get_by_test_id("filter-submit-btn").click()
    
    page.wait_for_load_state("networkidle")

    def parse_t(t_str):
        for fmt in ("%I:%M %p", "%H:%M", "%I:%M%p"):
            try: return dt.strptime(t_str.strip(), fmt)
            except: continue
        return None

    rows = page.locator(SELECTORS["timesheet_rows"]).all()
    if not rows:
        print(f"[WARN] No records found.")
        return

    # --- OVERLAP DETECTION PRE-SCAN ---
    worker_schedules = {}
    for row in rows:
        try:
            worker_id = row.locator(SELECTORS["row_worker_id"]).inner_text().strip()
            cat = row.locator(SELECTORS["row_category"]).inner_text().strip()
            start_text = row.locator(SELECTORS["row_schedule_start"]).inner_text().strip()
            end_text = row.locator(SELECTORS["row_schedule_end"]).inner_text().strip()

            if worker_id not in worker_schedules:
                worker_schedules[worker_id] = []
            
            s_dt, e_dt = parse_t(start_text), parse_t(end_text)
            if s_dt and e_dt:
                worker_schedules[worker_id].append({"start": s_dt, "end": e_dt, "category": cat})
        except Exception:
            continue

    processed_successfully = True

    for row in rows:
        try:
            worker_id = row.locator(SELECTORS["row_worker_id"]).inner_text().strip()
            category = row.locator(SELECTORS["row_category"]).inner_text().strip()
            
            # Conditional: avoid adjustment for "Brigde Charter" tasks (Priority)
            if category == "Brigde Charter":
                print(f"[REPORT] Task '{category}' is set to avoid adjustment. Skipping.")
                processed_successfully = False
                continue

            # --- OVERLAP ADJUSTMENT LOGIC ---
            row_s_txt = row.locator(SELECTORS["row_schedule_start"]).inner_text().strip()
            row_e_txt = row.locator(SELECTORS["row_schedule_end"]).inner_text().strip()
            cur_s, cur_e = parse_t(row_s_txt), parse_t(row_e_txt)
            
            if cur_s and cur_e:
                # Identify all other task overlaps for this worker
                overlaps = [t for t in worker_schedules.get(worker_id, []) 
                            if (t['start'] != cur_s or t['end'] != cur_e or t['category'] != category) 
                            and (cur_s < t['end'] and t['start'] < cur_e)]
                
                new_start_dt = None
                
                # Rule 1: "Extra Work" always shifts if it overlaps a different task
                if category == "Extra Work":
                    diff_task_overlaps = [t for t in overlaps if t['category'] != "Extra Work"]
                    if diff_task_overlaps:
                        new_start_dt = max(t['end'] for t in diff_task_overlaps)
                
                # Rule 2: General minor overlaps (< 15 mins) shift the later task
                if not new_start_dt:
                    for t in overlaps:
                        overlap_dur = min(cur_e, t['end']) - max(cur_s, t['start'])
                        if datetime.timedelta(0) < overlap_dur < datetime.timedelta(minutes=15):
                            if cur_s >= t['start']: # This is the "conflicted" (later) schedule
                                new_start_dt = t['end']
                                break

                if new_start_dt:
                    new_start_str = new_start_dt.strftime("%I:%M %p")
                    print(f"[ACTION] Minor overlap detected. Adjusting {category} for {worker_id} to start at {new_start_str}.")
                    if adjust_time_entry(page, row, new_start_str):
                        processed_successfully = False
                        continue

            # Conditional: avoid adjustment if there is an overlap in schedule time
            all_intervals = [(t['start'], t['end']) for t in worker_schedules.get(worker_id, [])]
            if check_for_overlaps(all_intervals):
                print(f"[REPORT] Overlap detected for {worker_id}. Skipping adjustment for reporting.")
                processed_successfully = False
                continue
            
            # Conditional: Only consider adjustments for rows where 'Paid Hours' is highlighted in Red
            actual_cell = row.locator(SELECTORS["row_actual_hours"])
            is_red = actual_cell.evaluate("""(el) => {
                const style = window.getComputedStyle(el);
                const red = 'rgb(255, 0, 0)';
                return style.color === red || style.backgroundColor === red || el.classList.contains('text-danger');
            }""")

            if not is_red:
                # Skip rows that the UI does not flag as needing adjustment
                continue

            actual_text = actual_cell.inner_text().strip()
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
                processed_successfully = False
                continue

        except Exception as e:
            print(f"[ERROR] Row processing error: {e}")
            processed_successfully = False

    # If all rows validated, perform the recorded bulk verification
    if processed_successfully:
        print("[ACTION] All rows valid. Performing bulk verification...")
        page.get_by_role("checkbox", name=SELECTORS["checkbox_verify_all"]).check()
        page.get_by_role("button", name=SELECTORS["btn_verify"]).click()
        page.get_by_test_id(SELECTORS["btn_bulk_ok"]).click()
    else:
        print("[WARN] Bulk verification skipped due to flagged rows or detected overlaps.")

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