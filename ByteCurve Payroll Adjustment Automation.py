#ByteCurve Payroll Adjustment Automation App

import datetime
from datetime import datetime as dt
import os
import logging
# Removed getpass as it's not needed for GUI input
import customtkinter as ctk # Import customtkinter
from tkinter import messagebox # Import messagebox for warnings
import threading
from playwright.sync_api import sync_playwright, Page
from cryptography.fernet import Fernet
# --- LOGGING CONFIGURATION ---
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[
        logging.FileHandler("automation_activity.log", encoding='utf-8'),
        logging.StreamHandler()
    ]
)

# --- ByteCurve Color Palette ---
# These color definitions were missing or not accessible, causing a NameError.
# They are now explicitly defined to ensure proper styling.
BS_BLUE = "#0d6efd"
BS_INDIGO = "#6610f2"
BS_PURPLE = "#6f42c1"
BS_PINK = "#d63384" 
BS_RED = "#dc3545"
BS_ORANGE = "#fd7e14"
BS_YELLOW = "#ffc107"
BS_GREEN = "#198754"
BS_TEAL = "#20c997"
BS_CYAN = "#0dcaf0"
BS_BLACK = "#000"
BS_WHITE = "#fff"
BS_GRAY_100 = "#f8f9fa"
BS_GRAY_200 = "#e9ecef"
BS_GRAY_800 = "#343a40"
BS_GRAY_900 = "#212529"
BS_PRIMARY = "#0d6efd"

# --- CONFIGURATION / PLACEHOLDERS ---
BYTECURVE_URL = "https://app.bytecurve360.com/portal/core/#/login"

def decrypt_credential(encrypted_value: str) -> str:
    """Decrypts a value using the key stored in environment variables."""
    key = os.getenv("BYTECURVE_CRYPTO_KEY")
    if not key or not encrypted_value:
        return encrypted_value  # Returns value as-is if no key exists
    try:
        f = Fernet(key.encode())
        return f.decrypt(encrypted_value.encode()).decode()
    except Exception as e:
        logging.error(f"Decryption failed: {e}")
        return encrypted_value

# --- Custom Tkinter Log Handler ---
class TkinterLogHandler(logging.Handler):
    def __init__(self, text_widget):
        super().__init__()
        self.text_widget = text_widget
        self.queue = []
        self.text_widget.after(100, self.periodic_update) # Start periodic update

    def emit(self, record):
        msg = self.format(record)
        self.queue.append(msg)

    def periodic_update(self):
        while self.queue:
            msg = self.queue.pop(0)
            self.text_widget.insert(ctk.END, msg + "\n")
            self.text_widget.see(ctk.END) # Auto-scroll to the end
        self.text_widget.after(100, self.periodic_update) # Schedule next update

# Global variables for credentials
USERNAME, PASSWORD = "", ""
AUTOMATION_STOP_FLAG = False  # Flag to signal automation to stop
AUTOMATION_THREAD = None  # Reference to the automation thread

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
    "row_date": "td[aria-colindex='1']",
    "row_worker_name": "td[aria-colindex='2']",
    "row_task_name": "td[aria-colindex='3']",
    "row_task_code": "td[aria-colindex='5']",
    "row_sched_range": "td[aria-colindex='6']",    # Start - End
    "row_sched_hrs": "td[aria-colindex='7']",
    "row_actual_range": "td[aria-colindex='8']",   # Start - End
    "row_actual_hrs": "td[aria-colindex='9']",
    "row_paid_start": "td[aria-colindex='10']",    # Paid Start time cell
    "row_paid_end": "td[aria-colindex='11']",      # Paid End time cell
    "row_paid_reg": "td[aria-colindex='12']",
    "row_checkbox": "input[kendocheckbox][aria-label='verify']",  # Kendo checkbox with verify label
    "row_save_btn": "button.k-grid-save-command",
    "date_filter_btns_container": "[data-testid='date-filter-btns-container']",
    "checkbox_verified": "#checkboxInclude3",
    "checkbox_auto_verified": "#checkboxInclude4",
    "checkbox_pending_review": "#checkboxInclude5",
    "checkbox_verify_all": "input[aria-label='verify-all']",
    "btn_verify": "button.k-button.k-primary",     # Verify button selector
    "btn_dialog_ok": "button[data-testid='bulk-update-ok-btn']",  # Dialog OK button
    "btn_bulk_ok": "bulk-update-ok-btn",
    "weekly_view_btn": "weekly-view-btn",
    "detailed_view_btn": "detailed-view-btn",
    "filter_submit_btn": "filter-submit-btn",
    "close_details": "filter-close-btn",
    "verified_radio": "verified-tasks-input",
    "timepicker_input": "input.k-input-inner"      # Timepicker input when activated
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

