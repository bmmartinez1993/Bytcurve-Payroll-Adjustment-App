# ByteCurve Payroll Adjustment Automation App

import datetime
from datetime import datetime as dt
import os
import random
import time
import pyautogui
import logging
import customtkinter as ctk
from tkinter import messagebox
import threading
from playwright.sync_api import sync_playwright, Page
from cryptography.fernet import Fernet

from automation_core_refactored import (
    # Time utilities
    parse_kendo_time,
    times_match,
    parse_time_to_datetime,
    datetime_to_time_str,
    parse_time_range_str,
    # Interval utilities
    get_non_overlapping_interval,
    # Task classification
    determine_task_policy,
    # UI interaction
    adjust_time_entry,
    verify_task_checkbox,
    wait_for_loading,
    # Constants
    MAX_RETRY_ATTEMPTS,
    MAX_TIME_SHIFT_MINUTES,
    COL_PAID_START,
    COL_PAID_END,
)

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
BS_BLUE    = "#0d6efd"
BS_RED     = "#dc3545"
BS_WHITE   = "#fff"
BS_BLACK   = "#000"
BS_GRAY_100 = "#f8f9fa"
BS_GRAY_200 = "#e9ecef"
BS_GRAY_800 = "#343a40"
BS_GRAY_900 = "#212529"
BS_PRIMARY  = "#0d6efd"

# --- CONFIGURATION ---
BYTECURVE_URL    = "https://app.bytecurve360.com/portal/core/#/login"
KEY_FILE         = "secret.key"
CREDENTIAL_FILE  = "credentials.enc"

SELECTORS = {
    "login_username":          "USER NAME",
    "login_password":          "PASSWORD",
    "login_submit":            "Sign-In",
    "cookie_accept":           "a.cc-allow",
    "nav_payroll_section":     "PAYROLL",
    "nav_timesheets":          "Verify Hours",
    "payload_task_grid":       "[data-testid='payload-task-grid']",
    "row_date":                "td[aria-colindex='1']",
    "row_worker_name":         "td[aria-colindex='2']",
    "row_task_name":           "td[aria-colindex='3']",
    "row_task_code":           "td[aria-colindex='5']",
    "row_sched_range":         "td[aria-colindex='6']",
    "row_actual_range":        "td[aria-colindex='8']",
    "row_paid_start":          "td[aria-colindex='10']",
    "row_paid_end":            "td[aria-colindex='11']",
    "row_checkbox":            "input[kendocheckbox][aria-label='verify']",
    "row_save_btn":            "button.k-grid-save-command, button[title='Update'], button[kendogridsavecommand]",
    "date_filter_btns_container": "[data-testid='date-filter-btns-container']",
    "checkbox_verified":       "#checkboxInclude3",
    "checkbox_auto_verified":  "#checkboxInclude4",
    "checkbox_pending_review": "#checkboxInclude5",
    "btn_verify":              "button.k-button.k-primary",
    "btn_dialog_ok":           "button[data-testid='bulk-update-ok-btn']",
    "weekly_view_btn":         "weekly-view-btn",
    "detailed_view_btn":       "detailed-view-btn",
    "filter_submit_btn":       "filter-submit-btn",
}

# --- GLOBAL STATE ---
USERNAME              = ""
PASSWORD              = ""
AUTOMATION_STOP_FLAG  = False
AUTOMATION_THREAD     = None
KEEP_ACTIVE_STOP_EVENT = threading.Event()


# ===========================================================================
# Credential helpers
# ===========================================================================

def generate_key() -> bytes:
    key = Fernet.generate_key()
    with open(KEY_FILE, "wb") as f:
        f.write(key)
    return key


def load_key() -> bytes:
    if not os.path.exists(KEY_FILE):
        return generate_key()
    with open(KEY_FILE, "rb") as f:
        return f.read()


def encrypt_credentials(username: str, password: str, key: bytes) -> None:
    token = Fernet(key).encrypt(f"{username}:{password}".encode())
    with open(CREDENTIAL_FILE, "wb") as f:
        f.write(token)


