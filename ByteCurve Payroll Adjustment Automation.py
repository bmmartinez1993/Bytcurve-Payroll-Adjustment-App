# ByteCurve Payroll Adjustment Automation App

import datetime
from datetime import datetime as dt
import os
import random
import re
import socket
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
    intervals_overlap,
    # Task classification
    determine_task_policy,
    # UI interaction
    adjust_time_entry,
    verify_task_checkbox,
    wait_for_loading,
    is_page_loading,
    # Constants
    MAX_RETRY_ATTEMPTS,
    MAX_TIME_SHIFT_MINUTES,
    COL_PAID_START,
    COL_PAID_END,
)
from employee_scorer import (
    load_history,
    save_history,
    sort_employees_by_priority,
    record_outcome,
)
from log_digest import generate_digest

# --- LOGGING CONFIGURATION ---

class _LiveFileHandler(logging.FileHandler):
    """FileHandler that flushes to disk after every record for live log tailing."""
    def emit(self, record):
        super().emit(record)
        self.flush()

_log_formatter = logging.Formatter(
    fmt='%(asctime)s [%(levelname)s] %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S',
)

os.makedirs("logs", exist_ok=True)
_file_handler = _LiveFileHandler(
    os.path.join("logs", "automation_activity.log"),
    mode='w',           # overwrite each run — keeps the file to the current session only
    encoding='utf-8',
)
_file_handler.setFormatter(_log_formatter)

_stream_handler = logging.StreamHandler()
_stream_handler.setFormatter(_log_formatter)

_root_logger = logging.getLogger()
_root_logger.setLevel(logging.INFO)
_root_logger.handlers.clear()          # remove any handlers set by imported modules
_root_logger.addHandler(_file_handler)
_root_logger.addHandler(_stream_handler)

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
    "date_filter_btns_container": "[data-testid='date-filter-btns-container']",
    "date_calendar_btn":          "kendo-datepicker button.k-input-button",
    "date_calendar":              "kendo-calendar",
    "date_cal_nav_prev":          "kendo-calendar button.k-calendar-nav-prev",
    "date_cal_nav_next":          "kendo-calendar button.k-calendar-nav-next",
    "date_cal_title":             "kendo-calendar button.k-calendar-nav-fast",
    "date_input":                 "input.k-input-inner[role='combobox']",
    "checkbox_incomplete":     "#checkboxInclude2",
    "checkbox_verified":       "#checkboxInclude3",
    "checkbox_auto_verified":  "#checkboxInclude4",
    "checkbox_pending_review": "#checkboxInclude5",
    "btn_verify":              "button.k-button.k-primary",
    "btn_dialog_ok":           "button[data-testid='bulk-update-ok-btn']",
    "btn_confirm_changes_yes": "button[data-testid='confirm-changes-yes-btn']",
    "btn_confirm_changes_no":  "button[data-testid='confirm-changes-no-btn']",
    "btn_conflict_ok":         "button[data-testid='verification-conflict-ok-btn']",
    "btn_dialog_close":        "button.k-dialog-close, button[aria-label='Close']",
    "weekly_view_btn":         "weekly-view-btn",      # present on the daily view (toggles to weekly)
    "auto_verify_btn":         "auto-verify-btn",      # present on the daily view (bulk auto-verify)
    "filter_submit_btn":       "filter-submit-btn",
    "emp_filter":              "[data-testid='emp-input']",
    "emp_filter_popup_items":  "kendo-popup li.k-list-item",
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

        # Primary: the test-id the dialog ships with. If that is absent, fall back to
        # the generic Kendo confirm-dialog primary button (proven in diagnose_dialog.py),
        # scoped to the open dialog and matched on its action text.
        ok_btn = page.locator(SELECTORS["btn_dialog_ok"])
        if ok_btn.count() == 0:
            ok_btn = page.locator(
                "div[role='dialog'] button.k-button-solid-primary, "
                "div[role='dialog'] button.k-primary"
            ).filter(has_text=re.compile(r"ok|yes|update", re.I))

        try:
            ok_btn.first.wait_for(state="visible", timeout=10000)
        except Exception:
            logging.error(f"VERIFY_BTN: Dialog OK not found for {worker_name}")
            return False

        ok_btn.first.scroll_into_view_if_needed()
        page.wait_for_timeout(500)
        ok_btn.first.click(force=True, timeout=5000, delay=100)
        page.wait_for_timeout(3000)
        wait_for_loading(page)
        page.wait_for_timeout(1500)

        # After the grid reloads, the platform may show an "Employee Conflict"
        # dialog if a verified task overlaps with another worker's already-verified
        # schedule.  This dialog does NOT have data-testid='confirm-changes-dialog'
        # so _handle_confirm_changes_dialog would miss it without this check.
        conflict_btn = page.locator(SELECTORS["btn_conflict_ok"])
        if conflict_btn.count() > 0 and conflict_btn.first.is_visible():
            logging.info(
                f"VERIFY_BTN: 'Employee Conflict' dialog detected after verification "
                f"for {worker_name}. Clicking Ok."
            )
            try:
                conflict_btn.first.scroll_into_view_if_needed()
                conflict_btn.first.click(force=True)
                page.wait_for_selector(
                    "div[role='dialog'][aria-modal='true'].k-dialog",
                    state="hidden", timeout=6000
                )
                page.wait_for_timeout(800)
                wait_for_loading(page)
            except Exception as ce:
                logging.warning(
                    f"VERIFY_BTN: Could not dismiss conflict dialog for {worker_name}: {ce}"
                )

        # Dismiss any remaining dialog (e.g. 'Task Save Detail') that may have
        # appeared as server feedback. Its overlay would block the next worker.
        _handle_confirm_changes_dialog(page, worker_name, timeout=2000)

        logging.info(f"VERIFY_BTN: Verification complete for {worker_name}")
        return True

    except Exception as e:
        logging.error(f"VERIFY_BTN: Failed for {worker_name}: {e}")
        return False


# ===========================================================================
# validate_and_process_rows — split into focused helpers
# ===========================================================================

def _select_date_via_calendar(page: Page, target_dt: dt) -> bool:
    """
    Selects the business date by typing directly into the Kendo DatePicker
    input field in MM/DD/YYYY format and pressing Enter.

    Strategy:
      1. Click the input to focus it (may open the calendar popup).
      2. Press Escape to close any popup that opened, keeping input focus.
      3. Press Ctrl+A on the input to select the existing value.
      4. Type the date character-by-character (so Kendo fires input/change
         events on each keystroke) in MM/DD/YYYY format.
      5. Press Enter — Kendo commits the date and reloads the grid.

    Returns True on success, False on any error.
    """
    date_str = target_dt.strftime("%m/%d/%Y")   # "06/07/2026"

    def _dismiss_popup():
        try:
            page.keyboard.press("Escape")
            page.wait_for_timeout(200)
        except Exception:
            pass

    try:
        inp = page.locator(SELECTORS["date_input"]).first
        inp.wait_for(state="visible", timeout=5000)

        # Focus the input — clicking it may open the calendar popup.
        inp.click()
        page.wait_for_timeout(300)

        # Close any popup that just opened; the input remains focused.
        _dismiss_popup()

        # Select the entire existing value so the typed date replaces it.
        inp.press("Control+a")
        page.wait_for_timeout(100)

        # Type MM/DD/YYYY with a small per-keystroke delay so Kendo's
        # masked-input handler processes each character individually.
        inp.type(date_str, delay=50)
        page.wait_for_timeout(200)

        # Commit: Enter applies the date and triggers the grid reload.
        inp.press("Enter")
        page.wait_for_timeout(500)

        logging.info(f"DATE: Business date {date_str} entered via input field.")
        return True

    except Exception as e:
        logging.error(f"DATE: Failed to enter business date '{date_str}': {e}")
        _dismiss_popup()
        return False