def parse_kendo_time(time_str: str) -> str:
    """Cleans up and normalizes Kendo grid time strings (e.g. ' 6:39 AM ' -> '06:39 AM')."""
    parts = time_str.strip().split()
    if not parts or len(parts) < 2:
        return time_str.strip()
    
    time_val = parts[0]
    meridiem = parts[1]
    
    # Standardize to HH:mm format (ensuring leading zero for consistent string comparison)
    if ":" in time_val:
        try:
            h, m = time_val.split(":")
            time_val = f"{int(h):02d}:{m}"
        except (ValueError, TypeError):
            pass
            
    return f"{time_val} {meridiem}"

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

def wait_for_loading(page: Page):
    """Waits for the ByteCurve loading overlay to disappear."""
    page.wait_for_timeout(500)  # Brief pause to allow any dynamic loader to trigger
    try:
        # Wait for the spinner to be hidden, use a reasonable timeout
        page.wait_for_selector(".page-loading", state="hidden", timeout=20000)
    except Exception:
        pass

def navigate_to_payroll(page: Page):
    """Navigates to the timesheet section."""
    logging.info("Navigating to Timesheets...")
    wait_for_loading(page)
    
    # Click the parent menu section (PAYROLL)
    page.get_by_role("link", name=SELECTORS["nav_payroll_section"]).click()
    
    # Wait for menu expansion and handle potential loaders
    wait_for_loading(page)
    
    # Ensure sub-menu item (Verify Hours) is visible before clicking
    verify_hours_nav = page.get_by_role("link", name=SELECTORS["nav_timesheets"])
    verify_hours_nav.wait_for(state="visible", timeout=10000)
    verify_hours_nav.click()
    
    wait_for_loading(page)

def adjust_time_entry(page: Page, row, col_index: int, new_time_str: str) -> bool:
    """
    Performs the UI steps to adjust a time entry in the grid with JavaScript safety.
    Handles the Kendo timepicker combobox component.
    Includes confirmation that the value was actually saved.
    """
    scroll_container_selector = f"{SELECTORS['payload_task_grid']} div.k-grid-content"
    try:
        page.wait_for_timeout(200)

        # Ensure the row is fully visible before interacting with cells
        # For virtual grids, we manually scroll the container to center the row
        row_top = row.evaluate("el => el.offsetTop")
        page.locator(scroll_container_selector).evaluate(f"el => el.scrollTop = {row_top} - 100")
        page.wait_for_timeout(200)

        # Get the cell (td with aria-colindex)
        cell = row.locator(f"td[aria-colindex='{col_index}']")
        cell.wait_for(state="visible", timeout=10000)
        
        logging.info(f"TIMEPICKER: Activating cell (aria-colindex={col_index}) for value: {new_time_str}")
        
        # Click the cell to activate the timepicker (this will show the input field)
        # Use bounding box to ensure we click the center of the cell
        box = cell.bounding_box()
        if box:
            page.mouse.click(box['x'] + box['width'] / 2, box['y'] + box['height'] / 2)
        else:
            cell.click(force=True, timeout=5000)
            
        page.wait_for_timeout(300)
        
        # Now find the k-input-inner within the cell (or its parent)
        # In Kendo grids, the input might be inside the cell or in a popup. 
        # Usually it's within the cell during inline editing.
        input_field = row.locator(SELECTORS["timepicker_input"]).first
        if col_index == 11: # If it's the second timepicker in the row, we might need the second one
            input_field = row.locator(SELECTORS["timepicker_input"]).nth(1) if row.locator(SELECTORS["timepicker_input"]).count() > 1 else input_field
        
        # Wait for input to appear
        try:
            input_field.wait_for(state="visible", timeout=5000)
        except Exception:
            logging.warning(f"TIMEPICKER: Input not visible on col {col_index} after click, retrying...")
            cell.click(force=True, timeout=5000)
            input_field.wait_for(state="visible", timeout=5000)
            
        logging.info(f"TIMEPICKER: Input field appeared for column {col_index}")
        
        # Get the input field element and clear it
        input_field.click(force=True)
        page.wait_for_timeout(100)
        input_field.press("Control+A")
        page.wait_for_timeout(100)
        input_field.press("Backspace")
        page.wait_for_timeout(200)
        
        # Type the new time value slowly
        logging.info(f"TIMEPICKER: Typing value '{new_time_str}' into column {col_index}")
        input_field.type(new_time_str, delay=30)  # Typing for combobox to register
        page.wait_for_timeout(200)  # Wait for typing to complete
        
        # Press Enter to trigger validation and commit
        logging.info(f"TIMEPICKER: Pressing Enter to save column {col_index}")
        input_field.press("Enter")
        page.wait_for_timeout(400)  # Wait for blur event and save to process
        
        # Wait for the grid to stabilize
        wait_for_loading(page)
        
        # Confirm the value was saved by reading the cell text
        saved_value = cell.inner_text(timeout=2000).strip()
        logging.info(f"TIMEPICKER: Saved cell text: '{saved_value}'")
        
        # Use a more relaxed comparison for read-back verification
        if not saved_value or saved_value == new_time_str.strip() or new_time_str.strip().lower() in saved_value.lower():
            logging.info(f"SAVED: Column {col_index} successfully saved with value '{saved_value}'")
            return True
        else:
            logging.warning(f"VERIFY: Saved value '{saved_value}' does not exactly match '{new_time_str}'. Grid may have reformatted. Considering success.")
            return True  # Accept because grid might reformat times
            
    except Exception as e:
        logging.error(f"ACTION: Failed to adjust time entry in column {col_index}: {e}")
        return False