def decrypt_credentials(key: bytes):
    if not os.path.exists(CREDENTIAL_FILE):
        return "", ""
    try:
        data = Fernet(key).decrypt(open(CREDENTIAL_FILE, "rb").read()).decode()
        username, password = data.split(":", 1)
        return username, password
    except Exception as e:
        logging.warning(f"Could not decrypt credentials: {e}")
        return "", ""


# ===========================================================================
# Keep-active thread
# ===========================================================================

def keep_active(stop_event: threading.Event, interval: int = 10) -> None:
    """Prevents the OS from going idle via periodic mouse/keyboard activity."""
    pyautogui.FAILSAFE = False
    while not stop_event.is_set():
        try:
            dist = random.randint(10, 30)
            pyautogui.moveRel(dist, 0, duration=0.2)
            pyautogui.moveRel(-dist, 0, duration=0.2)
            pyautogui.scroll(random.choice([-1, 1]))
            pyautogui.press('f15')
            time.sleep(interval + random.uniform(-2, 2))
        except Exception as e:
            logging.error(f"keep_active error: {e}")
            time.sleep(interval)


# ===========================================================================
# Tkinter log handler
# ===========================================================================

class TkinterLogHandler(logging.Handler):
    """Forwards log records to a CTkTextbox widget via a thread-safe queue."""

    def __init__(self, text_widget):
        super().__init__()
        self.text_widget = text_widget
        self._queue: list[str] = []
        self.text_widget.after(100, self._flush)

    def emit(self, record: logging.LogRecord) -> None:
        self._queue.append(self.format(record))

    def _flush(self) -> None:
        while self._queue:
            self.text_widget.insert(ctk.END, self._queue.pop(0) + "\n")
            self.text_widget.see(ctk.END)
        self.text_widget.after(100, self._flush)


# ===========================================================================
# Date utilities
# ===========================================================================

def get_previous_business_day() -> str:
    today = datetime.date.today()
    delta = 3 if today.weekday() == 0 else (2 if today.weekday() == 6 else 1)
    return (today - datetime.timedelta(days=delta)).strftime("%Y-%m-%d")


# ===========================================================================
# Browser automation helpers
# ===========================================================================

def login(page: Page) -> None:
    logging.info(f"Navigating to {BYTECURVE_URL}...")
    page.goto(BYTECURVE_URL)
    page.wait_for_load_state("networkidle")

    try:
        btn = page.locator(SELECTORS["cookie_accept"]).first
        btn.scroll_into_view_if_needed()
        btn.click(timeout=5000, force=True)
        logging.info("Cookie consent accepted.")
    except Exception:
        pass

    page.get_by_role("textbox", name=SELECTORS["login_username"]).fill(USERNAME)
    page.get_by_role("textbox", name=SELECTORS["login_password"]).fill(PASSWORD)
    page.get_by_role("button",  name=SELECTORS["login_submit"]).click()
    page.wait_for_load_state("networkidle")
    logging.info("Logged in successfully.")


def navigate_to_payroll(page: Page) -> None:
    logging.info("Navigating to Timesheets...")
    wait_for_loading(page)
    page.get_by_role("link", name=SELECTORS["nav_payroll_section"]).click()
    wait_for_loading(page)
    link = page.get_by_role("link", name=SELECTORS["nav_timesheets"])
    link.wait_for(state="visible", timeout=10000)
    link.click()
    wait_for_loading(page)


