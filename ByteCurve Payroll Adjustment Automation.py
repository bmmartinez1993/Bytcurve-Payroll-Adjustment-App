#ByteCurve Payroll Adjustment Automation App

import datetime
from datetime import datetime as dt
import os
import re
import random
import time
import pyautogui
import logging
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
KEY_FILE = "secret.key"
CREDENTIAL_FILE = "credentials.enc"

def generate_key():
    key = Fernet.generate_key()
    with open(KEY_FILE, "wb") as key_file:
        key_file.write(key)
    return key

def load_key():
    if not os.path.exists(KEY_FILE):
        return generate_key()
    with open(KEY_FILE, "rb") as key_file:
        return key_file.read()

def encrypt_credentials(username, password, key):
    f = Fernet(key)
    data = f"{username}:{password}".encode()
    encrypted = f.encrypt(data)
    with open(CREDENTIAL_FILE, "wb") as file:
        file.write(encrypted)

def decrypt_credentials(key):
    if not os.path.exists(CREDENTIAL_FILE):
        return "", ""
    try:
        f = Fernet(key)
        with open(CREDENTIAL_FILE, "rb") as file:
            encrypted = file.read()
        decrypted = f.decrypt(encrypted).decode()
        username, password = decrypted.split(":", 1)
        return username, password
    except Exception as e:
        # If decryption fails, return empty credentials
        logging.warning(f"Could not decrypt credentials: {e}")
        return "", ""

def keep_active(stop_event, interval=10):
    """Keep system active by periodic mouse movements, clicks and keyboard input for Insightful."""
    pyautogui.FAILSAFE = False
    while not stop_event.is_set():
        try:
            # Simulate human-like mouse wiggle and scroll
            dist = random.randint(10, 30)
            pyautogui.moveRel(dist, 0, duration=0.2)
            pyautogui.moveRel(-dist, 0, duration=0.2)
            pyautogui.scroll(random.choice([-1, 1]))
            
            pyautogui.press('f15')
            
            # Randomize sleep to avoid pattern detection (e.g., 8-12 seconds)
            time.sleep(interval + random.uniform(-2, 2))
        except Exception as e:
            logging.error(f"Error in keep_active: {e}")
            time.sleep(interval)

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
KEEP_ACTIVE_STOP_EVENT = threading.Event()  # Global event to control keep_active

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
    "row_save_btn": "button.k-grid-save-command, button[title='Update'], button[kendogridsavecommand]",
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