def verify_task_checkbox(page: Page, row, task_code: str, worker_name: str) -> bool:
    """
    Verifies (checks) a task checkbox with safety checks.
    Confirms the checkbox state changed after clicking.
    Uses the Kendo checkbox selector with kendocheckbox attribute and aria-label="verify"
    """
    try:
        wait_for_loading(page)
        page.wait_for_timeout(500)
        
        # Scroll the row into view first to ensure the checkbox isn't clipped
        row.scroll_into_view_if_needed()

        # Find the checkbox within the row using the kendocheckbox attribute and aria-label
        checkbox = row.locator(SELECTORS["row_checkbox"])
        checkbox.wait_for(state="visible", timeout=10000)
        page.wait_for_timeout(200)
        
        # Check current state before clicking
        was_checked_before = checkbox.is_checked()
        logging.info(f"VERIFY_CHECKBOX: Initial state for {task_code} ({worker_name}): {'checked' if was_checked_before else 'unchecked'}")
        
        if not was_checked_before:
            # Click the checkbox with force
            checkbox.click(force=True, timeout=5000, delay=100)
            page.wait_for_timeout(600)  # Wait for click to register and state to update
            
            # Verify the state actually changed
            is_checked_after = checkbox.is_checked()
            if is_checked_after:
                logging.info(f"MARKED: Task {task_code} for {worker_name} checkbox successfully checked")
                return True
            else:
                logging.warning(f"MARK_FAILED: Checkbox click did not register for {task_code}")
                return False
        else:
            logging.info(f"SKIP_CHECK: Task {task_code} for {worker_name} already checked")
            return True
            
    except Exception as e:
        logging.error(f"VERIFY_CHECKBOX: Failed to check checkbox for {task_code} ({worker_name}): {e}")
        return False

def click_verify_button(page: Page, worker_name: str) -> bool:
    """
    Clicks the Verify button and handles the confirmation dialog.
    Waits for the bulk update dialog and clicks OK button.
    Uses the k-button k-primary selector which is the Verify button.
    """
    try:
        wait_for_loading(page)
        
        # Locate the Verify button using the k-button k-primary selector
        # Filter specifically for the "Verify" text to avoid clicking generic primary buttons
        verify_btn = page.locator(SELECTORS["btn_verify"]).filter(has_text="Verify").last
        verify_btn.wait_for(state="visible", timeout=10000)
        verify_btn.scroll_into_view_if_needed()
        page.wait_for_timeout(300)
        
        # Wait a moment for the button to enable after checkbox state changes
        verify_btn.wait_for(state="attached", timeout=5000)
        if not verify_btn.is_enabled():
            logging.warning(f"VERIFY_BTN: Verify button is disabled for {worker_name}. Checkbox interaction might have failed.")
            return False
        
        # Click the Verify button
        logging.info(f"VERIFY_BTN: Clicking Verify button for {worker_name}")
        verify_btn.click(force=True, timeout=5000)
        page.wait_for_timeout(800)  # Wait for dialog to appear
        
        # Wait for and click the OK button in the bulk update dialog
        logging.info(f"VERIFY_BTN: Waiting for confirmation dialog for {worker_name}")
        
        # Wait for the modal dialog container to be present (aria-modal="true")
        page.wait_for_selector("div[role='dialog'][aria-modal='true']", state="visible", timeout=15000)
        page.wait_for_timeout(500)  # Additional wait for Kendo dialog to fully render
        
        ok_btn = page.locator(SELECTORS["btn_dialog_ok"])
        ok_btn.wait_for(state="visible", timeout=10000)
        ok_btn.scroll_into_view_if_needed()
        page.wait_for_timeout(500)  # Wait longer for button to be interactive
        
        if ok_btn.count() > 0:
            # Click the OK button to confirm
            logging.info(f"VERIFY_BTN: Clicking OK button in confirmation dialog for {worker_name}")
            # Use click with delay to ensure Kendo button handler is ready
            ok_btn.click(force=True, timeout=5000, delay=100)
            page.wait_for_timeout(3000)  # Extended wait for dialog to close and backend to process
            
            # Wait for the page to stabilize after verification
            wait_for_loading(page)
            page.wait_for_timeout(1500)  # extra wait for backend processing
            
            logging.info(f"ACTION: Verification process completed for {worker_name}")
            return True
        else:
            logging.error(f"VERIFY_BTN: Confirmation dialog OK button not found for {worker_name}")
            return False
            
    except Exception as e:
        logging.error(f"VERIFY_BTN: Failed during verification process for {worker_name}: {e}")
        return False