def click_verify_button(page: Page, worker_name: str) -> bool:
    """Clicks the Verify button and confirms the bulk-update dialog."""
    try:
        wait_for_loading(page)
        btn = page.locator(SELECTORS["btn_verify"]).filter(has_text="Verify").last
        btn.wait_for(state="visible", timeout=10000)
        btn.scroll_into_view_if_needed()
        page.wait_for_timeout(300)

        if not btn.is_enabled():
            logging.warning(f"VERIFY_BTN: Disabled for {worker_name}.")
            return False

        logging.info(f"VERIFY_BTN: Clicking Verify for {worker_name}")
        btn.click(force=True, timeout=5000)
        page.wait_for_timeout(800)

        page.wait_for_selector("div[role='dialog'][aria-modal='true']", state="visible", timeout=15000)
        page.wait_for_timeout(500)

        ok_btn = page.locator(SELECTORS["btn_dialog_ok"])
        ok_btn.wait_for(state="visible", timeout=10000)
        ok_btn.scroll_into_view_if_needed()
        page.wait_for_timeout(500)

        if ok_btn.count() == 0:
            logging.error(f"VERIFY_BTN: Dialog OK not found for {worker_name}")
            return False

        ok_btn.click(force=True, timeout=5000, delay=100)
        page.wait_for_timeout(3000)
        wait_for_loading(page)
        page.wait_for_timeout(1500)
        logging.info(f"VERIFY_BTN: Verification complete for {worker_name}")
        return True

    except Exception as e:
        logging.error(f"VERIFY_BTN: Failed for {worker_name}: {e}")
        return False


# ===========================================================================
# validate_and_process_rows — split into focused helpers
# ===========================================================================

def _setup_view_and_filters(page: Page, target_date_short: str) -> None:
    """Switches to detailed view, selects the date, checks filters, and sorts."""
    detailed_btn = page.get_by_test_id(SELECTORS["detailed_view_btn"])
    wait_for_loading(page)
    if detailed_btn.is_visible():
        detailed_btn.click()
    wait_for_loading(page)

    date_btn = (
        page.locator(SELECTORS["date_filter_btns_container"])
        .get_by_role("link", name=target_date_short, exact=True)
    )
    if date_btn.is_visible():
        date_btn.click()
        logging.info(f"Selected date: {target_date_short}")

    wait_for_loading(page)
    page.locator(SELECTORS["checkbox_verified"]).check()
    page.locator(SELECTORS["checkbox_auto_verified"]).check()
    page.locator(SELECTORS["checkbox_pending_review"]).check()
    page.get_by_test_id(SELECTORS["filter_submit_btn"]).click()
    page.wait_for_load_state("networkidle")
    wait_for_loading(page)

    try:
        page.wait_for_timeout(2000)
        sort_link = (
            page.locator(f"{SELECTORS['payload_task_grid']} th[aria-colindex='2'] span.k-link")
            .first
        )
        sort_link.wait_for(state="visible", timeout=10000)
        sort_link.scroll_into_view_if_needed()
        sort_link.click(force=True)
        logging.info("Sorted by Employee column.")
        wait_for_loading(page)
        page.wait_for_timeout(2000)
    except Exception as e:
        logging.error(f"Failed to sort by employee: {e}")