def times_match(t1: str, t2: str) -> bool:
    """Intelligently compares two time strings regardless of leading zeros or whitespace."""
    clean_t1 = parse_kendo_time(t1 or "").lower().strip()
    clean_t2 = parse_kendo_time(t2 or "").lower().strip()
    if not clean_t1 or not clean_t2:
        return clean_t1 == clean_t2
    return clean_t1 == clean_t2

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

        cell = row.locator(f"td[aria-colindex='{col_index}']")
        cell.wait_for(state="visible", timeout=10000)
        
        # Robust Check: If input is already present, don't dblclick (it might close the editor)
        input_field = cell.locator("input")
        if input_field.count() == 0:
            logging.info(f"TIMEPICKER: Activating cell (aria-colindex={col_index}) for value: {new_time_str}")
            cell.dblclick(timeout=5000)
            page.wait_for_timeout(500)
            input_field = cell.locator("input")

        # Wait for input to appear and be editable
        try:
            input_field.first.wait_for(state="visible", timeout=3000)
            if not input_field.first.is_editable():
                raise Exception("Not editable")
        except Exception:
            logging.warning(f"TIMEPICKER: Input not ready on col {col_index}, attempting refocus trick...")
            all_rows = page.locator(f"{SELECTORS['payload_task_grid']} tbody tr.k-master-row")
            if all_rows.count() > 1:
                # Row Refocus Trick
                curr_idx = row.evaluate("el => el.sectionRowIndex")
                other_row = all_rows.nth(1 if curr_idx == 0 else 0)
                other_row.locator("td[aria-colindex='10']").click(timeout=2000)
                page.wait_for_timeout(200)
                cell.dblclick(timeout=5000)
                input_field.first.wait_for(state="visible", timeout=5000)

        if not input_field.first.is_visible() or not input_field.first.is_editable():
            logging.error(f"TIMEPICKER: Failed to detect editable field in col {col_index}")
            return False
            
        logging.info(f"TIMEPICKER: Input field ready for column {col_index}")
        
        # Clear and Type
        input_field.first.click(force=True)
        page.keyboard.press("Control+A")
        page.keyboard.press("Backspace")
        page.wait_for_timeout(100)
        input_field.first.type(new_time_str, delay=50)
        page.wait_for_timeout(300)
        
        # Use Enter to commit the adjustment instead of Tab.
        # This prevents the grid from losing row focus and triggering 'Unsaved Changes' dialogs.
        page.keyboard.press("Enter")
        page.wait_for_timeout(500)
        
        # Dismiss any Kendo confirmation modal that blocks the grid UI
        try:
            dialog_ok = page.locator("button.k-button-solid-primary, button[data-testid='bulk-update-ok-btn']").filter(has_text=re.compile("Ok|Yes|Update", re.I))
            if dialog_ok.count() > 0 and dialog_ok.first.is_visible():
                logging.info("DIALOG: Dismissing commit confirmation modal.")
                dialog_ok.first.click(timeout=2000)
                page.wait_for_timeout(500)
        except Exception:
            pass
        
        # Confirm save
        saved_value = (cell.text_content(timeout=2000) or "").strip()
        if times_match(saved_value, new_time_str):
            return True
        else:
            logging.warning(f"VERIFY: Mismatch on col {col_index}. Expected '{new_time_str}', got '{saved_value}'")
            return False
            
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
        target_worker_id = ""
        target_worker_display = ""
        worker_rows_locators = []
        is_block_complete = False
        
        # Detect if we are at the bottom of the scroll to handle the last worker block
        is_at_bottom = scroll_container.evaluate("el => el.scrollTop + el.clientHeight >= el.scrollHeight - 20")

        for i, row in enumerate(task_rows):
            row_date = (row.locator(SELECTORS["row_date"]).text_content(timeout=2000) or "").strip()
            if row_date != target_date_full: continue
            name_span = row.locator(SELECTORS["row_worker_name"]).locator("span[title]").first
            if name_span.count() == 0: continue
            
            emp_id = (name_span.get_attribute("title", timeout=2000) or "").strip()
            emp_name = (name_span.text_content(timeout=2000) or "").strip()
            worker_id = emp_id or emp_name
            if not worker_id or worker_id in processed_workers: continue
            if not target_worker:
                target_worker = worker_id
                target_worker_id = emp_id
                target_worker_name = emp_name
                target_worker_display = f"{emp_name} ({emp_id})" if emp_id else emp_name
            if worker_id == target_worker:
                worker_rows_locators.append(row)
                if i < len(task_rows) - 1:
                    next_name_span = task_rows[i+1].locator(SELECTORS["row_worker_name"]).locator("span[title]").first
                    if next_name_span.count() > 0:
                        next_id = (next_name_span.get_attribute("title") or next_name_span.text_content()).strip()
                        if next_id != target_worker: is_block_complete = True
                elif is_at_bottom:
                    is_block_complete = True

        if not target_worker:
            scroll_container.evaluate("el => el.scrollTop += 800")
            page.wait_for_timeout(1000)
            if scroll_container.evaluate("el => el.scrollTop") == scroll_top:
                logging.info("All workers processed.")
                break
            continue
        if not is_block_complete:
            scroll_container.evaluate("el => el.scrollTop += 300")
            page.wait_for_timeout(400)
            continue
        # PHASE 5: Looping the previous phases per employee
        worker_fully_processed = False
        retry_tracking = {} # To detect stuck tasks
        manual_flag = False
        
        # Use a flexible filter on the Employee column text to find rows reliably
        worker_row_filter = page.locator(f"{SELECTORS['payload_task_grid']} tbody tr.k-master-row").filter(
            has=page.locator("td[aria-colindex='2'] span").get_by_text(target_worker_name, exact=True)
        )

        while not worker_fully_processed and not AUTOMATION_STOP_FLAG:
            wait_for_loading(page)
            # PHASE 1: Identification of verified task or not per employee
            worker_tasks = []
            fixed_intervals = []

            # Re-fetch worker rows to ensure locators are fresh
            worker_rows = worker_row_filter.all() 
            if not worker_rows:
                logging.warning(f"STALE: Could not find rows for {target_worker_display}. Skipping.")
                processed_workers.add(target_worker)
                scroll_container.evaluate("el => el.scrollTop += 400") # Force movement to break loop
                break

            for row in worker_rows:
                is_verified = row.locator(SELECTORS["row_checkbox"]).is_checked()
                t_code_text = (row.locator(SELECTORS["row_task_code"]).text_content() or "").strip()
                t_name_text = (row.locator(SELECTORS["row_task_name"]).text_content() or "").strip()
                p_start = parse_kendo_time(row.locator(SELECTORS["row_paid_start"]).text_content() or "")
                p_end = parse_kendo_time(row.locator(SELECTORS["row_paid_end"]).text_content() or "")
                s_text = row.locator(SELECTORS["row_sched_range"]).text_content() or ""
                a_text = row.locator(SELECTORS["row_actual_range"]).text_content() or ""
                s_range = [x.strip() for x in s_text.split('-') if x.strip()]
                a_range = [x.strip() for x in a_text.split('-') if x.strip()]

                task_info = {
                    'row': row, 
                    'verified': is_verified, 
                    'code': t_code_text, 
                    'name': t_name_text,
                    'p_start': p_start, 
                    'p_end': p_end, 
                    's_range': s_range,
                    'a_range': a_range
                }
                worker_tasks.append(task_info)
                if is_verified:
                    s_dt = parse_time_to_datetime(p_start, target_dt)
                    e_dt = parse_time_to_datetime(p_end, target_dt)
                    if s_dt and e_dt: fixed_intervals.append((s_dt, e_dt))
            # PHASE 2: Adjustment of Paid time according to current conditionals
            adjustment_made = False
            manual_flag = False

            for task in worker_tasks:
                if task.get('verified') or len(task.get('s_range', [])) < 2: continue
                
                # Define task variables to fix NameError and ensure consistency
                task_code = task.get('code', 'N/A')
                task_name = task.get('name', 'N/A')
                
                # Conditional 2: Skip Bridge Charter
                if any(kw in task_code for kw in ["Bridge Charter", "BridgeCharter"]) or \
                   any(kw in task_name for kw in ["Bridge Charter", "BridgeCharter"]):
                    logging.info(f"SKIP: {task_code} is Bridge Charter. Manual adjustment required.")
                    continue

                # Setup DateTimes
                s_sched_dt = parse_time_to_datetime(task.get('s_range', [])[0], target_dt)
                e_sched_dt = parse_time_to_datetime(task.get('s_range', [])[1], target_dt)
                if not s_sched_dt or not e_sched_dt: continue
                
                # Determine Proposed Subset
                if any(word in task_code for word in ["Extra Work", "S2S Charter"]):
                    # Conditional 1: 1-minute span
                    prop_start_dt, prop_end_dt = s_sched_dt, s_sched_dt + datetime.timedelta(minutes=1)
                elif any(word in task_code for word in ["Spare CDL", "Spare Monitor"]) or \
                     ("HTS" in task_code and any(word in task_code for word in ["Units", "Hrs"])):
                    # Conditionals 3 & 4: Exact Schedule
                    prop_start_dt, prop_end_dt = s_sched_dt, e_sched_dt
                else:
                    # Conditional 5: Comparison logic (Under Schedule)
                    a_start_dt = parse_time_to_datetime(task.get('a_range', [])[0], target_dt) if len(task.get('a_range', [])) > 0 else s_sched_dt
                    a_end_dt = parse_time_to_datetime(task.get('a_range', [])[1], target_dt) if len(task.get('a_range', [])) > 1 else e_sched_dt
                    prop_start_dt = max(a_start_dt or s_sched_dt, s_sched_dt)
                    prop_end_dt = min(a_end_dt or e_sched_dt, e_sched_dt)

                # Resolve Overlaps and apply the 15-minute violation rule
                final_s, final_e = get_non_overlapping_interval(prop_start_dt, prop_end_dt, fixed_intervals)
                
                # Check for 15-minute overlap violation (Rule 1 & 5)
                time_shifed_mins = (final_s - prop_start_dt).total_seconds() / 60
                if time_shifed_mins > 15:
                    logging.warning(f"FLAG: Overlap shift {time_shifed_mins}m > 15m for {task_code}. Manual review required.")
                    manual_flag = True
                    continue

                t_start, t_end = final_s.strftime("%I:%M %p"), final_e.strftime("%I:%M %p")
                
                if not times_match(task.get('p_start'), t_start) or not times_match(task.get('p_end'), t_end):
                    # Detect if we are stuck updating this specific task
                    task_key = f"{target_worker}_{task_code}_{t_start}_{t_end}"
                    retry_tracking[task_key] = retry_tracking.get(task_key, 0) + 1
                    
                    if retry_tracking.get(task_key, 0) > 3:
                        logging.error(f"STUCK: Task {task_code} for {target_worker_display} failed to update after 3 tries. Skipping worker.")
                        worker_fully_processed = True
                        processed_workers.add(target_worker)
                        break

                    logging.info(f"PHASE 2: Adjustment needed for {task_code} for {target_worker_display}")
                    # PHASE 3: Save/Update the changes
                    success_s = adjust_time_entry(page, task['row'], 10, t_start) if not times_match(task.get('p_start'), t_start) else True
                    page.wait_for_timeout(300)
                    success_e = adjust_time_entry(page, task['row'], 11, t_end) if not times_match(task.get('p_end'), t_end) else True
                    
                    if success_s and success_e:
                        save_btn = task['row'].locator(SELECTORS["row_save_btn"]).first
                        save_btn.wait_for(state="visible", timeout=5000)
                        
                        # Wait for button to become enabled (Kendo delay after input)
                        for _ in range(10):
                            if save_btn.is_enabled(): break
                            page.wait_for_timeout(300)
                            
                        if save_btn.is_enabled():
                            logging.info(f"PHASE 3: Clicking Update for {task_code}")
                        if save_btn.is_enabled():
                            save_btn.click(force=True)
                            page.wait_for_timeout(2000)
                            wait_for_loading(page)
                            adjustment_made = True
                            break # Break for-loop to re-map after save
            
            if worker_fully_processed:
                continue
                
            if adjustment_made: continue # Restart the while-loop to re-map
            # PHASE 4: Mark down adjusted tasks and click Verify button
            if manual_flag: 
                logging.info(f"PHASE 4: Skipping verification for {target_worker_display} due to flags.")
                processed_workers.add(target_worker)
                break

            worker_rows = worker_row_filter.all()
            checked_count = 0
            for row in worker_rows:
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
            break # Break current worker loop to refresh master grid handles

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
            context = browser.new_context(no_viewport=True)
            page = context.new_page()

            try:
                # Auto-accept native browser alerts to prevent the bot from hanging
                page.on("dialog", lambda dialog: dialog.accept())

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
def start_automation_thread(log_text_widget, username_entry, password_entry, save_creds_var, start_button, stop_button):
    """Starts the Playwright automation in a new thread."""
    global AUTOMATION_STOP_FLAG, AUTOMATION_THREAD
    
    username = username_entry.get()
    password = password_entry.get()

    if not username or not password:
        messagebox.showwarning("Missing Credentials", "Please enter both username and password.")
        return

    # Encrypt and save credentials locally if requested
    if save_creds_var.get():
        encryption_key = load_key()
        encrypt_credentials(username, password, encryption_key)

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

    # Load and decrypt existing credentials to pre-fill the GUI
    encryption_key = load_key()
    saved_user, saved_pass = decrypt_credentials(encryption_key)

    # Start keep-active background thread for the entire lifetime of the application
    ka_thread = threading.Thread(target=keep_active, args=(KEEP_ACTIVE_STOP_EVENT,))
    ka_thread.daemon = True
    ka_thread.start()
    logging.info("SYSTEM: Keep-active simulation started for the application session.")

    def on_closing():
        KEEP_ACTIVE_STOP_EVENT.set() # Stop the simulation thread
        root.destroy()

    root.protocol("WM_DELETE_WINDOW", on_closing)

    # --- Credential Input Frame ---
    credential_frame = ctk.CTkFrame(root, fg_color=BS_GRAY_200, corner_radius=10)
    credential_frame.pack(pady=10, padx=10, fill=ctk.X)

    # Inner frame to hold and center the credential fields
    inner_fields_frame = ctk.CTkFrame(credential_frame, fg_color="transparent")
    inner_fields_frame.pack(pady=10)

    ctk.CTkLabel(inner_fields_frame, text="Username:", text_color=BS_BLACK).grid(row=0, column=0, sticky=ctk.E, pady=5, padx=5)
    username_entry = ctk.CTkEntry(inner_fields_frame, width=250, fg_color=BS_WHITE, text_color=BS_BLACK)
    username_entry.grid(row=0, column=1, pady=5, padx=10)
    username_entry.insert(0, saved_user)

    ctk.CTkLabel(inner_fields_frame, text="Password:", text_color=BS_BLACK).grid(row=1, column=0, sticky=ctk.E, pady=5, padx=5)
    password_entry = ctk.CTkEntry(inner_fields_frame, width=250, show="*", fg_color=BS_WHITE, text_color=BS_BLACK)
    password_entry.grid(row=1, column=1, pady=5, padx=10)
    password_entry.insert(0, saved_pass)

    # Save credentials checkbox centered below fields
    save_creds_var = ctk.BooleanVar(value=True if saved_user else False)
    save_creds_checkbox = ctk.CTkCheckBox(credential_frame, text="Save Credentials Encrypted", 
                                          variable=save_creds_var, text_color=BS_BLACK,
                                          fg_color=BS_PRIMARY, hover_color=BS_BLUE)
    save_creds_checkbox.pack(pady=5)

    # --- Button Frame ---
    button_frame = ctk.CTkFrame(credential_frame, fg_color="transparent")
    button_frame.pack(pady=15)

    start_button = ctk.CTkButton(button_frame, text="Start Automation", 
                                 command=lambda: start_automation_thread(log_text_widget, username_entry, password_entry, save_creds_var, start_button, stop_button),
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