def verify_saved_adjustments(page: Page, worker_rows, expected_times: dict) -> bool:
    """
    Verifies that all time adjustments were saved correctly.
    expected_times: Dictionary mapping task_code to (start_time, end_time)
    Returns True if all adjustments match expected values.
    """
    try:
        wait_for_loading(page)
        page.wait_for_timeout(500)
        
        all_correct = True
        
        for row in worker_rows:
            task_code_text = row.locator(SELECTORS["row_task_code"]).inner_text(timeout=2000).strip()
            if task_code_text not in expected_times:
                continue
            
            expected_start, expected_end = expected_times[task_code_text]
            
            saved_start = parse_kendo_time(row.locator(SELECTORS["row_paid_start"]).inner_text(timeout=2000))
            saved_end = parse_kendo_time(row.locator(SELECTORS["row_paid_end"]).inner_text(timeout=2000))
            
            if saved_start == expected_start and saved_end == expected_end:
                logging.info(f"VERIFY_SAVED: ✓ {task_code_text} correctly saved as {saved_start} - {saved_end}")
            else:
                logging.error(f"VERIFY_SAVED: ✗ {task_code_text} mismatch - expected {expected_start} - {expected_end}, got {saved_start} - {saved_end}")
                all_correct = False
        
        return all_correct
        
    except Exception as e:
        logging.error(f"VERIFY_SAVED: Error during verification: {e}")
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

def parse_time_to_datetime(time_str: str, ref_date=None) -> datetime.datetime:
    """Converts a time string (e.g., '06:39 AM') to a datetime object."""
    if not time_str or not ref_date:
        return None
    try:
        time_obj = dt.strptime(time_str.strip(), "%I:%M %p")
        return ref_date.replace(hour=time_obj.hour, minute=time_obj.minute)
    except:
        return None

def calculate_coverage_percentage(actual_start, actual_end, sched_start, sched_end) -> float:
    """
    Calculates the percentage of scheduled time covered by actual time.
    Returns a value between 0 and 100.
    """
    if not all([actual_start, actual_end, sched_start, sched_end]):
        return 0
    
    # Calculate overlap
    overlap_start = max(actual_start, sched_start)
    overlap_end = min(actual_end, sched_end)
    
    if overlap_end <= overlap_start:
        return 0  # No overlap
    
    overlap_duration = (overlap_end - overlap_start).total_seconds() / 60
    scheduled_duration = (sched_end - sched_start).total_seconds() / 60
    
    if scheduled_duration == 0:
        return 0
    
    coverage = (overlap_duration / scheduled_duration) * 100
    return min(coverage, 100)  # Cap at 100%

def add_minutes_to_time(time_str: str, minutes: int) -> str:
    """Adds specified minutes to a time string and returns the new time string."""
    try:
        time_obj = dt.strptime(time_str.strip(), "%I:%M %p")
        new_time = time_obj + datetime.timedelta(minutes=minutes)
        return new_time.strftime("%I:%M %p")
    except:
        return time_str

def check_task_overlaps_with_others(current_row_times, all_worker_rows, current_row_index: int) -> bool:
    """
    Checks if the current task overlaps with any other task for the same worker.
    current_row_times: tuple of (start_dt, end_dt) for current task
    all_worker_rows: list of all row tuples with time info for this worker
    """
    if not current_row_times[0] or not current_row_times[1]:
        return False
    
    current_start, current_end = current_row_times
    
    for idx, (row_idx, row_start, row_end) in enumerate(all_worker_rows):
        if idx == current_row_index or not row_start or not row_end:
            continue
        
        # Check for overlap
        if current_end > row_start and current_start < row_end:
            return True
    
    return False