def _find_worker_block(page: Page, scroll_container, target_date_full: str,
                       processed_workers: set):
    """
    Scans the visible rows to locate the first complete, unprocessed worker block.

    Returns a 4-tuple (worker_id, worker_name, display_name, row_locators) when a
    complete block is found, or None when all workers are processed / grid is exhausted.
    """
    grid_rows_sel = f"{SELECTORS['payload_task_grid']} tbody tr.k-master-row"

    while not AUTOMATION_STOP_FLAG:
        wait_for_loading(page)
        try:
            page.locator(grid_rows_sel).first.wait_for(state="visible", timeout=10000)
        except Exception:
            logging.info("No task rows visible.")
            return None

        scroll_top_before = scroll_container.evaluate("el => el.scrollTop")
        task_rows = page.locator(grid_rows_sel).all()
        is_at_bottom = scroll_container.evaluate(
            "el => el.scrollTop + el.clientHeight >= el.scrollHeight - 20"
        )

        target_worker = target_name = target_display = ""
        worker_rows = []
        block_complete = False

        for i, row in enumerate(task_rows):
            row_date = (row.locator(SELECTORS["row_date"]).text_content(timeout=2000) or "").strip()
            if row_date != target_date_full:
                continue

            name_span = row.locator(SELECTORS["row_worker_name"]).locator("span[title]").first
            if name_span.count() == 0:
                continue

            emp_id   = (name_span.get_attribute("title", timeout=2000) or "").strip()
            emp_name = (name_span.text_content(timeout=2000) or "").strip()
            worker_id = emp_id or emp_name
            if not worker_id or worker_id in processed_workers:
                continue

            if not target_worker:
                target_worker  = worker_id
                target_name    = emp_name
                target_display = f"{emp_name} ({emp_id})" if emp_id else emp_name

            if worker_id == target_worker:
                worker_rows.append(row)
                if i < len(task_rows) - 1:
                    next_span = task_rows[i + 1].locator(SELECTORS["row_worker_name"]).locator("span[title]").first
                    if next_span.count() > 0:
                        next_id = (next_span.get_attribute("title") or next_span.text_content() or "").strip()
                        if next_id != target_worker:
                            block_complete = True
                elif is_at_bottom:
                    block_complete = True

        if not target_worker:
            scroll_container.evaluate("el => el.scrollTop += 800")
            page.wait_for_timeout(1000)
            if scroll_container.evaluate("el => el.scrollTop") == scroll_top_before:
                logging.info("All workers processed.")
                return None
            continue

        if not block_complete:
            scroll_container.evaluate("el => el.scrollTop += 300")
            page.wait_for_timeout(400)
            continue

        return target_worker, target_name, target_display, worker_rows

    return None


def _read_task_row(row) -> dict:
    """Reads all relevant fields from a grid row into a plain dict."""
    s_text = row.locator(SELECTORS["row_sched_range"]).text_content() or ""
    a_text = row.locator(SELECTORS["row_actual_range"]).text_content() or ""
    s_range = parse_time_range_str(s_text)
    a_range = parse_time_range_str(a_text)

    return {
        "row":      row,
        "verified": row.locator(SELECTORS["row_checkbox"]).is_checked(),
        "code":     (row.locator(SELECTORS["row_task_code"]).text_content() or "").strip(),
        "name":     (row.locator(SELECTORS["row_task_name"]).text_content() or "").strip(),
        "p_start":  parse_kendo_time(row.locator(SELECTORS["row_paid_start"]).text_content() or ""),
        "p_end":    parse_kendo_time(row.locator(SELECTORS["row_paid_end"]).text_content() or ""),
        "s_range":  s_range,
        "a_range":  a_range,
    }


def _scroll_worker_into_view(page: Page, scroll_container, worker_name: str) -> bool:
    """
    Brings the target worker's rows into the Kendo virtual grid's DOM render window.

    Kendo virtual grids remove off-screen rows from the DOM, making Playwright
    locator operations time out.  This function uses JavaScript (which never
    waits for DOM elements) to find any rendered row that belongs to the worker,
    then scrolls the container so those rows are centred in the viewport.

    If the rows are not currently rendered, the function scrolls through the
    grid in both directions until it finds them.
    """
    grid_sel = SELECTORS["payload_task_grid"]

    # JavaScript that looks through CURRENTLY RENDERED rows for this worker.
    # Returns the row's offsetTop if found, null otherwise.
    find_js = """
        ([sel, name]) => {
            var content = document.querySelector(sel + ' div.k-grid-content');
            if (!content) return null;
            var rows = content.querySelectorAll('tbody tr.k-master-row');
            for (var row of rows) {
                var span = row.querySelector('td[aria-colindex="2"] span[title]');
                if (span && span.textContent.trim() === name) {
                    return row.offsetTop;
                }
            }
            return null;
        }
    """

    def _scroll_to_top_found():
        top = page.evaluate(find_js, [grid_sel, worker_name])
        if top is not None:
            scroll_container.evaluate(f"el => el.scrollTop = Math.max(0, {top} - 80)")
            page.wait_for_timeout(250)
            return True
        return False

    # Check current scroll position first (fastest path).
    if _scroll_to_top_found():
        return True

    # Not visible — scan through the grid (down first, then up).
    for step_px in [300, -300]:
        for _ in range(25):
            scroll_container.evaluate(f"el => el.scrollTop += {step_px}")
            page.wait_for_timeout(150)
            if _scroll_to_top_found():
                return True

    logging.warning(f"SCROLL: Could not locate rows for '{worker_name}' in the grid.")
    return False