def _setup_view_and_filters(page: Page, target_dt: dt) -> None:
    """Confirms the daily grid is loaded, selects the date, checks filters, and sorts."""
    wait_for_loading(page)

    # "Verify Hours" lands directly on the daily/detailed view, so no view toggle is
    # needed — the editable paid-time grid (payload-task-grid) is present on landing.
    # We simply confirm it has rendered before relying on its rows/columns.
    grid = page.locator(SELECTORS["payload_task_grid"])
    try:
        grid.first.wait_for(state="visible", timeout=15000)
    except Exception:
        logging.error("VIEW: Daily payroll grid not found — aborting filter setup.")
        return

    wait_for_loading(page)

    target_date_short = target_dt.strftime("%m/%d")   # e.g. "06/07"

    date_btn = (
        page.locator(SELECTORS["date_filter_btns_container"])
        .get_by_role("link", name=target_date_short, exact=True)
    )
    if date_btn.is_visible():
        date_btn.click()
        logging.info(f"Selected date: {target_date_short}")
    else:
        logging.info(
            f"DATE: {target_date_short} not in quick-access strip — "
            "opening calendar widget."
        )
        if not _select_date_via_calendar(page, target_dt):
            logging.error(
                f"DATE: Could not select {target_date_short} via calendar widget. "
                "Proceeding with whatever date is currently active."
            )

    wait_for_loading(page)
    page.locator(SELECTORS["checkbox_incomplete"]).check()
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
    Retained as a fallback / diagnostic tool.
    The main loop now uses _get_all_employees_from_dropdown + _filter_grid_by_employee
    instead, which eliminates virtual-scroll stale-DOM issues entirely.

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
    Brings the target worker's rows into the Kendo virtual grid's DOM render window
    and waits until they are confirmed present before returning.

    Kendo virtual grids remove off-screen rows from the DOM, so Playwright
    locator operations on those rows time out.  This function:

    1. Calls scrollIntoView() on any matching row already in the render window
       (correct for CSS-transform virtual scroll — offsetTop must NOT be used).
    2. After scrolling, polls with wait_for_function until the row is confirmed
       in the DOM (up to 3 s), so the caller never reads a stale empty list.
    3. If the row is not currently rendered, scans the grid in 400 px increments
       (down first, then up) until it finds and confirms the row.
    """
    grid_sel = SELECTORS["payload_task_grid"]

    # JS: find the row, call scrollIntoView so the container scrolls to it.
    scroll_js = """
        ([sel, name]) => {
            var content = document.querySelector(sel + ' div.k-grid-content');
            if (!content) return false;
            var rows = content.querySelectorAll('tbody tr.k-master-row');
            for (var row of rows) {
                var span = row.querySelector('td[aria-colindex="2"] span[title]');
                if (span && span.textContent.trim() === name) {
                    row.scrollIntoView({ block: 'center', inline: 'nearest' });
                    return true;
                }
            }
            return false;
        }
    """

    # JS: check whether the row is currently rendered — used for polling.
    check_js = """
        ([sel, name]) => {
            var content = document.querySelector(sel + ' div.k-grid-content');
            if (!content) return false;
            var rows = content.querySelectorAll('tbody tr.k-master-row');
            for (var row of rows) {
                var span = row.querySelector('td[aria-colindex="2"] span[title]');
                if (span && span.textContent.trim() === name) return true;
            }
            return false;
        }
    """

    def _scroll_and_confirm():
        """scrollIntoView the row, then poll until Kendo re-renders it."""
        if not page.evaluate(scroll_js, [grid_sel, worker_name]):
            return False
        # Kendo virtual grid re-renders rows after every scroll.  Poll until
        # the row is stable in the DOM rather than relying on a fixed timeout.
        try:
            page.wait_for_function(check_js, arg=[grid_sel, worker_name], timeout=3000)
            return True
        except Exception:
            return False

    # Fast path: row already in current render window (most iterations after a
    # simple grid reload land here and return immediately).
    if _scroll_and_confirm():
        return True

    # Full-grid scan.
    #
    # Scroll to the bottom first: (a) evicts any lingering Kendo edit row that
    # would bounce scrollTop=0 writes back, and (b) immediately surfaces
    # late-alphabet (Y/Z) employees who live near the end of the list.
    scroll_container.evaluate("el => { el.scrollTop = el.scrollHeight; }")
    page.wait_for_timeout(300)
    if _scroll_and_confirm():   # fast-path for employees near the end of the list
        return True

    scroll_container.evaluate("el => { el.scrollTop = 0; }")
    page.wait_for_timeout(400)   # let Kendo re-render the initial rows at top

    if _scroll_and_confirm():
        return True

    # Strategy A: scroll one viewport-height at a time and stop as soon as
    # scrollTop stops advancing (bottom reached).  clientHeight-relative steps
    # cover the full grid regardless of zoom/row height; early exit avoids
    # wasting time after the last row is already past.
    for _ in range(40):
        reached_bottom = scroll_container.evaluate(
            "el => { const prev = el.scrollTop; el.scrollTop += el.clientHeight; "
            "return el.scrollTop === prev; }"
        )
        page.wait_for_timeout(150)
        if _scroll_and_confirm():
            return True
        if reached_bottom:
            break  # already at the bottom — no more rows to uncover

    logging.warning(f"SCROLL: Could not locate rows for '{worker_name}' in the grid.")
    return False


# ---------------------------------------------------------------------------
# Employee-filter helpers
# ---------------------------------------------------------------------------

def _open_employee_dropdown(page: Page) -> bool:
    """
    Opens the emp-input Kendo DropDownList popup and waits until the full employee
    list (more than just the 'Select employee' placeholder) is loaded.
    Returns True when ready, False if all strategies fail.

    Design rule: never re-click when the popup is already open — Kendo toggles the
    popup on each click, so a second click would close it instead of keeping it open.
    Each strategy first checks popup presence before deciding whether to click.
    """
    popup_host = page.locator("kendo-popup")
    comp       = page.locator(SELECTORS["emp_filter"])

    def _popup_is_open() -> bool:
        return popup_host.count() > 0

    def _popup_items_loaded() -> bool:
        """True once the employee list rows (>1 items) are present in the popup.
        A count of exactly 1 means only the 'Select employee' placeholder loaded."""
        try:
            page.wait_for_function(
                "() => document.querySelectorAll('kendo-popup li.k-list-item').length > 1",
                timeout=5000,
            )
            return True
        except Exception:
            return False

    # Strategy 1: click the inner toggle button Kendo renders (.k-input-button in v8+,
    # .k-select in older versions).  The host kendo-dropdownlist element itself has no
    # click handler in Angular — clicking it silently does nothing.
    if not _popup_is_open():
        inner = comp.locator(".k-input-button, .k-select")
        if inner.count() > 0:
            inner.first.click()
        else:
            comp.click()
        page.wait_for_timeout(600)

    if _popup_is_open():
        if _popup_items_loaded():
            return True
        # Popup opened but items never loaded — emit DOM snapshot for debugging.
        try:
            dom_snapshot = page.evaluate(
                "() => { const p = document.querySelector('kendo-popup'); "
                "return p ? p.innerHTML.substring(0, 800) : 'no kendo-popup'; }"
            )
            logging.warning(f"EMP_FILTER: Popup open but employee list empty. DOM: {dom_snapshot}")
        except Exception:
            pass
        return False

    # Strategy 2: popup did not open — try a JavaScript click which bypasses Angular
    # zone wrapping that can swallow synthetic Playwright events.
    page.evaluate(
        """() => {
            const btn = document.querySelector(
                "[data-testid='emp-input'] .k-input-button, "
                + "[data-testid='emp-input'] .k-select, "
                + "[data-testid='emp-input'] .k-picker"
            ) || document.querySelector("[data-testid='emp-input']");
            if (btn) btn.click();
        }"""
    )
    page.wait_for_timeout(800)
    if _popup_is_open() and _popup_items_loaded():
        return True

    # Strategy 3: force-click as last resort (only if still not open).
    if not _popup_is_open():
        try:
            comp.click(force=True)
            page.wait_for_timeout(800)
        except Exception:
            pass
    if _popup_is_open() and _popup_items_loaded():
        return True

    page.keyboard.press("Escape")
    return False


def _read_dropdown_names_via_js(page: Page) -> list[str]:
    """
    Reads all visible kendo-popup list item texts using JavaScript textContent.
    This is robust against any internal span structure Kendo may use — it captures
    the text regardless of whether it lives in span.k-list-item-text or elsewhere.
    """
    try:
        raw: list = page.evaluate(
            "() => Array.from(document.querySelectorAll('kendo-popup li.k-list-item'))"
            ".map(li => (li.textContent || '').trim())"
        )
        return raw if isinstance(raw, list) else []
    except Exception as e:
        logging.warning(f"EMP_FILTER: JS read of popup names failed: {e}")
        return []


def _get_all_employees_from_dropdown(page: Page) -> list:
    """
    Opens the emp-input dropdown and returns all employee display names in order.
    Closes the popup without selecting anything so the current filter is unchanged.
    """
    if not _open_employee_dropdown(page):
        logging.warning("EMP_FILTER: Dropdown popup did not open — no employee list returned.")
        return []

    raw_names = _read_dropdown_names_via_js(page)
    if raw_names:
        logging.info(f"EMP_FILTER: Raw popup sample (first 3): {raw_names[:3]}")

    # Exclude the Kendo defaultItem placeholder and blanks.
    names = [n for n in raw_names if n and n.lower() != "select employee"]
    page.keyboard.press("Escape")
    page.wait_for_timeout(300)
    logging.info(f"EMP_FILTER: {len(names)} employees found in dropdown.")
    return names


def _filter_grid_by_employee(page: Page, employee_name: str) -> bool:
    """
    Selects employee_name in the emp-input dropdown so the grid shows only
    that employee's rows.  Eliminates virtual-scroll stale-DOM issues because
    all rows for the selected employee are always in the DOM render buffer.
    Returns True when the item was found and clicked.
    """
    if not _open_employee_dropdown(page):
        logging.warning(f"EMP_FILTER: Could not open dropdown to filter by '{employee_name}'.")
        return False
    try:
        # JS click: exact text match, robust against any internal span structure.
        clicked: bool = page.evaluate(
            """(name) => {
                const items = document.querySelectorAll('kendo-popup li.k-list-item');
                for (const li of items) {
                    if ((li.textContent || '').trim() === name) { li.click(); return true; }
                }
                return false;
            }""",
            employee_name,
        )
        if not clicked:
            # Fallback: Playwright locator with partial-text match.
            item = (
                page.locator(SELECTORS["emp_filter_popup_items"])
                .filter(has_text=employee_name)
                .first
            )
            item.wait_for(state="visible", timeout=3000)
            item.click()
        wait_for_loading(page)
        return True
    except Exception as e:
        page.keyboard.press("Escape")
        logging.warning(f"EMP_FILTER: Could not select '{employee_name}': {e}")
        return False


def _clear_employee_filter(page: Page) -> None:
    """
    Resets the emp-input dropdown to 'Select employee' so the grid shows all rows.
    Uses JS to click the first popup item (the Kendo defaultItem placeholder).
    """
    if not _open_employee_dropdown(page):
        logging.warning("EMP_FILTER: Could not open dropdown to clear filter.")
        return
    try:
        # The Kendo defaultItem ("Select employee") is always the first list item.
        page.evaluate(
            "() => { const first = document.querySelector('kendo-popup li.k-list-item');"
            " if (first) first.click(); }"
        )
        wait_for_loading(page)
    except Exception as e:
        page.keyboard.press("Escape")
        logging.warning(f"EMP_FILTER: Could not clear filter: {e}")


def _calculate_proposed_times(task: dict, target_dt: dt):
    """Returns (proposed_start_dt, proposed_end_dt) or (None, None) for skipped tasks."""
    s_range = task["s_range"]
    if len(s_range) < 2:
        return None, None

    s_start_dt = parse_time_to_datetime(s_range[0], target_dt)
    s_end_dt   = parse_time_to_datetime(s_range[1], target_dt)
    if not s_start_dt or not s_end_dt:
        return None, None

    policy  = determine_task_policy(task["code"], task["name"])
    if policy is None:
        return None, None

    if policy.is_one_minute_only:
        return s_start_dt, s_start_dt + datetime.timedelta(minutes=1)

    # Schedule times are always authoritative — use them directly for all task types.
    return s_start_dt, s_end_dt


def _handle_confirm_changes_dialog(page: Page, task_code: str,
                                    timeout: int = 8000) -> bool:
    """Dismisses any Kendo modal dialog that is currently blocking the page.

    Handles three distinct dialog types:

    1. 'Unsaved changes' dialog — click Yes to commit the edit.
    2. 'Employee Conflict' dialog — click Ok to acknowledge.
    3. Any other Kendo dialog (e.g. 'Task Save Detail') — click the × close
       button. These dialogs have no known action button but their k-overlay
       blocks all subsequent grid interactions if not dismissed.

    Returns True if a dialog was found and dismissed, False otherwise.
    """
    wrapper_sel  = "[data-testid='confirm-changes-dialog']"
    any_dlg_sel  = "div[role='dialog'][aria-modal='true'].k-dialog"

    # Fast-path: wait for any Kendo modal to become visible.
    detected = False
    for sel in (wrapper_sel, any_dlg_sel):
        try:
            page.wait_for_selector(sel, state="visible", timeout=timeout)
            detected = True
            break
        except Exception:
            pass

    if not detected:
        return False

    # Determine dialog type by checking which button is present.
    conflict_btn = page.locator(SELECTORS["btn_conflict_ok"]).first
    yes_btn      = page.locator(SELECTORS["btn_confirm_changes_yes"]).first

    if conflict_btn.is_visible():
        # ── Employee Conflict dialog ──────────────────────────────────────────
        logging.info(
            f"DIALOG: 'Employee Conflict' dialog detected for {task_code}. "
            "Clicking Ok to acknowledge."
        )
        try:
            conflict_btn.scroll_into_view_if_needed()
            conflict_btn.click(force=True)
            page.wait_for_selector(any_dlg_sel, state="hidden", timeout=8000)
            return True
        except Exception as e:
            logging.warning(
                f"DIALOG: Could not dismiss conflict dialog for {task_code}: {e}"
            )
            return False

    if yes_btn.is_visible():
        # ── Unsaved changes dialog ────────────────────────────────────────────
        logging.info(
            f"DIALOG: 'Unsaved changes' dialog detected for {task_code}. Clicking Yes."
        )
        try:
            yes_btn.scroll_into_view_if_needed()
            yes_btn.click(force=True)
            page.wait_for_selector(wrapper_sel, state="hidden", timeout=8000)
            return True
        except Exception as e:
            logging.warning(f"DIALOG: Could not dismiss 'Unsaved changes' dialog for {task_code}: {e}")
            return False

    # ── Unknown / Task Save Detail dialog — click the × close button ─────────
    # These dialogs appear as server-side feedback after saves. They have no
    # action buttons we care about; their k-overlay blocks grid interactions.
    try:
        # Try to read the dialog title for logging
        title_el = page.locator(".k-dialog-title, .k-window-title").first
        title_text = title_el.text_content(timeout=1000).strip() if title_el.count() > 0 else "unknown"
    except Exception:
        title_text = "unknown"

    logging.info(
        f"DIALOG: Unknown dialog ('{title_text}') detected for {task_code}. "
        "Clicking close (×) button to dismiss."
    )
    close_btn = page.locator(SELECTORS["btn_dialog_close"]).first
    try:
        close_btn.wait_for(state="visible", timeout=3000)
        close_btn.click(force=True)
        page.wait_for_selector(any_dlg_sel, state="hidden", timeout=8000)
        logging.info(f"DIALOG: '{title_text}' dialog dismissed for {task_code}.")
        return True
    except Exception as e:
        logging.warning(
            f"DIALOG: Could not dismiss '{title_text}' dialog for {task_code}: {e}"
        )
        # Last resort: press Escape to close any modal
        try:
            page.keyboard.press("Escape")
            page.wait_for_timeout(500)
            logging.info(f"DIALOG: Sent Escape to dismiss '{title_text}' dialog for {task_code}.")
            return True
        except Exception:
            return False



def _save_via_navigation_dialog(page: Page, task_code: str,
                                 current_task_idx: int, tasks: list,
                                 emp_filter_name: str = "") -> bool:
    """
    Saves an in-edit row by triggering Kendo's "Unsaved changes" dialog.

    Strategy (in priority order)
    ─────────────────────────────
    PRIMARY — Double-click the next employee's paid-start cell.
      The dialog reliably fires when Kendo detects an edit attempt on a row that
      belongs to a different employee block.  Double-click is the real edit
      gesture, matching exactly what a user would do.

    FALLBACK — Double-click within-block adjacent row (paid-start → paid-end).
      Used only when the next-employee row is not reachable from the DOM.

    Pre-step: dispatch 'input'+'change' on every input inside the current edit
    row so Angular's FormControl marks the values as dirty before navigation.
    Without this, Kendo sees the row as unchanged and skips the dialog.
    """
    try:
        # ── Pre-step: mark row dirty in Angular ───────────────────────────────
        page.evaluate("""() => {
            var row = document.querySelector('tr.k-grid-edit-row');
            if (!row) return;
            row.querySelectorAll('input').forEach(function(inp) {
                inp.dispatchEvent(new Event('input',  { bubbles: true }));
                inp.dispatchEvent(new Event('change', { bubbles: true }));
            });
        }""")
        page.wait_for_timeout(600)

        def _dblclick_coords_and_check(cx: float, cy: float, label: str) -> bool:
            """Double-click at (cx, cy) and return True if the dialog appears."""
            logging.info(
                f"SAVE_VIA_DIALOG [{task_code}]: dblclick {label} "
                f"at ({cx:.0f}, {cy:.0f})."
            )
            page.mouse.dblclick(cx, cy)
            page.wait_for_timeout(2000)
            if _handle_confirm_changes_dialog(page, task_code, timeout=5000):
                return True
            return False

        # ── PRIMARY: double-click next employee's paid-start cell ─────────────
        logging.info(
            f"SAVE_VIA_DIALOG [{task_code}]: PRIMARY — locating next-employee row."
        )
        try:
            last_handle = tasks[-1]["row"].element_handle(timeout=2000)
            if last_handle:
                # Walk DOM forward from the last row of the current employee block
                # to find the first k-master-row belonging to a different employee.
                # Try paid-start (col 10), then paid-end (col 11), then first td.
                coords_list = page.evaluate("""(lastRow) => {
                    var results = [];
                    var el = lastRow.nextElementSibling;
                    while (el) {
                        if (el.classList.contains('k-master-row')) {
                            var selectors = [
                                'td[aria-colindex="10"]',
                                'td[aria-colindex="11"]',
                                'td'
                            ];
                            for (var i = 0; i < selectors.length; i++) {
                                var cell = el.querySelector(selectors[i]);
                                if (cell) {
                                    cell.scrollIntoView({
                                        block: 'nearest', inline: 'nearest'
                                    });
                                    var r = cell.getBoundingClientRect();
                                    if (r.width > 0 && r.height > 0) {
                                        results.push({
                                            x: r.left + r.width  / 2,
                                            y: r.top  + r.height / 2,
                                            col: selectors[i]
                                        });
                                        break;
                                    }
                                }
                            }
                            break;
                        }
                        el = el.nextElementSibling;
                    }
                    return results;
                }""", last_handle)

                if coords_list:
                    page.wait_for_timeout(400)   # let scroll settle
                    c = coords_list[0]
                    cx, cy = c["x"], c["y"]
                    col_label = f"next-employee {c['col']}"
                    if _dblclick_coords_and_check(cx, cy, col_label):
                        wait_for_loading(page)
                        return True
                    # Second attempt: single-click first, then double-click
                    logging.info(
                        f"SAVE_VIA_DIALOG [{task_code}]: dblclick did not trigger dialog — "
                        "retrying with single-click then dblclick."
                    )
                    page.mouse.click(cx, cy)
                    page.wait_for_timeout(600)
                    if _dblclick_coords_and_check(cx, cy, col_label + " (retry)"):
                        wait_for_loading(page)
                        return True
                else:
                    logging.info(
                        f"SAVE_VIA_DIALOG [{task_code}]: no next-employee row found in DOM."
                    )
        except Exception as ex:
            logging.warning(
                f"SAVE_VIA_DIALOG [{task_code}]: next-employee PRIMARY failed — {ex}"
            )

        # ── FALLBACK: double-click within-block adjacent row ──────────────────
        if current_task_idx + 1 < len(tasks):
            nav_row = tasks[current_task_idx + 1]["row"]
        elif current_task_idx > 0:
            nav_row = tasks[current_task_idx - 1]["row"]
        else:
            nav_row = None

        if nav_row is not None:
            logging.info(
                f"SAVE_VIA_DIALOG [{task_code}]: FALLBACK — dblclick within-block "
                "adjacent row."
            )
            for label, sel in (
                ("paid-start", SELECTORS["row_paid_start"]),
                ("paid-end",   SELECTORS["row_paid_end"]),
            ):
                try:
                    cell = nav_row.locator(sel)
                    cell.scroll_into_view_if_needed()
                    box = cell.bounding_box()
                    if box:
                        cx = box["x"] + box["width"]  / 2
                        cy = box["y"] + box["height"] / 2
                        if _dblclick_coords_and_check(cx, cy, f"within-block {label}"):
                            wait_for_loading(page)
                            return True
                except Exception as ex:
                    logging.warning(
                        f"SAVE_VIA_DIALOG [{task_code}]: fallback {label} — {ex}"
                    )

        # FILTER BYPASS — when the grid is filtered to one employee there is no
        # next-employee row visible and single-task blocks have no adjacent row
        # either.  Temporarily clear the employee filter so any other employee's
        # row becomes accessible, trigger the dialog from there, then re-apply
        # the filter so the caller's rows are back in the DOM.
        if emp_filter_name:
            logging.info(
                f"SAVE_VIA_DIALOG [{task_code}]: FILTER BYPASS — "
                "clearing employee filter to expose a next-employee row."
            )
            _clear_employee_filter(page)
            page.wait_for_timeout(500)
            try:
                coord = page.evaluate("""() => {
                    var editRow = document.querySelector('tr.k-grid-edit-row');
                    var rows = document.querySelectorAll(
                        '[data-testid="payload-task-grid"] tbody tr.k-master-row'
                    );
                    for (var i = 0; i < rows.length; i++) {
                        if (rows[i] === editRow) continue;
                        var cell = rows[i].querySelector('td[aria-colindex="10"]')
                                || rows[i].querySelector('td');
                        if (!cell) continue;
                        cell.scrollIntoView({ block: 'nearest', inline: 'nearest' });
                        var r = cell.getBoundingClientRect();
                        if (r.width > 0 && r.height > 0)
                            return { x: r.left + r.width / 2, y: r.top + r.height / 2 };
                    }
                    return null;
                }""")
                if coord:
                    if _dblclick_coords_and_check(
                        coord["x"], coord["y"], "filter-bypass any-other-row"
                    ):
                        wait_for_loading(page)
                        _filter_grid_by_employee(page, emp_filter_name)
                        return True
            except Exception as ex:
                logging.warning(f"SAVE_VIA_DIALOG [{task_code}]: filter bypass — {ex}")
            _filter_grid_by_employee(page, emp_filter_name)  # re-apply regardless

        logging.warning(
            f"SAVE_VIA_DIALOG: Save dialog did not appear for {task_code} "
            "after all strategies (next-employee dblclick + within-block fallback)."
        )
        return False

    except Exception as e:
        logging.error(f"SAVE_VIA_DIALOG: Failed for {task_code}: {e}")
        return False


def _click_update_button(page: Page, task_code: str) -> bool:
    """
    Clicks the Update (save) button that Kendo renders in the command cell
    (column 17) whenever a row is in edit mode.

    Returns True if the button was found, clicked, and the edit row closed
    (confirming the save was committed).  Returns False with a WARNING log
    explaining why it could not complete so the caller can fall back.
    """
    try:
        edit_row = page.locator("tr.k-grid-edit-row")

        try:
            edit_row.wait_for(state="visible", timeout=2000)
        except Exception as e:
            logging.warning(
                f"UPDATE_BTN [{task_code}]: No active edit row found — "
                f"cannot locate Update button ({e})."
            )
            return False

        update_btn = edit_row.locator(
            "button.k-grid-save-command, "
            "button[title='Update'], "
            "button[kendogridsavecommand]"
        ).first

        try:
            update_btn.wait_for(state="visible", timeout=2000)
        except Exception as e:
            logging.warning(
                f"UPDATE_BTN [{task_code}]: Update button not visible in edit row — "
                f"({e})."
            )
            return False

        if not update_btn.is_enabled():
            logging.warning(
                f"UPDATE_BTN [{task_code}]: Update button is disabled — "
                "values may not have been accepted by Kendo yet."
            )
            return False

        logging.info(f"UPDATE_BTN [{task_code}]: Clicking Update button.")
        update_btn.click(force=True)
        page.wait_for_timeout(500)
        wait_for_loading(page)

        # Confirm the save landed: the edit row should disappear.
        try:
            page.wait_for_selector("tr.k-grid-edit-row", state="hidden", timeout=4000)
            logging.info(
                f"UPDATE_BTN [{task_code}]: Save confirmed — edit row closed after Update click."
            )
            return True
        except Exception:
            logging.warning(
                f"UPDATE_BTN [{task_code}]: Edit row still visible after Update click — "
                "save may not have completed."
            )
            return False

    except Exception as e:
        logging.warning(f"UPDATE_BTN [{task_code}]: Unexpected error — {e}.")
        return False


def _shadow_classify(task_code: str, task_name: str, keyword_policy) -> None:
    """Runs the ML classifier alongside the keyword policy. Never raises."""
    try:
        from task_classifier import shadow_compare
        shadow_compare(task_code, task_name, keyword_policy)
    except Exception:
        pass


def _adjust_worker_tasks(page: Page, worker_filter, worker_display: str,
                         worker_id: str, target_dt: dt,
                         scroll_container=None, worker_name: str = "",
                         emp_filter_name: str = "") -> bool:
    """
    Adjusts all unadjusted paid-time tasks for one worker, then signals
    readiness for verification.

    Per-task flow (matches the required automation steps):
      1. Adjust the paid-start and paid-end cells.
      2. Click the adjacent row to trigger Kendo's "Unsaved changes" dialog,
         then click Yes to commit the save.
      3. The grid reloads after each save, so rows are re-read fresh and the
         next unadjusted task is located for the next iteration.
    Verification (checkbox marking + Verify button) is handled by the caller
    ONLY after this function returns True — i.e., after ALL tasks for this
    worker have been adjusted.

    Returns True when no more adjustments are needed, False when the worker
    must be skipped (repeated failures or manual-review flag).
    """
    retry_tracking: dict[str, int] = {}
    manual_flag = False
    _stale_budget = 3   # retries before giving up when rows aren't in DOM yet
    _saved_task_count = 0  # tasks successfully committed this call

    while not AUTOMATION_STOP_FLAG:
        # Bring worker rows into the Kendo virtual grid's render window BEFORE
        # calling .all() — otherwise virtualized (off-screen) rows are absent
        # from the DOM and every subsequent locator operation times out (30 s).
        if scroll_container is not None and worker_name:
            _scroll_worker_into_view(page, scroll_container, worker_name)
            page.wait_for_timeout(400)

        wait_for_loading(page)

        # If the page-loading overlay is still visible after the normal wait,
        # a VPN/network stall is the likely cause — attempt full session recovery.
        if is_page_loading(page):
            logging.warning(
                f"NETWORK: Page-loading overlay still present for {worker_display} "
                "— checking connectivity and attempting recovery."
            )
            if not _recover_from_network_stall(
                page, target_dt=target_dt, emp_filter_name=emp_filter_name
            ):
                return False
            continue  # re-read rows from the restored session

        worker_rows = worker_filter.all()
        if not worker_rows:
            if _stale_budget > 0:
                _stale_budget -= 1
                logging.info(
                    f"STALE: Rows not in DOM yet for {worker_display} — "
                    f"retrying ({_stale_budget} retries left)."
                )
                if emp_filter_name:
                    _filter_grid_by_employee(page, emp_filter_name)
                else:
                    wait_for_loading(page)
                continue
            logging.warning(f"STALE: Rows gone for {worker_display} after all retries. Skipping.")
            if _saved_task_count > 0:
                # Tasks were saved but rows disappeared (grid filter reset after save).
                # Proceed to verification rather than skipping — the rows may reappear
                # after the filter is re-applied in _verify_worker_tasks.
                logging.info(
                    f"STALE: {_saved_task_count} task(s) saved for {worker_display} "
                    "— rows filtered out post-save. Proceeding to verification."
                )
                return True
            return False

        _stale_budget = 3  # reset on successful read so later iterations get fresh budget

        # Build task list and collect fixed (verified) intervals
        tasks = [_read_task_row(r) for r in worker_rows]
        fixed_intervals = [
            (parse_time_to_datetime(t["p_start"], target_dt),
             parse_time_to_datetime(t["p_end"],   target_dt))
            for t in tasks
            if t["verified"]
        ]

        # PRE-SCAN: scan the whole employee block — every task, every class,
        # verified and unverified — to build the complete schedule picture
        # before any unverified task is adjusted.
        #
        # An "anchor" is any scheduled slot that an unverified task must not
        # violate when its paid time is placed:
        #   • Verified tasks (all classes)      → anchor (slot already committed)
        #   • Unverified non-Spare tasks        → anchor (schedule is authoritative)
        #   • Unverified Spare CDL/Monitor      → NOT an anchor; these are the
        #                                         tasks being adjusted to fit around
        #                                         everything else
        #   • Bridge Charter (policy = None)    → excluded (skipped by adj. loop)
        #
        # Adjustments are only ever applied to unverified tasks, following the
        # existing task-policy and schedule-conflict conditionals already in place.
        anchor_schedule_intervals: list = []
        spare_unverified_count = 0
        for _t in tasks:
            _tp = determine_task_policy(_t["code"], _t["name"])
            if _tp is None:
                continue  # Bridge Charter — excluded
            if _tp.require_schedule_match and not _t["verified"]:
                # Unverified Spare CDL/Monitor — being adjusted, not an anchor
                spare_unverified_count += 1
                continue
            # Everything else (verified any class, unverified non-Spare):
            # schedule time is a fixed anchor for the whole employee block
            _sr = _t["s_range"]
            if len(_sr) >= 2:
                _ss = parse_time_to_datetime(_sr[0], target_dt)
                _se = parse_time_to_datetime(_sr[1], target_dt)
                if _ss and _se and _ss < _se:
                    anchor_schedule_intervals.append((_ss, _se))

        if spare_unverified_count and anchor_schedule_intervals:
            logging.info(
                f"PRE_SCAN: {worker_display} — full employee block scanned: "
                f"{spare_unverified_count} unverified Spare CDL/Monitor task(s) "
                f"resolved against {len(anchor_schedule_intervals)} anchor schedule "
                f"interval(s) (all classes, verified + unverified)."
            )

        adjustment_made = False
        needs_retry     = False   # set when a save attempt fails so we loop again

        for task_idx, task in enumerate(tasks):
            if task["verified"]:
                continue
            task_policy = determine_task_policy(task["code"], task["name"])
            _shadow_classify(task["code"], task["name"], task_policy)
            if task_policy is None:
                continue  # Bridge Charter or similar — skip

            prop_s, prop_e = _calculate_proposed_times(task, target_dt)
            if not prop_s:
                continue

            # Guard: raw interval invalid (start >= end).  Kendo silently
            # rejects reversed times so the row never becomes dirty and the
            # "Unsaved changes" dialog never fires — all retries are wasted.
            if prop_s >= prop_e:
                logging.warning(
                    f"SKIP: {task['code']} for {worker_display} has invalid interval "
                    f"({datetime_to_time_str(prop_s)} ≥ {datetime_to_time_str(prop_e)}) "
                    f"— manual review needed."
                )
                manual_flag = True
                continue

            # Resolve overlaps. Spare CDL/Monitor tasks must also avoid the
            # schedule times of every other task for this employee (anchor
            # intervals), not just already-verified paid times.
            if task_policy.require_schedule_match and anchor_schedule_intervals:
                blocking_intervals = fixed_intervals + anchor_schedule_intervals
                if any(
                    intervals_overlap(prop_s, prop_e, bs, be)
                    for bs, be in anchor_schedule_intervals
                ):
                    logging.info(
                        f"SPARE_CONFLICT: {task['code']} for {worker_display} — "
                        f"schedule time {datetime_to_time_str(prop_s)}–{datetime_to_time_str(prop_e)} "
                        f"conflicts with anchor task(s). Resolving against all "
                        f"{len(blocking_intervals)} blocking interval(s)."
                    )
                final_s, final_e = get_non_overlapping_interval(prop_s, prop_e, blocking_intervals)

            elif task_policy.is_one_minute_only:
                # Extra Work / S2S Charter: also block against other unverified
                # 1-minute tasks so multiple same-start-time entries stagger
                # correctly. Use each peer's current paid time if already set
                # (from a previous outer-loop save), otherwise use its schedule time.
                other_one_min: list = []
                for _oi, _ot in enumerate(tasks):
                    if _oi == task_idx or _ot["verified"]:
                        continue
                    _op = determine_task_policy(_ot["code"], _ot["name"])
                    if _op is None or not _op.is_one_minute_only:
                        continue
                    _ops = parse_time_to_datetime(_ot["p_start"], target_dt)
                    _ope = parse_time_to_datetime(_ot["p_end"],   target_dt)
                    if _ops and _ope and _ops < _ope:
                        other_one_min.append((_ops, _ope))
                    else:
                        _osr = _ot["s_range"]
                        if len(_osr) >= 2:
                            _oss = parse_time_to_datetime(_osr[0], target_dt)
                            _ose = parse_time_to_datetime(_osr[1], target_dt)
                            if _oss and _ose and _oss < _ose:
                                other_one_min.append((_oss, _ose))
                # Include anchor_schedule_intervals so the 1-minute entry never
                # lands on a scheduled slot that ByteCurve would flag as a conflict.
                blocking_intervals = fixed_intervals + other_one_min + anchor_schedule_intervals
                _peer_conflict   = other_one_min and any(
                    intervals_overlap(prop_s, prop_e, bs, be)
                    for bs, be in other_one_min
                )
                _anchor_conflict = anchor_schedule_intervals and any(
                    intervals_overlap(prop_s, prop_e, bs, be)
                    for bs, be in anchor_schedule_intervals
                )
                if _peer_conflict or _anchor_conflict:
                    _sources = []
                    if _peer_conflict:
                        _sources.append("1-min peer(s)")
                    if _anchor_conflict:
                        _sources.append("anchor schedule task(s)")
                    logging.info(
                        f"ONE_MIN_CONFLICT: {task['code']} for {worker_display} — "
                        f"1-min slot {datetime_to_time_str(prop_s)} conflicts with "
                        f"{' and '.join(_sources)}. Resolving to nearest available slot."
                    )
                final_s, final_e = get_non_overlapping_interval(prop_s, prop_e, blocking_intervals)

            else:
                final_s, final_e = get_non_overlapping_interval(prop_s, prop_e, fixed_intervals)

            # Spare CDL/Monitor: the paid time duration must always equal the
            # full Schedule Hrs amount (prop_e − prop_s). get_non_overlapping_interval
            # already preserves this duration when shifting, so no truncation is
            # applied. If the conflict shift pushes the resolved start entirely
            # past the schedule end the displacement is too large to be automatic
            # — flag for manual review.
            if task_policy.require_schedule_match and final_s >= prop_e:
                logging.warning(
                    f"SKIP: {task['code']} for {worker_display} — "
                    f"conflict shift places start ({datetime_to_time_str(final_s)}) "
                    f"past Schedule Hrs end ({datetime_to_time_str(prop_e)}) "
                    f"— manual review needed."
                )
                manual_flag = True
                continue

            # Guard: overlap resolution (or the schedule-end cap above) can
            # produce a reversed interval.
            if final_s >= final_e:
                logging.warning(
                    f"SKIP: {task['code']} for {worker_display} interval invalid after "
                    f"overlap resolution ({datetime_to_time_str(final_s)} ≥ "
                    f"{datetime_to_time_str(final_e)}) — manual review needed."
                )
                manual_flag = True
                continue

            shift_mins = (final_s - prop_s).total_seconds() / 60
            if shift_mins > MAX_TIME_SHIFT_MINUTES:
                # Spare CDL/Monitor: duration is fixed to Schedule Hrs — large shifts expected.
                # Extra Work/S2S Charter: 1-min slot must always be placed — skip the guard.
                if task_policy.require_schedule_match:
                    logging.info(
                        f"SPARE_SHIFT: {task['code']} for {worker_display} — "
                        f"shifted {shift_mins:.0f} min (Schedule Hrs duration preserved: "
                        f"{datetime_to_time_str(final_s)}–{datetime_to_time_str(final_e)}) "
                        f"— proceeding."
                    )
                elif task_policy.is_one_minute_only:
                    logging.info(
                        f"ONE_MIN_SHIFT: {task['code']} for {worker_display} — "
                        f"shifted {shift_mins:.0f} min to {datetime_to_time_str(final_s)}–"
                        f"{datetime_to_time_str(final_e)} (1-min slot preserved) — proceeding."
                    )
                else:
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

            logging.info(
                f"STEP1: Adjusting paid cells — {task['code']} for {worker_display} "
                f"→ {t_start} – {t_end}"
            )

            # Dismiss any lingering dialog BEFORE touching cells so its overlay
            # does not intercept the dblclick on the paid-start/end cells.
            _handle_confirm_changes_dialog(page, task["code"], timeout=1500)

            ok_s = adjust_time_entry(page, task["row"], COL_PAID_START, t_start) \
                   if not times_match(task["p_start"], t_start) else True
            ok_e = adjust_time_entry(page, task["row"], COL_PAID_END,   t_end)   \
                   if not times_match(task["p_end"],   t_end)   else True

            if ok_s and ok_e:
                logging.info(
                    f"STEP2: Paid cells adjusted — attempting Update button save "
                    f"for {task['code']}."
                )
                saved = _click_update_button(page, task["code"])
                if not saved:
                    logging.info(
                        f"STEP2_FALLBACK [{task['code']}]: Update button did not save — "
                        "falling back to navigation dialog."
                    )
                    saved = _save_via_navigation_dialog(
                        page, task["code"], task_idx, tasks,
                        emp_filter_name=emp_filter_name
                    )

                if saved:
                    logging.info(
                        f"STEP3: {task['code']} saved. Grid reloaded — "
                        f"locating next unadjusted task for {worker_display}."
                    )
                    # Dismiss any schedule-conflict dialog ByteCurve fires back
                    # immediately after an individual save before the next iteration.
                    _handle_confirm_changes_dialog(page, task["code"], timeout=1500)
                    retry_tracking.pop(task_key, None)
                    adjustment_made = True
                    _saved_task_count += 1
                else:
                    # Neither strategy succeeded — retry on next outer loop pass.
                    logging.warning(
                        f"SAVE_FAIL: Both Update button and navigation dialog failed "
                        f"for {task['code']} ({worker_display}). Retrying on next pass."
                    )
                    _handle_confirm_changes_dialog(page, task["code"], timeout=1500)
                    needs_retry = True
                break  # Always break — grid state changed; re-read rows regardless
            else:
                logging.warning(
                    f"ADJUST_FAIL: Could not edit paid cells for {task['code']} "
                    f"({worker_display}). Checking if edit row is still open."
                )
                # Kendo TimePicker stores its value via a custom ControlValueAccessor,
                # so Playwright's fill() sets the displayed value correctly while
                # input.value (read by adjust_time_entry's VERIFY) returns ''.
                # If the edit row is still open in the DOM the fields ARE set —
                # navigate to the next employee to trigger the save dialog so
                # Kendo commits the values that are visually shown.
                edit_row_open = False
                try:
                    edit_row_open = page.locator("tr.k-grid-edit-row").is_visible(
                        timeout=1500
                    )
                except Exception:
                    pass

                if edit_row_open:
                    logging.info(
                        f"ADJUST_VERIFY_FAIL [{task['code']}]: Edit row is open — "
                        "fields may be set. Attempting Update button save."
                    )
                    saved = _click_update_button(page, task["code"])
                    if not saved:
                        logging.info(
                            f"ADJUST_VERIFY_FAIL_FALLBACK [{task['code']}]: Update button "
                            "did not save — falling back to navigation dialog."
                        )
                        saved = _save_via_navigation_dialog(
                            page, task["code"], task_idx, tasks,
                            emp_filter_name=emp_filter_name
                        )

                    if saved:
                        logging.info(
                            f"STEP3: {task['code']} saved despite VERIFY failure. "
                            f"Locating next unadjusted task for {worker_display}."
                        )
                        # Dismiss any schedule-conflict dialog ByteCurve fires back
                        # immediately after an individual save before the next iteration.
                        _handle_confirm_changes_dialog(page, task["code"], timeout=1500)
                        retry_tracking.pop(task_key, None)
                        adjustment_made = True
                        _saved_task_count += 1
                    else:
                        logging.warning(
                            f"SAVE_FAIL: Both Update button and navigation dialog failed "
                            f"for {task['code']} ({worker_display}) after VERIFY failure. Retrying."
                        )
                        _handle_confirm_changes_dialog(page, task["code"], timeout=1500)
                        needs_retry = True
                else:
                    # Edit row is not open — cell could not be opened at all
                    # (e.g. a dialog overlay blocked the dblclick). Dismiss any
                    # lingering dialog and retry on the next pass.
                    _handle_confirm_changes_dialog(page, task["code"], timeout=1500)
                    needs_retry = True
                break  # Always break — grid state may have changed; re-read rows

        if adjustment_made or needs_retry:
            continue  # Restart outer while — re-read rows, find next task

        # Inner for loop completed without any task needing adjustment AND
        # without any retry signal — all tasks already match their target times
        # (or were skipped due to policy/overlap).
        if manual_flag:
            logging.info(f"MANUAL_FLAG: Skipping verification for {worker_display}")
            return False

        # STEP4: All adjustments done — caller will mark checkboxes and verify.
        logging.info(
            f"STEP4: All paid-time adjustments complete for {worker_display}. "
            "Proceeding to verification."
        )
        return True

    return False  # Stopped


def _reapply_grid_filters(page: Page) -> None:
    """Re-checks all three filter checkboxes and re-submits the grid query.

    Called when worker rows disappear after a save (the grid filter can reset
    to 'Pending Review' only after a reload, hiding tasks that transitioned to
    Verified or Auto-Verified state).  Re-applying restores the full view so
    the verification step can find the rows.
    """
    try:
        page.locator(SELECTORS["checkbox_incomplete"]).check()
        page.locator(SELECTORS["checkbox_verified"]).check()
        page.locator(SELECTORS["checkbox_auto_verified"]).check()
        page.locator(SELECTORS["checkbox_pending_review"]).check()
        page.get_by_test_id(SELECTORS["filter_submit_btn"]).click()
        page.wait_for_load_state("networkidle")
        wait_for_loading(page)
        page.wait_for_timeout(1500)
        logging.info("FILTER: Re-applied all filter options (Incomplete / Verified / Auto-Verified / Pending Review).")
    except Exception as e:
        logging.warning(f"FILTER: Could not re-apply filters: {e}")


def _verify_worker_tasks(page: Page, worker_filter, worker_display: str,
                         scroll_container=None, worker_name: str = "",
                         emp_filter_name: str = "") -> None:
    """Checks all unchecked task checkboxes then clicks the Verify button.

    Processes checkboxes one at a time: after each successful check the rows
    are re-fetched from scratch.  This avoids stale-locator timeouts that occur
    when Kendo virtual scroll destroys and recreates DOM elements between the
    initial worker_filter.all() snapshot and the is_checked() call on later rows.
    """
    if emp_filter_name:
        pass  # Grid is already filtered from the adjustment step — no re-open needed.
    elif scroll_container is not None and worker_name:
        found = _scroll_worker_into_view(page, scroll_container, worker_name)
        if not found:
            # Rows may have disappeared because the grid filter reset after the last
            # save (task transitioned out of Pending Review).  Re-apply all filters
            # so verified/auto-verified rows become visible again, then retry scroll.
            logging.info(
                f"VERIFY: Rows not visible for {worker_display} — re-applying grid filters."
            )
            _reapply_grid_filters(page)
            _scroll_worker_into_view(page, scroll_container, worker_name)
        page.wait_for_timeout(400)

    checked_count = 0

    for _ in range(30):   # safety ceiling — no worker has 30 tasks
        # Re-fetch rows on every iteration so locators are fresh.
        if emp_filter_name:
            pass  # filter keeps rows in DOM — no scroll needed
        elif scroll_container is not None and worker_name:
            _scroll_worker_into_view(page, scroll_container, worker_name)
            page.wait_for_timeout(200)

        worker_rows = worker_filter.all()
        found_unchecked = False

        for row in worker_rows:
            try:
                row.scroll_into_view_if_needed(timeout=3000)
                page.wait_for_timeout(150)

                # Skip tasks that are excluded from verification (e.g. Bridge Charter).
                # determine_task_policy returns None for these — same guard used in
                # the adjustment loop so the two paths stay consistent.
                _task_code = (row.locator(SELECTORS["row_task_code"]).text_content(timeout=2000) or "").strip()
                _task_name = (row.locator(SELECTORS["row_task_name"]).text_content(timeout=2000) or "").strip()
                if determine_task_policy(_task_code, _task_name) is None:
                    logging.info(
                        f"VERIFY: Skipping '{_task_code} {_task_name}' for "
                        f"{worker_display} — excluded from verification."
                    )
                    continue

                already_checked = row.locator(SELECTORS["row_checkbox"]).is_checked(timeout=5000)
            except Exception as e:
                logging.warning(
                    f"VERIFY: Could not read row data for {worker_display} "
                    f"— skipping row. ({e})"
                )
                continue

            if not already_checked:
                if verify_task_checkbox(page, row, "N/A", worker_display):
                    checked_count += 1
                found_unchecked = True
                break   # re-fetch rows before processing the next unchecked row

        if not found_unchecked:
            break   # all rows checked (or none left)

    if checked_count > 0:
        logging.info(f"VERIFY: {checked_count} task(s) checked for {worker_display}. Clicking Verify.")
        click_verify_button(page, worker_display)
    else:
        logging.info(f"VERIFY: No new checkboxes needed for {worker_display}.")


def validate_and_process_rows(page: Page, target_date: str) -> None:
    """
    Main orchestration loop.

    Iterates through every employee listed in the emp-input dropdown (alphabetical
    order).  For each employee the grid is filtered to show only their rows before
    any adjustment or verification work begins.  This keeps every row permanently
    in the DOM render buffer — completely eliminating virtual-scroll stale-DOM
    issues — and replaces the previous scroll-based worker discovery.
    """
    logging.info(f"Processing date: {target_date}")
    target_dt        = dt.strptime(target_date, "%Y-%m-%d")
    target_date_full = target_dt.strftime("%m/%d/%Y")

    _setup_view_and_filters(page, target_dt)

    employee_names = _get_all_employees_from_dropdown(page)
    if not employee_names:
        logging.warning("EMP_FILTER: No employees found in dropdown — nothing to process.")
        return

    history = load_history()
    employee_names = sort_employees_by_priority(employee_names, history)

    processed_workers: set[str] = set()
    grid_rows_sel = f"{SELECTORS['payload_task_grid']} tbody tr.k-master-row"

    for emp_filter_name in employee_names:
        if AUTOMATION_STOP_FLAG:
            break

        # Guard against a VPN drop that occurred between employees: the
        # page-loading overlay would block the employee-filter click and
        # produce the exact timeout seen in the log.  Detect it here before
        # touching any UI element and recover if needed.
        wait_for_loading(page)
        if is_page_loading(page):
            logging.warning(
                f"NETWORK: Page-loading overlay stuck before filtering for "
                f"'{emp_filter_name}' — checking connectivity and attempting recovery."
            )
            if not _recover_from_network_stall(page, target_dt=target_dt):
                break  # unrecoverable — stop the run
            # _recover_from_network_stall already restored the view via
            # _setup_view_and_filters; the per-employee filter below re-selects
            # the right employee.

        if not _filter_grid_by_employee(page, emp_filter_name):
            continue

        # Identify the canonical worker_id / worker_name from the filtered grid rows.
        task_rows     = page.locator(grid_rows_sel).all()
        worker_id     = worker_name = worker_display = ""
        for row in task_rows:
            row_date = (row.locator(SELECTORS["row_date"]).text_content(timeout=2000) or "").strip()
            if row_date != target_date_full:
                continue
            name_span = row.locator(SELECTORS["row_worker_name"]).locator("span[title]").first
            if name_span.count() == 0:
                continue
            emp_id       = (name_span.get_attribute("title", timeout=2000) or "").strip()
            worker_name  = (name_span.text_content(timeout=2000) or "").strip()
            worker_id    = emp_id or worker_name
            worker_display = f"{worker_name} ({emp_id})" if emp_id else worker_name
            break

        if not worker_id or worker_id in processed_workers:
            continue

        # Build a live locator filtered to this worker's rows.
        worker_filter = (
            page.locator(f"{SELECTORS['payload_task_grid']} tbody tr.k-master-row")
            .filter(has=page.locator("td[aria-colindex='2'] span").get_by_text(worker_name, exact=True))
        )

        logging.info(f"WORKER: Processing {worker_display}")

        adjustments_ok = _adjust_worker_tasks(
            page, worker_filter, worker_display, worker_id, target_dt,
            emp_filter_name=emp_filter_name,
        )

        if adjustments_ok:
            _verify_worker_tasks(
                page, worker_filter, worker_display,
                emp_filter_name=emp_filter_name,
            )
            logging.info(f"WORKER: Done with {worker_display}")
        else:
            logging.warning(
                f"WORKER: Skipping verification for {worker_display} (manual review needed)"
            )

        record_outcome(emp_filter_name, success=adjustments_ok,
                       manual_flag=not adjustments_ok, history=history)
        logging.info(f"STEP5: Moving to next employee after completing {worker_display}.")
        processed_workers.add(worker_id)

    save_history(history)


# ===========================================================================
# Network resilience helpers
# ===========================================================================

NETWORK_CHECK_HOST            = "app.bytecurve360.com"
NETWORK_CHECK_PORT            = 443
NETWORK_STALL_TIMEOUT_MS      = 30_000   # ms before treating a stuck loader as a network stall
NETWORK_RECOVERY_POLL_SEC     = 10       # seconds between TCP connectivity polls
NETWORK_RECOVERY_MAX_WAIT_SEC = 300      # give up after 5 minutes


def _is_network_reachable() -> bool:
    """TCP handshake to the app server — True when VPN/network is up."""
    try:
        with socket.create_connection((NETWORK_CHECK_HOST, NETWORK_CHECK_PORT), timeout=5):
            return True
    except (socket.timeout, OSError):
        return False


def _wait_for_network_recovery(max_wait_sec: int = NETWORK_RECOVERY_MAX_WAIT_SEC) -> bool:
    """Polls every NETWORK_RECOVERY_POLL_SEC seconds until connectivity returns.
    Returns True when restored, False if the wait timed out or Stop was requested."""
    logging.warning(
        f"NETWORK: Waiting up to {max_wait_sec}s for VPN/network connectivity to restore..."
    )
    elapsed = 0
    while elapsed < max_wait_sec:
        if AUTOMATION_STOP_FLAG:
            return False
        if _is_network_reachable():
            logging.info(f"NETWORK: Connectivity restored after ~{elapsed}s.")
            return True
        time.sleep(NETWORK_RECOVERY_POLL_SEC)
        elapsed += NETWORK_RECOVERY_POLL_SEC
        logging.info(f"NETWORK: Still waiting... ({elapsed}/{max_wait_sec}s elapsed)")
    logging.error(f"NETWORK: Gave up waiting after {max_wait_sec}s — aborting.")
    return False


def _recover_from_network_stall(page, target_dt=None, emp_filter_name: str = "") -> bool:
    """Called when the page-loading overlay is stuck (VPN/network stall suspected).

    Steps:
      1. TCP check — if unreachable, poll until restored (up to 5 min).
      2. Reload the page; fall back to navigating to the login URL if reload fails.
      3. Re-login if the session expired while the VPN was down.
      4. Re-navigate to the Verify Hours payroll view.
      5. Re-apply date + status filters when target_dt is supplied.
      6. Re-select the current employee when emp_filter_name is supplied so the
         caller's worker_filter locator resolves correctly on the next iteration.

    Returns True when the session is fully restored, False if unrecoverable.
    """
    if not _is_network_reachable():
        logging.warning("NETWORK: TCP check failed — VPN/network appears to be down.")
        if not _wait_for_network_recovery():
            return False
        time.sleep(3)  # let TCP layer stabilise before making browser requests
    else:
        logging.info(
            "NETWORK: TCP check passed but page-loading overlay is stuck. "
            "Reloading to clear state."
        )
        time.sleep(2)

    logging.info("NETWORK: Reloading page to clear stuck loading overlay...")
    try:
        page.reload(timeout=30_000, wait_until="networkidle")
        wait_for_loading(page, timeout_ms=30_000)
    except Exception as e:
        logging.warning(f"NETWORK: Page reload failed ({e}). Navigating to login URL.")
        try:
            page.goto(BYTECURVE_URL, timeout=30_000, wait_until="networkidle")
        except Exception as e2:
            logging.error(f"NETWORK: Could not reach app after reload failure: {e2}")
            return False

    # Re-login if the session expired while the VPN was down.
    if "#/login" in page.url or page.url.rstrip("/").endswith("/login"):
        logging.info("NETWORK: Session expired — re-logging in.")
        try:
            login(page)
        except Exception as e:
            logging.error(f"NETWORK: Re-login failed: {e}")
            return False

    # Re-navigate to the payroll view.
    try:
        navigate_to_payroll(page)
    except Exception as e:
        logging.error(f"NETWORK: Re-navigation to payroll failed: {e}")
        return False

    # Re-apply date + status filters.
    if target_dt is not None:
        try:
            _setup_view_and_filters(page, target_dt)
        except Exception as e:
            logging.warning(f"NETWORK: Could not re-apply filters after recovery: {e}")

    # Re-select the current employee so the worker_filter locator resolves again.
    if emp_filter_name:
        try:
            _filter_grid_by_employee(page, emp_filter_name)
        except Exception as e:
            logging.warning(
                f"NETWORK: Could not re-select employee '{emp_filter_name}' after recovery: {e}"
            )

    logging.info("NETWORK: Session restored — resuming automation.")
    return True


# ===========================================================================
# Thread management
# ===========================================================================

def run_playwright_automation(log_text_widget, username: str, password: str,
                              start_button, stop_button,
                              digest_widget=None) -> None:
    global USERNAME, PASSWORD, AUTOMATION_STOP_FLAG
    USERNAME = username
    PASSWORD = password

    # Remove only the plain console StreamHandler and any previous TkinterLogHandler.
    # Must NOT use isinstance(h, StreamHandler) because FileHandler is a subclass of
    # StreamHandler — that check would silently remove _file_handler and kill file logging.
    for handler in logging.root.handlers[:]:
        if type(handler) is logging.StreamHandler or isinstance(handler, TkinterLogHandler):
            logging.root.removeHandler(handler)

    tkinter_handler = TkinterLogHandler(log_text_widget)
    tkinter_handler.setFormatter(_log_formatter)   # timestamps in the UI match the file
    logging.root.addHandler(tkinter_handler)

    logging.info("=" * 60)
    logging.info(f"AUTOMATION RUN STARTED: {dt.now().strftime('%Y-%m-%d %H:%M:%S')}")
    logging.info("=" * 60)

    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=False, channel="chrome", args=["--start-maximized", "--no-sandbox"])
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

        try:
            from task_classifier import retrain_from_log
            retrain_from_log()
        except Exception:
            pass

        if digest_widget is not None:
            def _update_digest(text: str) -> None:
                digest_widget.configure(state="normal")
                digest_widget.delete(1.0, ctk.END)
                digest_widget.insert(ctk.END, text)
                digest_widget.configure(state="disabled")

            def _run_digest() -> None:
                digest_widget.after(0, lambda: _update_digest("Analyzing run log with AI..."))
                result = generate_digest()
                digest_widget.after(0, lambda: _update_digest(result))

            threading.Thread(target=_run_digest, daemon=True).start()


def start_automation_thread(log_text_widget, username_entry, password_entry,
                            save_creds_var, start_button, stop_button,
                            digest_widget=None) -> None:
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
        args=(log_text_widget, username, password, start_button, stop_button, digest_widget),
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
    root.geometry("800x860")
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
            save_creds_var, start_button, stop_button, digest_text_widget
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

    # --- AI Analysis frame ---
    digest_frame = ctk.CTkFrame(root, fg_color=BS_GRAY_200, corner_radius=10)
    digest_frame.pack(pady=(0, 10), padx=10, fill=ctk.X)

    ctk.CTkLabel(
        digest_frame, text="AI Run Analysis", text_color=BS_GRAY_900,
    ).pack(pady=(6, 2))

    digest_text_widget = ctk.CTkTextbox(
        digest_frame, width=780, height=165,
        fg_color=BS_GRAY_800, text_color=BS_WHITE,
    )
    digest_text_widget.pack(fill=ctk.X, padx=10, pady=(0, 10))
    digest_text_widget.insert(
        ctk.END,
        "AI analysis will appear here after the run completes.\n"
        "Powered by Ollama (llama3.2) — make sure the Ollama desktop app is running.",
    )
    digest_text_widget.configure(state="disabled")

    root.mainloop()


if __name__ == "__main__":
    start_gui_and_automation()