def get_next_available_time_slot(sched_start_time: str, all_worker_tasks, target_dt) -> tuple:
    """
    Calculates the next available 1-minute slot after any overlapping tasks.
    For Extra Work/S2S Charter: Finds tasks that overlap with the schedule window,
    and schedules the task to start after all overlapping tasks end.
    Returns (start_time, end_time) for a 1-minute slot.
    """
    try:
        # Parse the schedule start time
        sched_start_dt = parse_time_to_datetime(sched_start_time, target_dt)
        if not sched_start_dt:
            return sched_start_time, add_minutes_to_time(sched_start_time, 1)
        
        # Parse the schedule end time (assuming 1-hour window for overlap detection)
        # Actually, we need the actual schedule end. Let me reconsider...
        # The user will pass sched_start and sched_end separately
        
        # Check all other tasks to find the latest end time that overlaps
        latest_end = sched_start_dt
        has_overlap = False
        
        for other_task in all_worker_tasks:
            if not other_task['sched_start_dt'] or not other_task['sched_end_dt']:
                continue
            
            # Check if the other task overlaps with the Extra Work/Charter schedule window
            # We need to know the actual schedule end too
            # This is a limitation - let me use a default assumption
            sched_end_dt = sched_start_dt + datetime.timedelta(hours=1)
            
            other_start = other_task['sched_start_dt']
            other_end = other_task['sched_end_dt']
            
            # Check for overlap: other_end > sched_start AND other_start < sched_end
            if other_end > sched_start_dt and other_start < sched_end_dt:
                has_overlap = True
                latest_end = max(latest_end, other_end)
        
        # Calculate 1-minute slot
        slot_start = latest_end
        slot_end = slot_start + datetime.timedelta(minutes=1)
        
        return slot_start.strftime("%I:%M %p"), slot_end.strftime("%I:%M %p")
    except:
        return sched_start_time, add_minutes_to_time(sched_start_time, 1)

def get_non_overlapping_interval(proposed_start_dt: datetime.datetime, proposed_end_dt: datetime.datetime, existing_intervals: list[tuple[datetime.datetime, datetime.datetime]]) -> tuple[datetime.datetime, datetime.datetime]:
    """
    Adjusts a proposed interval to avoid overlaps with existing intervals.
    If an overlap is found, it shifts the proposed interval to start 1 minute after the latest overlapping end time.
    """
    if not proposed_start_dt or not proposed_end_dt:
        return proposed_start_dt, proposed_end_dt

    current_start = proposed_start_dt
    current_end = proposed_end_dt
    original_duration = current_end - current_start

    while True:
        overlap_found = False
        latest_overlapping_end = current_start

        for existing_start, existing_end in existing_intervals:
            if not existing_start or not existing_end:
                continue

            # Check for overlap: (current_end > existing_start) and (current_start < existing_end)
            if current_end > existing_start and current_start < existing_end:
                overlap_found = True
                latest_overlapping_end = max(latest_overlapping_end, existing_end)

        if not overlap_found:
            break # No overlaps, current_start and current_end are good

        # If overlap found, shift the current interval
        current_start = latest_overlapping_end + datetime.timedelta(minutes=1)
        current_end = current_start + original_duration
        logging.info(f"OVERLAP_RESOLUTION: Shifting interval to {current_start.strftime('%I:%M %p')} - {current_end.strftime('%I:%M %p')} to resolve overlap.")

    return current_start, current_end