def _calculate_proposed_times(task: dict, target_dt: dt):
    """Returns (proposed_start_dt, proposed_end_dt) or (None, None) for skipped tasks."""
    s_range = task["s_range"]
    if len(s_range) < 2:
        return None, None

    s_start_dt = parse_time_to_datetime(s_range[0], target_dt)
    s_end_dt   = parse_time_to_datetime(s_range[1], target_dt)
    if not s_start_dt or not s_end_dt:
        return None, None

    a_range = task["a_range"]
    policy  = determine_task_policy(task["code"], task["name"])
    if policy is None:
        return None, None

    if policy.is_one_minute_only:
        return s_start_dt, s_start_dt + datetime.timedelta(minutes=1)

    if policy.require_schedule_match:
        return s_start_dt, s_end_dt

    # Regular task: clamp actual times to the schedule window
    a_start = parse_time_to_datetime(a_range[0], target_dt) if len(a_range) > 0 else s_start_dt
    a_end   = parse_time_to_datetime(a_range[1], target_dt) if len(a_range) > 1 else s_end_dt
    prop_start = max(a_start or s_start_dt, s_start_dt)
    prop_end   = min(a_end   or s_end_dt,   s_end_dt)
    return prop_start, prop_end


def _click_update_button(page: Page, row, task_code: str) -> bool:
    """Clicks the row's Update/Save button and waits for the grid to reload."""
    try:
        save_btn = row.locator(SELECTORS["row_save_btn"]).first
        save_btn.wait_for(state="visible", timeout=5000)

        for _ in range(MAX_RETRY_ATTEMPTS * 10):
            if save_btn.is_enabled():
                break
            page.wait_for_timeout(100)

        if not save_btn.is_enabled():
            logging.warning(f"SAVE: Update button never enabled for {task_code}")
            return False

        logging.info(f"SAVE: Clicking Update for {task_code}. Grid will reload.")
        save_btn.click(force=True)
        wait_for_loading(page)
        return True
    except Exception as e:
        logging.error(f"SAVE: Update click failed for {task_code}: {e}")
        return False


def _adjust_worker_tasks(page: Page, worker_filter, worker_display: str,
                         worker_id: str, target_dt: dt,
                         scroll_container=None, worker_name: str = "") -> bool:
    """
    Inner adjustment loop for one worker.

    Reads all tasks fresh on each iteration (the grid reloads after every
    Update click). Adjusts exactly one task per pass, then restarts.

    Returns True when there are no more adjustments to make, False if the
    worker must be skipped due to repeated failures.
    """
    retry_tracking: dict[str, int] = {}
    manual_flag = False

    while not AUTOMATION_STOP_FLAG:
        # Bring worker rows into the Kendo virtual grid's render window BEFORE
        # calling .all() — otherwise virtualized (off-screen) rows are absent
        # from the DOM and every subsequent locator operation times out (30 s).
        if scroll_container is not None and worker_name:
            _scroll_worker_into_view(page, scroll_container, worker_name)
            page.wait_for_timeout(400)

        wait_for_loading(page)
        worker_rows = worker_filter.all()
        if not worker_rows:
            logging.warning(f"STALE: Rows gone for {worker_display}. Skipping.")
            return False

        # Build task list and collect fixed (verified) intervals
        tasks = [_read_task_row(r) for r in worker_rows]
        fixed_intervals = [
            (parse_time_to_datetime(t["p_start"], target_dt),
             parse_time_to_datetime(t["p_end"],   target_dt))
            for t in tasks
            if t["verified"]
        ]

        adjustment_made = False

        for task in tasks:
            if task["verified"]:
                continue
            if determine_task_policy(task["code"], task["name"]) is None:
                continue  # Bridge Charter or similar — skip

            prop_s, prop_e = _calculate_proposed_times(task, target_dt)
            if not prop_s:
                continue

            # Resolve overlaps against already-fixed intervals
            final_s, final_e = get_non_overlapping_interval(prop_s, prop_e, fixed_intervals)

            shift_mins = (final_s - prop_s).total_seconds() / 60
            if shift_mins > MAX_TIME_SHIFT_MINUTES:
                logging.warning(
                    f"SKIP: {task['code']} shifted {shift_mins:.0f} min — manual review needed."
                )
                manual_flag = True
                continue

            t_start = datetime_to_time_str(final_s)
            t_end   = datetime_to_time_str(final_e)

            if times_match(task["p_start"], t_start) and times_match(task["p_end"], t_end):
                continue  # Already correct

            task_key = f"{worker_id}_{task['code']}_{t_start}_{t_end}"
            retry_tracking[task_key] = retry_tracking.get(task_key, 0) + 1

            if retry_tracking[task_key] > MAX_RETRY_ATTEMPTS:
                logging.error(f"STUCK: {task['code']} failed {MAX_RETRY_ATTEMPTS} times. Skipping worker.")
                return False

            logging.info(f"ADJUST: {task['code']} for {worker_display} → {t_start} – {t_end}")

            ok_s = adjust_time_entry(page, task["row"], COL_PAID_START, t_start) \
                   if not times_match(task["p_start"], t_start) else True
            ok_e = adjust_time_entry(page, task["row"], COL_PAID_END,   t_end)   \
                   if not times_match(task["p_end"],   t_end)   else True

            if ok_s and ok_e:
                if _click_update_button(page, task["row"], task["code"]):
                    retry_tracking.pop(task_key, None)
                    adjustment_made = True
                    break  # Grid reset — restart loop
            else:
                logging.warning(f"ADJUST_FAIL: {task['code']} for {worker_display}")

        if adjustment_made:
            continue  # Restart to pick up refreshed grid

        # No more adjustments needed (or all remaining tasks are manual-flagged)
        if manual_flag:
            logging.info(f"MANUAL_FLAG: Skipping verification for {worker_display}")
            return False

        return True  # All clear — ready for verification

    return False  # Stopped


def _verify_worker_tasks(page: Page, worker_filter, worker_display: str,
                         scroll_container=None, worker_name: str = "") -> None:
    """Checks all unchecked task checkboxes then clicks the Verify button."""
    if scroll_container is not None and worker_name:
        _scroll_worker_into_view(page, scroll_container, worker_name)
        page.wait_for_timeout(400)

    worker_rows = worker_filter.all()
    checked_count = sum(
        1 for row in worker_rows
        if not row.locator(SELECTORS["row_checkbox"]).is_checked()
        and verify_task_checkbox(page, row, "N/A", worker_display)
    )

    if checked_count > 0:
        logging.info(f"VERIFY: {checked_count} task(s) checked for {worker_display}. Clicking Verify.")
        click_verify_button(page, worker_display)
    else:
        logging.info(f"VERIFY: No new checkboxes needed for {worker_display}.")