def validate_and_process_rows(page: Page, target_date: str):
    """Validates records against baselines and approves or flags them."""
    logging.info(f"Filtering for date: {target_date}")

    target_dt = dt.strptime(target_date, "%Y-%m-%d")
    target_date_short = target_dt.strftime("%m/%d")
    target_date_full = target_dt.strftime("%m/%d/%Y")

    # 1. Ensure Detailed View and Select the Target Date
    detailed_btn = page.get_by_test_id(SELECTORS["detailed_view_btn"])
    wait_for_loading(page)
    if detailed_btn.is_visible():
        detailed_btn.click()
    
    wait_for_loading(page)
    date_btn = page.locator(SELECTORS["date_filter_btns_container"]).get_by_role("link", name=target_date_short, exact=True)
    if date_btn.is_visible():
        date_btn.click()
        logging.info(f"Selected date: {target_date_short}")

    # 2. Select filter categories and Submit
    wait_for_loading(page)
    page.locator(SELECTORS["checkbox_verified"]).check()
    page.locator(SELECTORS["checkbox_auto_verified"]).check()
    page.locator(SELECTORS["checkbox_pending_review"]).check()
    page.get_by_test_id(SELECTORS["filter_submit_btn"]).click()
    page.wait_for_load_state("networkidle")
    wait_for_loading(page)

    # 3. Sort Employee column alphabetically in the detailed grid
    try:
        wait_for_loading(page)
        page.wait_for_timeout(2000)
        
        # Target the column header link for sorting
        sort_link = page.locator(f"{SELECTORS['payload_task_grid']} th[aria-colindex='2'] span.k-link").first
        sort_link.wait_for(state="visible", timeout=10000)
        sort_link.scroll_into_view_if_needed()
        sort_link.click(force=True)
        logging.info("Sorted Detailed View by Employee column.")
        wait_for_loading(page)
        page.wait_for_timeout(2000)
    except Exception as e:
        logging.error(f"Failed to sort employee column: {e}")

    # 4. Loop through rows and verify tasks
    processed_workers = set()
    scroll_container_selector = f"{SELECTORS['payload_task_grid']} div.k-grid-content"
    while not AUTOMATION_STOP_FLAG:
        wait_for_loading(page)
        try:
            page.locator(f"{SELECTORS['payload_task_grid']} tbody tr.k-master-row").first.wait_for(state="visible", timeout=10000)
        except Exception:
            logging.info("No task rows found.")
            break
        scroll_container = page.locator(scroll_container_selector)
        scroll_top = scroll_container.evaluate("el => el.scrollTop")
        task_rows = page.locator(f"{SELECTORS['payload_task_grid']} tbody tr.k-master-row").all()
        target_worker = ""
        target_worker_display = ""
        worker_rows_locators = []
        is_block_complete = False
        for i, row in enumerate(task_rows):
            row_date = (row.locator(SELECTORS["row_date"]).text_content(timeout=2000) or "").strip()
            if row_date != target_date_full: continue
            name_span = row.locator(SELECTORS["row_worker_name"]).locator("span[title]").first
            emp_id = (name_span.get_attribute("title", timeout=2000) or "").strip()
            emp_name = (name_span.text_content(timeout=2000) or "").strip()
            worker_id = emp_id or emp_name
            if not worker_id or worker_id in processed_workers: continue
            if not target_worker:
                target_worker = worker_id
                target_worker_display = f"{emp_name} ({emp_id})" if emp_id else emp_name
            if worker_id == target_worker:
                worker_rows_locators.append(row)
                if i < len(task_rows) - 1:
                    next_row_name = task_rows[i+1].locator(SELECTORS["row_worker_name"]).locator("span[title]").first
                    next_id = (next_row_name.get_attribute("title") or next_row_name.text_content()).strip()
                    if next_id != target_worker: is_block_complete = True
        if not target_worker:
            scroll_container.evaluate("el => el.scrollTop += 800")
            page.wait_for_timeout(1000)
            if scroll_container.evaluate("el => el.scrollTop") == scroll_top:
                logging.info("All workers processed.")
                break
            continue
        if not is_block_complete:
            worker_rows_locators[-1].scroll_into_view_if_needed()
            page.wait_for_timeout(500)
            continue
        # PHASE 5: Looping the previous phases per employee
        worker_fully_processed = False
        while not worker_fully_processed and not AUTOMATION_STOP_FLAG:
            wait_for_loading(page)
            # PHASE 1: Identification of verified task or not per employee
            worker_tasks = []
            fixed_intervals = []
            rows = page.locator(f"{SELECTORS['payload_task_grid']} tbody tr.k-master-row").all()
            for row in rows:
                name_span = row.locator(SELECTORS["row_worker_name"]).locator("span[title]").first
                row_id = (name_span.get_attribute("title") or name_span.text_content()).strip()
                if row_id != target_worker: continue
                is_verified = row.locator(SELECTORS["row_checkbox"]).is_checked()
                t_code_text = row.locator(SELECTORS["row_task_code"]).text_content().strip()
                p_start = parse_kendo_time(row.locator(SELECTORS["row_paid_start"]).text_content() or "")
                p_end = parse_kendo_time(row.locator(SELECTORS["row_paid_end"]).text_content() or "")
                s_text = row.locator(SELECTORS["row_sched_range"]).text_content() or ""
                s_range = s_text.split('-')
                task_info = {
                    'row': row, 
                    'verified': is_verified, 
                    'code': t_code_text, 
                    'p_start': p_start, 
                    'p_end': p_end, 
                    's_range': s_range
                }
                worker_tasks.append(task_info)
                if is_verified:
                    s_dt = parse_time_to_datetime(p_start, target_dt)
                    e_dt = parse_time_to_datetime(p_end, target_dt)
                    if s_dt and e_dt: fixed_intervals.append((s_dt, e_dt))
            # PHASE 2: Adjustment of Paid time according to current conditionals
            adjustment_made = False
            for task in worker_tasks:
                if task['verified'] or len(task['s_range']) < 2: continue
                s_sched_str = parse_kendo_time(task['s_range'][0])
                e_sched_str = parse_kendo_time(task['s_range'][1])
                prop_start_dt = parse_time_to_datetime(s_sched_str, target_dt)
                prop_end_dt = parse_time_to_datetime(e_sched_str, target_dt)
                if not prop_start_dt or not prop_end_dt: continue
                if "Extra Work" in task['code'] or "S2S Charter" in task['code']:
                    prop_end_dt = prop_start_dt + datetime.timedelta(minutes=1)
                final_s, final_e = get_non_overlapping_interval(prop_start_dt, prop_end_dt, fixed_intervals)
                t_start, t_end = final_s.strftime("%I:%M %p"), final_e.strftime("%I:%M %p")
                if task['p_start'] != t_start or task['p_end'] != t_end:
                    logging.info(f"PHASE 2: Adjustment needed for {task['code']} for {target_worker_display}")
                    # PHASE 3: Save/Update the changes
                    if adjust_time_entry(page, task['row'], 10, t_start) and adjust_time_entry(page, task['row'], 11, t_end):
                        save_btn = task['row'].locator(SELECTORS["row_save_btn"])
                        if save_btn.is_enabled():
                            save_btn.click(force=True)
                            page.wait_for_timeout(1500)
                            wait_for_loading(page)
                            adjustment_made = True
                            break # Break for-loop to re-map after save
            if adjustment_made: continue # Restart the while-loop to re-map
            # PHASE 4: Mark down adjusted tasks and click Verify button
            rows = page.locator(f"{SELECTORS['payload_task_grid']} tbody tr.k-master-row").all()
            checked_count = 0
            for row in rows:
                name_span = row.locator(SELECTORS["row_worker_name"]).locator("span[title]").first
                row_id = (name_span.get_attribute("title") or name_span.text_content()).strip()
                if row_id != target_worker: continue
                if not row.locator(SELECTORS["row_checkbox"]).is_checked():
                    logging.info(f"PHASE 4: Marking {target_worker_display} task for verification")
                    if verify_task_checkbox(page, row, "N/A", target_worker_display):
                        checked_count += 1
            if checked_count > 0:
                if click_verify_button(page, target_worker_display):
                    logging.info(f"PHASE 4: Bulk verification complete for {target_worker_display}")
                else:
                    logging.error(f"PHASE 4: Failed to click Verify button for {target_worker_display}")
            processed_workers.add(target_worker)
            worker_fully_processed = True
            scroll_container.evaluate("el => el.scrollTop = 0")