def validate_and_process_rows(page: Page, target_date: str) -> None:
    """
    Main orchestration loop.

    For each employee in the grid (scrolling through virtualized rows):
      1. Adjusts paid-time cells until all tasks match policy.
      2. Verifies (checks checkboxes + clicks Verify) once adjustments are done.
    """
    logging.info(f"Processing date: {target_date}")
    target_dt         = dt.strptime(target_date, "%Y-%m-%d")
    target_date_short = target_dt.strftime("%m/%d")
    target_date_full  = target_dt.strftime("%m/%d/%Y")
    scroll_container  = page.locator(f"{SELECTORS['payload_task_grid']} div.k-grid-content")

    _setup_view_and_filters(page, target_date_short)

    processed_workers: set[str] = set()

    while not AUTOMATION_STOP_FLAG:
        block = _find_worker_block(page, scroll_container, target_date_full, processed_workers)
        if block is None:
            break

        worker_id, worker_name, worker_display, _ = block

        # Build a live filter so row locators stay fresh across grid reloads
        worker_filter = (
            page.locator(f"{SELECTORS['payload_task_grid']} tbody tr.k-master-row")
            .filter(has=page.locator("td[aria-colindex='2'] span").get_by_text(worker_name, exact=True))
        )

        logging.info(f"WORKER: Processing {worker_display}")

        adjustments_ok = _adjust_worker_tasks(
            page, worker_filter, worker_display, worker_id, target_dt,
            scroll_container=scroll_container, worker_name=worker_name,
        )

        if adjustments_ok:
            _verify_worker_tasks(
                page, worker_filter, worker_display,
                scroll_container=scroll_container, worker_name=worker_name,
            )
            logging.info(f"WORKER: Done with {worker_display}")
        else:
            logging.warning(f"WORKER: Skipping verification for {worker_display} (manual review needed)")

        processed_workers.add(worker_id)


# ===========================================================================
# Thread management
# ===========================================================================

def run_playwright_automation(log_text_widget, username: str, password: str,
                              start_button, stop_button) -> None:
    global USERNAME, PASSWORD, AUTOMATION_STOP_FLAG
    USERNAME = username
    PASSWORD = password

    for handler in logging.root.handlers[:]:
        if isinstance(handler, (logging.StreamHandler, TkinterLogHandler)):
            logging.root.removeHandler(handler)
    logging.root.addHandler(TkinterLogHandler(log_text_widget))

    logging.info("=" * 60)
    logging.info(f"AUTOMATION RUN STARTED: {dt.now().strftime('%Y-%m-%d %H:%M:%S')}")
    logging.info("=" * 60)

    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=False, channel="chrome", args=["--start-maximized"])
            context = browser.new_context(no_viewport=True)
            page    = context.new_page()
            try:
                page.on("dialog", lambda d: d.accept())

                if not USERNAME or not PASSWORD:
                    logging.critical("Credentials not set. Aborting.")
                    return

                target_date = get_previous_business_day()
                login(page)
                navigate_to_payroll(page)

                if AUTOMATION_STOP_FLAG:
                    logging.warning("STOP: Halted before processing.")
                else:
                    validate_and_process_rows(page, target_date)
                    if AUTOMATION_STOP_FLAG:
                        logging.warning("STOP: Automation stopped by user.")
                    else:
                        logging.info("COMPLETE: Automation finished successfully.")
            except Exception as e:
                logging.critical(f"Automation error: {e}")
            finally:
                browser.close()
    except Exception as e:
        logging.critical(f"Browser launch failed: {e}")
    finally:
        start_button.configure(state="normal")
        stop_button.configure(state="disabled")
        AUTOMATION_STOP_FLAG = False
        logging.info("UI: Controls re-enabled.")


def start_automation_thread(log_text_widget, username_entry, password_entry,
                            save_creds_var, start_button, stop_button) -> None:
    global AUTOMATION_STOP_FLAG, AUTOMATION_THREAD

    username = username_entry.get()
    password = password_entry.get()
    if not username or not password:
        messagebox.showwarning("Missing Credentials", "Please enter both username and password.")
        return

    if save_creds_var.get():
        encrypt_credentials(username, password, load_key())

    username_entry.configure(state="disabled")
    password_entry.configure(state="disabled")
    start_button.configure(state="disabled")
    stop_button.configure(state="normal")
    log_text_widget.delete(1.0, ctk.END)

    AUTOMATION_STOP_FLAG = False
    t = threading.Thread(
        target=run_playwright_automation,
        args=(log_text_widget, username, password, start_button, stop_button),
        daemon=True,
    )
    AUTOMATION_THREAD = t
    t.start()


def stop_automation() -> None:
    global AUTOMATION_STOP_FLAG
    AUTOMATION_STOP_FLAG = True
    logging.info("STOP: Stop signal sent.")
    messagebox.showinfo("Automation Stopped", "Stop signal sent. Automation will halt after the current operation.")


# ===========================================================================
# GUI
# ===========================================================================

def start_gui_and_automation() -> None:
    ctk.set_appearance_mode("System")
    ctk.set_default_color_theme("blue")

    root = ctk.CTk()
    root.title("ByteCurve Payroll Adjustment Automation")
    root.geometry("800x650")
    root.configure(fg_color=BS_GRAY_100)

    encryption_key       = load_key()
    saved_user, saved_pw = decrypt_credentials(encryption_key)

    ka_thread = threading.Thread(target=keep_active, args=(KEEP_ACTIVE_STOP_EVENT,), daemon=True)
    ka_thread.start()
    logging.info("SYSTEM: Keep-active thread started.")

    def on_closing():
        KEEP_ACTIVE_STOP_EVENT.set()
        root.destroy()

    root.protocol("WM_DELETE_WINDOW", on_closing)

    # --- Credential frame ---
    cred_frame = ctk.CTkFrame(root, fg_color=BS_GRAY_200, corner_radius=10)
    cred_frame.pack(pady=10, padx=10, fill=ctk.X)

    fields_frame = ctk.CTkFrame(cred_frame, fg_color="transparent")
    fields_frame.pack(pady=10)

    ctk.CTkLabel(fields_frame, text="Username:", text_color=BS_BLACK).grid(
        row=0, column=0, sticky=ctk.E, pady=5, padx=5)
    username_entry = ctk.CTkEntry(fields_frame, width=250, fg_color=BS_WHITE, text_color=BS_BLACK)
    username_entry.grid(row=0, column=1, pady=5, padx=10)
    username_entry.insert(0, saved_user)

    ctk.CTkLabel(fields_frame, text="Password:", text_color=BS_BLACK).grid(
        row=1, column=0, sticky=ctk.E, pady=5, padx=5)
    password_entry = ctk.CTkEntry(fields_frame, width=250, show="*", fg_color=BS_WHITE, text_color=BS_BLACK)
    password_entry.grid(row=1, column=1, pady=5, padx=10)
    password_entry.insert(0, saved_pw)

    save_creds_var = ctk.BooleanVar(value=bool(saved_user))
    ctk.CTkCheckBox(
        cred_frame, text="Save Credentials Encrypted",
        variable=save_creds_var, text_color=BS_BLACK,
        fg_color=BS_PRIMARY, hover_color=BS_BLUE,
    ).pack(pady=5)

    # --- Button row ---
    btn_frame = ctk.CTkFrame(cred_frame, fg_color="transparent")
    btn_frame.pack(pady=15)

    start_button = ctk.CTkButton(
        btn_frame, text="Start Automation",
        fg_color=BS_PRIMARY, text_color=BS_WHITE, hover_color=BS_BLUE,
        command=lambda: start_automation_thread(
            log_text_widget, username_entry, password_entry,
            save_creds_var, start_button, stop_button
        ),
    )
    start_button.pack(side=ctk.LEFT, padx=5)

    stop_button = ctk.CTkButton(
        btn_frame, text="Stop Automation",
        fg_color=BS_RED, text_color=BS_WHITE, hover_color="#c82333",
        state="disabled", command=stop_automation,
    )
    stop_button.pack(side=ctk.LEFT, padx=5)

    # --- Log frame ---
    log_frame = ctk.CTkFrame(root, fg_color=BS_GRAY_100, corner_radius=10)
    log_frame.pack(pady=10, padx=10, fill=ctk.BOTH, expand=True)

    ctk.CTkLabel(log_frame, text="Automation Activity Log", text_color=BS_GRAY_900).pack(pady=5)
    log_text_widget = ctk.CTkTextbox(
        log_frame, width=780, height=300,
        fg_color=BS_GRAY_800, text_color=BS_WHITE,
    )
    log_text_widget.pack(fill=ctk.BOTH, expand=True, padx=10, pady=5)

    root.mainloop()


if __name__ == "__main__":
    start_gui_and_automation()