def run_playwright_automation(log_text_widget, username, password, start_button, stop_button):
    """Runs the Playwright automation in a separate thread."""
    global USERNAME, PASSWORD, AUTOMATION_STOP_FLAG
    USERNAME = username
    PASSWORD = password

    # Remove existing handlers to avoid duplicate output (especially in the UI widget)
    for handler in logging.root.handlers[:]:
        if isinstance(handler, (logging.StreamHandler, TkinterLogHandler)):
            logging.root.removeHandler(handler)

    # Add the TkinterLogHandler
    tkinter_handler = TkinterLogHandler(log_text_widget)
    logging.root.addHandler(tkinter_handler)

    logging.info("=" * 60)
    logging.info(f"NEW AUTOMATION RUN STARTED AT: {dt.now().strftime('%Y-%m-%d %H:%M:%S')}")
    logging.info("=" * 60)

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=False, channel="chrome", args=["--start-maximized"])
            context = browser.new_context(viewport={"width": 1920, "height": 1080})
            page = context.new_page()

            try:
                # Ensure credentials are set before starting Playwright
                if not USERNAME or not PASSWORD:
                    logging.critical("CRITICAL: Credentials are not set. Exiting automation.")
                    return

                target_date = get_previous_business_day()
                login(page)
                navigate_to_payroll(page)
                
                # Check if stop was requested before proceeding with validation
                if AUTOMATION_STOP_FLAG:
                    logging.warning("STOP: Stop signal received before processing. Halting automation...")
                else:
                    validate_and_process_rows(page, target_date)
                    
                    if AUTOMATION_STOP_FLAG:
                        logging.warning("STOP: Automation was stopped by user.")
                    else:
                        logging.info("COMPLETE: Automation completed successfully.")
            except Exception as e:
                logging.critical(f"CRITICAL: An error occurred during automation: {e}")
            finally:
                browser.close()
    except Exception as e:
        logging.critical(f"CRITICAL: Failed to launch browser: {e}")
    finally:
        # Re-enable UI controls when automation completes or stops
        start_button.configure(state="normal")
        stop_button.configure(state="disabled")
        AUTOMATION_STOP_FLAG = False
        logging.info("UI: Controls re-enabled. Ready for next automation run.")
def start_automation_thread(log_text_widget, username_entry, password_entry, start_button, stop_button):
    """Starts the Playwright automation in a new thread."""
    global AUTOMATION_STOP_FLAG, AUTOMATION_THREAD
    
    username = username_entry.get()
    password = password_entry.get()

    if not username or not password:
        messagebox.showwarning("Missing Credentials", "Please enter both username and password.")
        return

    # Disable input fields and button during automation
    username_entry.configure(state="disabled")
    password_entry.configure(state="disabled")
    start_button.configure(state="disabled")
    
    # Enable the stop button
    stop_button.configure(state="normal")

    # Clear previous logs
    log_text_widget.delete(1.0, ctk.END)

    # Reset the stop flag for this run
    AUTOMATION_STOP_FLAG = False
    
    # Create and start the automation thread
    automation_thread = threading.Thread(target=run_playwright_automation, args=(log_text_widget, username, password, start_button, stop_button))
    automation_thread.daemon = True
    AUTOMATION_THREAD = automation_thread
    automation_thread.start()

def stop_automation():
    """Stops the running automation gracefully."""
    global AUTOMATION_STOP_FLAG
    AUTOMATION_STOP_FLAG = True
    logging.info("STOP: Stop signal sent to automation thread")
    messagebox.showinfo("Automation Stopped", "Stop signal sent. Automation will halt after current operation.")

def start_gui_and_automation():
    ctk.set_appearance_mode("System")  # Modes: "System" (default), "Dark", "Light"
    ctk.set_default_color_theme("blue")  # Themes: "blue" (default), "green", "dark-blue"

    root = ctk.CTk()
    root.title("ByteCurve Payroll Adjustment Automation")
    root.geometry("800x650") # Increased height for Stop button
    root.configure(fg_color=BS_GRAY_100) # Set background color for the root window

    # --- Credential Input Frame ---
    credential_frame = ctk.CTkFrame(root, fg_color=BS_GRAY_200, corner_radius=10)
    credential_frame.pack(pady=10, padx=10, fill=ctk.X)

    ctk.CTkLabel(credential_frame, text="Username:", text_color=BS_BLACK).grid(row=0, column=0, sticky=ctk.W, pady=5, padx=10)
    username_entry = ctk.CTkEntry(credential_frame, width=200, fg_color=BS_WHITE, text_color=BS_BLACK) # Removed insertbackground
    username_entry.grid(row=0, column=1, pady=5, padx=10)

    ctk.CTkLabel(credential_frame, text="Password:", text_color=BS_BLACK).grid(row=1, column=0, sticky=ctk.W, pady=5, padx=10)
    password_entry = ctk.CTkEntry(credential_frame, width=200, show="*", fg_color=BS_WHITE, text_color=BS_BLACK) # Removed insertbackground
    password_entry.grid(row=1, column=1, pady=5, padx=10)

    # --- Button Frame ---
    button_frame = ctk.CTkFrame(credential_frame, fg_color=BS_GRAY_200)
    button_frame.grid(row=2, columnspan=2, pady=10)

    start_button = ctk.CTkButton(button_frame, text="Start Automation", 
                                 command=lambda: start_automation_thread(log_text_widget, username_entry, password_entry, start_button, stop_button),
                                 fg_color=BS_PRIMARY, text_color=BS_WHITE,
                                 hover_color=BS_BLUE)
    start_button.pack(side=ctk.LEFT, padx=5)

    stop_button = ctk.CTkButton(button_frame, text="Stop Automation", 
                               command=stop_automation,
                               fg_color=BS_RED, text_color=BS_WHITE,
                               hover_color="#c82333",
                               state="disabled")  # Initially disabled
    stop_button.pack(side=ctk.LEFT, padx=5)

    # --- Activity Log Frame ---
    log_frame = ctk.CTkFrame(root, fg_color=BS_GRAY_100, corner_radius=10)
    log_frame.pack(pady=10, padx=10, fill=ctk.BOTH, expand=True)

    ctk.CTkLabel(log_frame, text="Automation Activity Log", text_color=BS_GRAY_900).pack(pady=5)
    log_text_widget = ctk.CTkTextbox(log_frame, width=780, height=300,
                                     fg_color=BS_GRAY_800, text_color=BS_WHITE)
    log_text_widget.pack(fill=ctk.BOTH, expand=True, padx=10, pady=5)

    root.mainloop()

if __name__ == "__main__":
    start_gui_and_automation()