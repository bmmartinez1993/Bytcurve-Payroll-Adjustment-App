"""
ByteCurve Payroll Adjustment Automation - Refactored Core Functions

This module contains the refactored core automation functions with improved:
- Error handling and recovery
- Code readability with comprehensive comments
- Type safety using dataclasses
- Separation of concerns
- Unit testability
"""

import datetime
from datetime import datetime as dt
from dataclasses import dataclass, field
from typing import Optional, List, Tuple, Dict, Callable
from enum import Enum
import logging
import re
from playwright.sync_api import Page, Locator

# ============================================================================
# Data Models
# ============================================================================

@dataclass
class TimeRange:
    """
    Represents a time interval with start and end times.

    Attributes:
        start_dt: The start datetime of the interval (or None if invalid)
        end_dt: The end datetime of the interval (or None if invalid)
        start_str: The start time as a string in "HH:MM AM/PM" format
        end_str: The end time as a string in "HH:MM AM/PM" format
    """
    start_dt: Optional[dt] = None
    end_dt: Optional[dt] = None
    start_str: str = ""
    end_str: str = ""

    @property
    def duration_minutes(self) -> Optional[float]:
        """Returns the duration of this time range in minutes."""
        if self.start_dt and self.end_dt:
            return (self.end_dt - self.start_dt).total_seconds() / 60
        return None

    @property
    def is_valid(self) -> bool:
        """Returns True if both start and end datetimes are valid."""
        return self.start_dt is not None and self.end_dt is not None


@dataclass
class TaskInfo:
    """
    Represents a single task entry in the payroll grid.

    This dataclass encapsulates all information about a task that needs to be
    processed, including its current state and proposed adjustments.

    Attributes:
        row: The Playwright Locator for the table row containing this task
        verified: Whether this task has already been verified
        code: The task code (e.g., "HTS", "Extra Work")
        name: The task name (e.g., "Home to School")
        paid_time: The currently paid time range
        schedule_time: The scheduled time range
        actual_time: The actual worked time range
        proposed_time: The proposed time range for adjustment
        requires_adjustment: True if this task needs time adjustment
        skip_reason: If not None, contains a reason why this task was skipped
    """
    row: Locator
    verified: bool = False
    code: str = ""
    name: str = ""
    paid_time: TimeRange = field(default_factory=TimeRange)
    schedule_time: TimeRange = field(default_factory=TimeRange)
    actual_time: TimeRange = field(default_factory=TimeRange)
    proposed_time: TimeRange = field(default_factory=TimeRange)
    requires_adjustment: bool = False
    skip_reason: Optional[str] = None

    @property
    def task_identifier(self) -> str:
        """Returns a unique identifier for this task."""
        return f"{self.code}_{self.name}"


@dataclass
class WorkerInfo:
    """
    Represents a worker (employee) and their associated tasks.

    Attributes:
        worker_id: The unique employee ID
        worker_name: The employee's display name
        display_name: Formatted display name including ID if available
        tasks: List of TaskInfo objects for this worker
        has_manual_intervention: True if any task requires manual review
        is_fully_processed: True if all tasks for this worker are processed
    """
    worker_id: str = ""
    worker_name: str = ""
    display_name: str = ""
    tasks: List[TaskInfo] = field(default_factory=list)
    has_manual_intervention: bool = False
    is_fully_processed: bool = False

    def add_task(self, task: TaskInfo) -> None:
        """Add a task to this worker's task list."""
        self.tasks.append(task)

    def get_verified_tasks(self) -> List[TaskInfo]:
        """Return all tasks that are already verified."""
        return [task for task in self.tasks if task.verified]

    def get_unverified_tasks(self) -> List[TaskInfo]:
        """Return all tasks that need verification."""
        return [task for task in self.tasks if not task.verified]

    def get_tasks_needing_adjustment(self) -> List[TaskInfo]:
        """Return all tasks that require time adjustment."""
        return [task for task in self.tasks if task.requires_adjustment]

    def get_fixed_intervals(self, reference_date: dt) -> List[Tuple[dt, dt]]:
        """
        Returns a list of time intervals from verified tasks.
        These represent "fixed" times that cannot be changed.
        """
        intervals = []
        for task in self.tasks:
            if task.verified and task.paid_time.is_valid:
                start_dt = parse_time_to_datetime(task.paid_time.start_str, reference_date)
                end_dt = parse_time_to_datetime(task.paid_time.end_str, reference_date)
                if start_dt and end_dt:
                    intervals.append((start_dt, end_dt))
        return intervals


class TaskPolicy(Enum):
    """
    Enumeration of task types with their specific adjustment policies.

    Each task type has rules for:
    - Maximum allowed adjustment duration (in hours)
    - Whether the schedule time must match the actual time
    - Whether only a 1-minute entry is allowed (for special tasks)
    """
    EXTRA_WORK = "Extra Work"
    S2S_CHARTER = "S2S Charter"
    SPARE_CDL = "Spare CDL"
    SPARE_MONITOR = "Spare Monitor"
    HTS_UNITS = "HTS Units"
    HTS_HOURS = "HTS Hours"
    REGULAR = "Regular"

    @property
    def max_allowed_hours(self) -> float:
        """Returns the maximum allowed adjustment duration in hours."""
        if self in (TaskPolicy.EXTRA_WORK, TaskPolicy.S2S_CHARTER):
            return 0.016666666666666666  # 1 minute
        return 4.0

    @property
    def require_schedule_match(self) -> bool:
        """Whether schedule time must match paid time exactly."""
        return self in (TaskPolicy.SPARE_CDL, TaskPolicy.SPARE_MONITOR)

    @property
    def is_one_minute_only(self) -> bool:
        """Whether this task type should only have a 1-minute duration."""
        return self in (TaskPolicy.EXTRA_WORK, TaskPolicy.S2S_CHARTER)


# ============================================================================
# Constants and Configuration
# ============================================================================

# Column indices for the Kendo grid
COL_PAID_START = 10  # Column index for Paid Start time
COL_PAID_END = 11    # Column index for Paid End time

# Time-related thresholds
MAX_RETRY_ATTEMPTS = 3  # Maximum retry attempts for a stuck task
MAX_TIME_SHIFT_MINUTES = 15  # Maximum minutes a task can be shifted before requiring manual intervention
TIME_ENTRY_TIMEOUT_MS = 10000  # Timeout for time entry operations

# Task type keywords for classification
ONE_MINUTE_TASK_KEYWORDS = ["Extra Work", "S2S Charter"]
SCHEDULE_MATCH_TASK_KEYWORDS = ["Spare CDL", "Spare Monitor"]
HTS_TASK_KEYWORDS = ["Units", "Hrs", "Hours"]
SKIP_TASK_KEYWORDS = ["Bridge Charter", "BridgeCharter"]


# ============================================================================
# Time Parsing and Formatting Utilities
# ============================================================================

def parse_kendo_time(time_str: str) -> str:
    """
    Cleans up and normalizes Kendo grid time strings.

    Kendo grid may return time strings with inconsistent formatting like:
    - ' 6:39 AM ' (with leading/trailing spaces)
    - '6:39 AM' (no leading zero)
    - '06:39 AM' (properly formatted)

    This function standardizes all inputs to 'HH:MM AM/PM' format.

    Args:
        time_str: The raw time string from the Kendo grid

    Returns:
        Normalized time string in 'HH:MM AM/PM' format

    Examples:
        >>> parse_kendo_time(' 6:39 AM ')
        '06:39 AM'
        >>> parse_kendo_time('12:05 PM')
        '12:05 PM'
    """
    if not time_str:
        return ""

    # Remove leading/trailing whitespace and normalize
    original = time_str.strip()
    parts = original.split()
    if not parts or len(parts) < 2:
        return original  # Return original if it doesn't look like a time with AM/PM

    # Separate time value from AM/PM indicator
    time_val = parts[0]
    meridiem = parts[1]

    # Standardize to HH:mm format (ensure leading zero for consistent string comparison)
    if ":" in time_val:
        try:
            hour, minute = time_val.split(":")
            time_val = f"{int(hour):02d}:{minute}"
        except (ValueError, TypeError):
            pass  # Keep original if parsing fails

    return f"{time_val} {meridiem}"


def times_match(t1: str, t2: str) -> bool:
    """
    Intelligently compares two time strings regardless of leading zeros or whitespace.

    This function normalizes both time strings using parse_kendo_time() and
    performs a case-insensitive comparison.

    Args:
        t1: First time string to compare
        t2: Second time string to compare

    Returns:
        True if the normalized time strings match, False otherwise

    Examples:
        >>> times_match(' 6:39 AM ', '06:39 AM')
        True
        >>> times_match('12:05 PM', '12:05 pm')
        True
        >>> times_match('9:00 AM', '9:30 AM')
        False
    """
    clean_t1 = parse_kendo_time(t1 or "").lower().strip()
    clean_t2 = parse_kendo_time(t2 or "").lower().strip()

    if not clean_t1 or not clean_t2:
        return clean_t1 == clean_t2

    return clean_t1 == clean_t2


def parse_time_to_datetime(time_str: str, ref_date: Optional[dt] = None) -> Optional[dt]:
    """
    Converts a time string (e.g., '06:39 AM') to a datetime object.

    This function parses a time string and combines it with a reference date
    to create a full datetime object. The reference date provides the year,
    month, and day.

    Args:
        time_str: Time string in 'HH:MM AM/PM' format
        ref_date: Reference datetime for the date portion (year, month, day)

    Returns:
        Datetime object with the time from time_str and date from ref_date,
        or None if parsing fails

    Examples:
        >>> ref = dt(2026, 6, 4)
        >>> parse_time_to_datetime('06:39 AM', ref)
        datetime.datetime(2026, 6, 4, 6, 39)
    """
    if not time_str or not ref_date:
        return None

    try:
        # Parse the time portion
        time_obj = dt.strptime(time_str.strip(), "%I:%M %p")
        # Combine with reference date
        return ref_date.replace(hour=time_obj.hour, minute=time_obj.minute)
    except (ValueError, TypeError, AttributeError):
        return None


def add_minutes_to_time(time_str: str, minutes: int) -> str:
    """
    Adds specified minutes to a time string and returns the new time string.

    This function parses the time string, adds the specified minutes,
    and returns the result as a formatted time string.

    Args:
        time_str: Original time string in 'HH:MM AM/PM' format
        minutes: Number of minutes to add (can be negative)

    Returns:
        New time string in 'HH:MM AM/PM' format, or original if parsing fails

    Examples:
        >>> add_minutes_to_time('06:39 AM', 30)
        '07:09 AM'
        >>> add_minutes_to_time('11:45 PM', 30)
        '12:15 AM'
    """
    try:
        time_obj = dt.strptime(time_str.strip(), "%I:%M %p")
        new_time = time_obj + datetime.timedelta(minutes=minutes)
        return new_time.strftime("%I:%M %p")
    except (ValueError, TypeError, AttributeError):
        return time_str


def datetime_to_time_str(dt_obj: Optional[dt]) -> str:
    """
    Converts a datetime object to a time string in 'HH:MM AM/PM' format.

    Args:
        dt_obj: Datetime object to convert

    Returns:
        Time string in 'HH:MM AM/PM' format, or empty string if dt_obj is None
    """
    if dt_obj is None:
        return ""
    return dt_obj.strftime("%I:%M %p")


def parse_time_range_str(range_str: str) -> List[str]:
    """
    Parses a time range string like '06:39 AM - 08:15 PM' into a list of strings.

    Args:
        range_str: Time range string separated by a dash

    Returns:
        List of time strings [start_time, end_time], or empty list if parsing fails
    """
    if not range_str:
        return []

    return [x.strip() for x in range_str.split('-') if x.strip()]


# ============================================================================
# Time Interval and Overlap Utilities
# ============================================================================

def intervals_overlap(start1: Optional[dt], end1: Optional[dt],
                     start2: Optional[dt], end2: Optional[dt]) -> bool:
    """
    Checks if two time intervals overlap.

    Two intervals overlap if:
    - Both have valid start and end times
    - The end of the first is after the start of the second
    - The end of the second is after the start of the first

    Args:
        start1: Start datetime of first interval
        end1: End datetime of first interval
        start2: Start datetime of second interval
        end2: End datetime of second interval

    Returns:
        True if intervals overlap, False otherwise

    Examples:
        >>> start1 = dt(2026, 6, 4, 6, 0)
        >>> end1 = dt(2026, 6, 4, 8, 0)
        >>> start2 = dt(2026, 6, 4, 7, 0)
        >>> end2 = dt(2026, 6, 4, 9, 0)
        >>> intervals_overlap(start1, end1, start2, end2)
        True
    """
    if not all([start1, end1, start2, end2]):
        return False

    # Intervals overlap if: (end1 > start2) AND (end2 > start1)
    return end1 > start2 and end2 > start1


def check_for_overlaps(intervals: List[Tuple[Optional[dt], Optional[dt]]],
                       min_overlap_minutes: int = 15) -> bool:
    """
    Checks if any time intervals overlap by at least the specified duration.

    This function sorts intervals by start time and checks adjacent intervals
    for overlaps that meet or exceed the minimum duration threshold.

    Args:
        intervals: List of tuples (start_datetime, end_datetime)
        min_overlap_minutes: Minimum overlap duration in minutes to report as overlapping

    Returns:
        True if any overlap of at least min_overlap_minutes is found, False otherwise
    """
    if len(intervals) < 2:
        return False

    # Filter out invalid intervals and sort by start time
    sorted_intervals = sorted(
        [i for i in intervals if i[0] and i[1]],
        key=lambda x: x[0]  # type: ignore
    )

    for i in range(len(sorted_intervals) - 1):
        current_end = sorted_intervals[i][1]
        next_start = sorted_intervals[i + 1][0]

        if current_end > next_start:  # An overlap exists
            overlap_duration = current_end - next_start
            overlap_minutes = overlap_duration.total_seconds() / 60

            if overlap_minutes >= min_overlap_minutes:
                return True

    return False


def get_non_overlapping_interval(
    proposed_start: Optional[dt],
    proposed_end: Optional[dt],
    existing_intervals: List[Tuple[Optional[dt], Optional[dt]]],
    gap_minutes: int = 1
) -> Tuple[Optional[dt], Optional[dt]]:
    """
    Adjusts a proposed interval to avoid overlaps with existing intervals.

    If an overlap is found, this function shifts the proposed interval to start
    after the latest overlapping interval ends, with a specified gap.

    The function iteratively shifts the interval until no overlaps remain,
    creating a cascading effect if multiple overlaps exist.

    Args:
        proposed_start: The proposed start datetime
        proposed_end: The proposed end datetime
        existing_intervals: List of existing (start, end) tuples to avoid
        gap_minutes: Minutes of gap to add between intervals (default: 1)

    Returns:
        Tuple of (adjusted_start, adjusted_end) datetimes that do not overlap

    Examples:
        >>> existing = [(dt(2026, 6, 4, 6, 0), dt(2026, 6, 4, 7, 0))]
        >>> proposed = dt(2026, 6, 4, 6, 30)
        >>> proposed_end = dt(2026, 6, 4, 7, 30)
        >>> get_non_overlapping_interval(proposed, proposed_end, existing)
        (datetime(2026, 6, 4, 7, 1), datetime(2026, 6, 4, 8, 1))
    """
    if not proposed_start or not proposed_end:
        return proposed_start, proposed_end

    current_start = proposed_start
    current_end = proposed_end
    original_duration = current_end - current_start

    # Iteratively find a non-overlapping position
    max_iterations = 100  # Prevent infinite loops
    iteration = 0

    while iteration < max_iterations:
        overlap_found = False
        latest_overlapping_end = current_start

        # Check against all existing intervals
        for existing_start, existing_end in existing_intervals:
            if not existing_start or not existing_end:
                continue

            if intervals_overlap(current_start, current_end, existing_start, existing_end):
                overlap_found = True
                latest_overlapping_end = max(latest_overlapping_end, existing_end)

        if not overlap_found:
            break  # No overlaps found, current interval is valid

        # Shift interval to after the latest overlapping end with gap
        current_start = latest_overlapping_end + datetime.timedelta(minutes=gap_minutes)
        current_end = current_start + original_duration

        logging.info(
            f"OVERLAP_RESOLUTION: Shifting interval to "
            f"{datetime_to_time_str(current_start)} - {datetime_to_time_str(current_end)} "
            f"to resolve overlap."
        )
        iteration += 1

    if iteration >= max_iterations:
        logging.warning(f"OVERLAP_RESOLUTION: Max iterations reached, interval may still overlap")

    return current_start, current_end


def calculate_coverage_percentage(
    actual_start: Optional[dt],
    actual_end: Optional[dt],
    sched_start: Optional[dt],
    sched_end: Optional[dt]
) -> float:
    """
    Calculates the percentage of scheduled time covered by actual time.

    This is useful for determining how much of a scheduled shift was actually
    worked. The result is capped at 100%.

    Args:
        actual_start: Start of actual worked time
        actual_end: End of actual worked time
        sched_start: Start of scheduled time
        sched_end: End of scheduled time

    Returns:
        Percentage of scheduled time covered (0-100), or 0 if invalid inputs

    Examples:
        >>> sched_start = dt(2026, 6, 4, 6, 0)
        >>> sched_end = dt(2026, 6, 4, 8, 0)
        >>> actual_start = dt(2026, 6, 4, 6, 30)
        >>> actual_end = dt(2026, 6, 4, 7, 30)
        >>> calculate_coverage_percentage(actual_start, actual_end, sched_start, sched_end)
        50.0
    """
    if not all([actual_start, actual_end, sched_start, sched_end]):
        return 0.0

    # Calculate the overlap between actual and scheduled times
    overlap_start = max(actual_start, sched_start)
    overlap_end = min(actual_end, sched_end)

    if overlap_end <= overlap_start:
        return 0.0  # No overlap

    # Calculate durations in minutes
    overlap_duration = (overlap_end - overlap_start).total_seconds() / 60
    scheduled_duration = (sched_end - sched_start).total_seconds() / 60

    if scheduled_duration == 0:
        return 0.0

    # Calculate and cap percentage at 100
    coverage = (overlap_duration / scheduled_duration) * 100
    return min(coverage, 100.0)


# ============================================================================
# Task Classification and Policy Application
# ============================================================================

def determine_task_policy(task_code: str, task_name: str) -> Optional[TaskPolicy]:
    """
    Determines the appropriate policy for a task based on its code and name.

    Task policies determine how tasks should be adjusted, including:
    - Maximum allowed adjustment duration
    - Whether schedule time must match actual time
    - Whether only 1-minute entries are allowed

    Args:
        task_code: The task code (e.g., "HTS", "Extra Work")
        task_name: The task name (e.g., "Home to School")

    Returns:
        TaskPolicy enum value, or None if task should be skipped
    """
    combined_text = f"{task_code} {task_name}".lower()

    # Check for tasks that should be skipped entirely
    for skip_keyword in SKIP_TASK_KEYWORDS:
        if skip_keyword.lower() in combined_text:
            return None  # Skip this task

    # Check for one-minute tasks (Extra Work, S2S Charter)
    for keyword in ONE_MINUTE_TASK_KEYWORDS:
        if keyword.lower() in task_code.lower():
            return TaskPolicy.EXTRA_WORK if keyword == "Extra Work" else TaskPolicy.S2S_CHARTER

    # Check for schedule-match tasks (Spare CDL, Spare Monitor)
    for keyword in SCHEDULE_MATCH_TASK_KEYWORDS:
        if keyword.lower() in combined_text:
            return TaskPolicy.SPARE_CDL if "CDL" in keyword else TaskPolicy.SPARE_MONITOR

    # Check for HTS units/hours tasks
    if "HTS" in task_code or "HTS" in task_name:
        for keyword in HTS_TASK_KEYWORDS:
            if keyword.lower() in combined_text:
                return TaskPolicy.HTS_UNITS if "units" in keyword.lower() else TaskPolicy.HTS_HOURS

    # Default to regular task
    return TaskPolicy.REGULAR


def calculate_proposed_time_range(
    task_code: str,
    task_name: str,
    schedule_range: TimeRange,
    actual_range: TimeRange,
    reference_date: dt
) -> Optional[Tuple[dt, dt]]:
    """
    Calculates the proposed time range for a task based on its policy.

    This function applies the task-specific policy to determine what the
    paid time should be for the task.

    Args:
        task_code: The task code
        task_name: The task name
        schedule_range: The scheduled time range
        actual_range: The actual worked time range
        reference_date: Reference datetime for date portion

    Returns:
        Tuple of (proposed_start, proposed_end) datetimes, or None if cannot calculate
    """
    # Determine the task policy
    policy = determine_task_policy(task_code, task_name)
    if policy is None:
        return None  # Task should be skipped

    # Validate schedule range
    if not schedule_range.is_valid:
        return None

    sched_start_dt = schedule_range.start_dt
    sched_end_dt = schedule_range.end_dt

    # Calculate based on policy type
    if policy.is_one_minute_only:
        # Extra Work and S2S Charter: 1-minute entry at schedule start
        return sched_start_dt, sched_start_dt + datetime.timedelta(minutes=1)

    elif policy in (TaskPolicy.SPARE_CDL, TaskPolicy.SPARE_MONITOR, TaskPolicy.HTS_UNITS, TaskPolicy.HTS_HOURS):
        # Use exact schedule time
        return sched_start_dt, sched_end_dt

    else:  # REGULAR task
        # Use actual time bounded by schedule time
        actual_start = actual_range.start_dt or sched_start_dt
        actual_end = actual_range.end_dt or sched_end_dt

        # Bound actual time by schedule time
        proposed_start = max(actual_start, sched_start_dt)
        proposed_end = min(actual_end, sched_end_dt)

        return proposed_start, proposed_end


# ============================================================================
# UI Interaction Utilities
# ============================================================================

def ensure_row_visible(page: Page, row: Locator, grid_selector: str,
                       scroll_offset: int = 100) -> bool:
    """
    Ensures that a row is fully visible in the virtualized grid by scrolling.

    Kendo grids often use virtualization where not all rows are rendered.
    This function scrolls the grid to ensure the target row is visible
    before attempting to interact with it.

    Args:
        page: Playwright Page object
        row: Locator for the target row
        grid_selector: CSS selector for the grid scroll container
        scroll_offset: Offset from top of row to center it (default: 100px)

    Returns:
        True if row was successfully made visible, False otherwise
    """
    try:
        # Get the row's offset position
        row_top = row.evaluate("el => el.offsetTop")

        # Scroll the container to position the row
        scroll_container = page.locator(grid_selector)
        scroll_container.evaluate(f"el => el.scrollTop = {row_top} - {scroll_offset}")

        # Allow scroll to complete
        page.wait_for_timeout(200)

        return True
    except Exception as e:
        logging.error(f"SCROLL: Failed to make row visible: {e}")
        return False


def activate_timepicker_cell(page: Page, cell: Locator, max_retries: int = 2) -> Optional[Locator]:
    """
    Activates a timepicker cell by double-clicking it.

    Kendo timepicker cells require activation (double-click) to show the
    editable input field. This function handles the activation with retry logic.

    Args:
        page: Playwright Page object
        cell: Locator for the target cell
        max_retries: Maximum number of activation attempts

    Returns:
        Locator for the input field if activation succeeded, None otherwise
    """
    input_field = cell.locator("input")

    # If input is already present and visible, don't double-click
    if input_field.count() > 0 and input_field.first.is_visible():
        return input_field.first

    for attempt in range(max_retries):
        try:
            logging.info(f"TIMEPICKER: Activation attempt {attempt + 1}/{max_retries}")

            # Double-click to activate the timepicker
            cell.dblclick(timeout=5000)
            page.wait_for_timeout(500)

            # Refresh input field locator after activation
            input_field = cell.locator("input")

            # Wait for input to become visible and editable
            input_field.first.wait_for(state="visible", timeout=3000)

            if not input_field.first.is_editable():
                # Try the "row refocus trick" if input exists but isn't editable
                if attempt < max_retries - 1:
                    logging.warning(f"TIMEPICKER: Input visible but not editable, attempting refocus trick...")
                    if _try_row_refocus_trick(page, cell):
                        continue  # Retry activation

            if input_field.first.is_visible() and input_field.first.is_editable():
                return input_field.first

        except Exception as e:
            logging.warning(f"TIMEPICKER: Activation attempt {attempt + 1} failed: {e}")
            if attempt < max_retries - 1:
                page.wait_for_timeout(500)

    return None


def _try_row_refocus_trick(page: Page, target_cell: Locator) -> bool:
    """
    Attempts to fix a non-editable timepicker by focusing another row and returning.

    Sometimes Kendo grids get into a state where a cell won't become editable.
    Clicking a different cell and returning can reset the grid state.

    Args:
        page: Playwright Page object
        target_cell: The cell that needs to be made editable

    Returns:
        True if refocus trick was attempted, False if not
    """
    try:
        all_rows = page.locator("tbody tr.k-master-row")
        if all_rows.count() <= 1:
            return False

        # Combine both DOM traversals into a single evaluate() call.
        # evaluate() serialises DOM nodes to plain dicts — you cannot chain
        # a second .evaluate() on the return value, so we must do it in one call.
        current_idx = target_cell.evaluate("el => el.closest('tr').sectionRowIndex")
        other_row = all_rows.nth(1 if current_idx == 0 else 0)

        other_row.locator("td[aria-colindex='10']").click(timeout=2000)
        page.wait_for_timeout(200)

        return True
    except Exception as e:
        logging.warning(f"TIMEPICKER: Row refocus trick failed: {e}")
        return False


def enter_time_in_input(page: Page, input_field: Locator, time_value: str) -> bool:
    """
    Enters a time value into an input field with proper clearing and validation.

    This function ensures the field is cleared before typing and handles
    the Kendo timepicker input format requirements.

    Args:
        page: Playwright Page object
        input_field: Locator for the input field
        time_value: Time string to enter (e.g., "06:39 AM")

    Returns:
        True if time was entered successfully, False otherwise
    """
    try:
        # Ensure input is focused
        input_field.click(force=True)
        page.wait_for_timeout(100)

        # Clear existing content (Select All + Backspace)
        page.keyboard.press("Control+A")
        page.keyboard.press("Backspace")
        page.wait_for_timeout(100)

        # Type the new time value with a slight delay for each character
        input_field.type(time_value, delay=50)
        page.wait_for_timeout(300)

        return True
    except Exception as e:
        logging.error(f"TIMEPICKER: Failed to enter time '{time_value}': {e}")
        return False


def commit_time_change(page: Page) -> None:
    """
    Commits a time change by pressing Enter and handling confirmation dialogs.

    After entering a time value, Kendo grids may show a confirmation dialog
    or require Enter to be pressed to save the value.

    Args:
        page: Playwright Page object
    """
    try:
        # Press Enter to commit the change (prevents grid focus issues with Tab)
        page.keyboard.press("Enter")
        page.wait_for_timeout(500)

        # Dismiss any Kendo confirmation modal
        dialog_ok = page.locator(
            "button.k-button-solid-primary, button[data-testid='bulk-update-ok-btn']"
        ).filter(has_text=re.compile("Ok|Yes|Update", re.I))

        if dialog_ok.count() > 0 and dialog_ok.first.is_visible():
            logging.info("DIALOG: Dismissing commit confirmation modal")
            dialog_ok.first.click(timeout=2000)
            page.wait_for_timeout(500)
    except Exception as e:
        logging.warning(f"TIMEPICKER: Issue during commit (non-critical): {e}")


def verify_saved_value(cell: Locator, expected_value: str, timeout_ms: int = 2000) -> bool:
    """
    Verifies that a cell contains the expected value after editing.

    Args:
        cell: Locator for the cell to check
        expected_value: Expected time string value
        timeout_ms: Maximum time to wait for value to settle

    Returns:
        True if cell contains expected value, False otherwise
    """
    try:
        # Wait for cell to settle (grid update to complete)
        page_wait = cell.page.wait_for_timeout if hasattr(cell, 'page') else lambda _: None
        page_wait(300)

        # Get the cell's text content
        saved_value = (cell.text_content(timeout=timeout_ms) or "").strip()

        # Compare using times_match for flexible comparison
        if times_match(saved_value, expected_value):
            return True
        else:
            logging.warning(
                f"VERIFY: Mismatch. Expected '{expected_value}', got '{saved_value}'"
            )
            return False
    except Exception as e:
        logging.error(f"VERIFY: Failed to verify saved value: {e}")
        return False


# ============================================================================
# Core Task Adjustment Functions
# ============================================================================

def adjust_time_entry(
    page: Page,
    row: Locator,
    col_index: int,
    new_time_str: str,
    grid_selector: str = "div.k-grid-content",
    max_retries: int = 2
) -> bool:
    """
    Performs the UI steps to adjust a time entry in the Kendo grid.

    This is the core function that handles the complex interaction with
    Kendo grid timepicker cells. It manages:
    - Row visibility in virtualized grids
    - Cell activation
    - Input field interaction
    - Time value entry
    - Change commitment
    - Save verification

    The function is designed to be robust against timing issues and
    Kendo grid state inconsistencies.

    Args:
        page: Playwright Page object
        row: Locator for the target row
        col_index: Column index (10 for start time, 11 for end time)
        new_time_str: New time value to set (e.g., "06:39 AM")
        grid_selector: CSS selector for the grid scroll container
        max_retries: Maximum retry attempts for the operation

    Returns:
        True if time was successfully adjusted and verified, False otherwise
    """
    scroll_container_selector = f"[data-testid='payload-task-grid'] {grid_selector}"

    try:
        page.wait_for_timeout(200)

        # Step 1: Ensure the row is visible in the virtualized grid
        if not ensure_row_visible(page, row, scroll_container_selector):
            return False

        # Step 2: Locate the target cell
        cell = row.locator(f"td[aria-colindex='{col_index}']")
        cell.wait_for(state="visible", timeout=10000)

        # Step 3: Activate the timepicker (double-click)
        input_field = activate_timepicker_cell(page, cell, max_retries)
        if input_field is None:
            logging.error(f"TIMEPICKER: Failed to activate cell at column {col_index}")
            return False

        logging.info(f"TIMEPICKER: Input field ready for column {col_index}")

        # Step 4: Enter the new time value
        if not enter_time_in_input(page, input_field, new_time_str):
            return False

        # Step 5: Commit the change
        commit_time_change(page)

        # Step 6: Verify the value was saved correctly.
        # Wait for the timepicker input to exit edit mode (detach from the DOM)
        # before reading the cell's display text; otherwise text_content() returns ""
        # because <input> values are not part of DOM text content.
        try:
            cell.locator("input").wait_for(state="detached", timeout=3000)
        except Exception:
            pass  # Input may have already detached or cell structure differs
        page.wait_for_timeout(200)

        saved_value = (cell.text_content(timeout=2000) or "").strip()
        if times_match(saved_value, new_time_str):
            logging.info(f"TIMEPICKER: Successfully saved '{new_time_str}' to column {col_index}")
            return True
        else:
            logging.warning(
                f"VERIFY: Column {col_index} verification failed. "
                f"Expected '{new_time_str}', got '{saved_value}'"
            )
            return False

    except Exception as e:
        logging.error(f"ACTION: Failed to adjust time entry in column {col_index}: {e}")
        return False


def adjust_task_time_range(
    page: Page,
    task: TaskInfo,
    proposed_start: dt,
    proposed_end: dt,
    reference_date: dt
) -> bool:
    """
    Adjusts both start and end times for a task.

    This function adjusts the paid start and end times for a task to match
    the proposed values. It handles both time adjustments and verifies
    the save was successful.

    Args:
        page: Playwright Page object
        task: TaskInfo object containing task details
        proposed_start: Proposed start datetime
        proposed_end: Proposed end datetime
        reference_date: Reference date for context

    Returns:
        True if both adjustments were successful, False otherwise
    """
    # Convert proposed times to strings
    proposed_start_str = datetime_to_time_str(proposed_start)
    proposed_end_str = datetime_to_time_str(proposed_end)

    # Check if adjustments are needed
    needs_start_adjustment = not times_match(task.paid_time.start_str, proposed_start_str)
    needs_end_adjustment = not times_match(task.paid_time.end_str, proposed_end_str)

    if not needs_start_adjustment and not needs_end_adjustment:
        logging.info(
            f"ADJUST: Task {task.task_identifier} already has correct times: "
            f"{proposed_start_str} - {proposed_end_str}"
        )
        return True

    # Adjust start time if needed
    success_start = True
    if needs_start_adjustment:
        logging.info(
            f"ADJUST: Adjusting start time for {task.task_identifier}: "
            f"{task.paid_time.start_str} -> {proposed_start_str}"
        )
        success_start = adjust_time_entry(page, task.row, COL_PAID_START, proposed_start_str)

    # Adjust end time if needed
    success_end = True
    if needs_end_adjustment:
        logging.info(
            f"ADJUST: Adjusting end time for {task.task_identifier}: "
            f"{task.paid_time.end_str} -> {proposed_end_str}"
        )
        success_end = adjust_time_entry(page, task.row, COL_PAID_END, proposed_end_str)

    return success_start and success_end


def save_task_changes(page: Page, task: TaskInfo, save_btn_selector: str,
                     max_wait_sec: int = 3) -> bool:
    """
    Clicks the Save/Update button for a task after making time adjustments.

    After adjusting time values in a row, the Update button must be clicked
    to persist the changes to the server. This function waits for the button
    to become enabled and clicks it.

    Args:
        page: Playwright Page object
        task: TaskInfo object containing the task row
        save_btn_selector: CSS selector for the Save/Update button
        max_wait_sec: Maximum seconds to wait for button to become enabled

    Returns:
        True if save was successfully triggered, False otherwise
    """
    try:
        # Locate the save button in the task row
        save_btn = task.row.locator(save_btn_selector).first
        save_btn.wait_for(state="visible", timeout=5000)

        # Wait for button to become enabled (grid may need to process changes)
        for _ in range(max_wait_sec * 10):  # Check every 100ms
            if save_btn.is_enabled():
                break
            page.wait_for_timeout(100)
        else:
            logging.warning(f"SAVE: Save button did not become enabled within {max_wait_sec}s")
            return False

        # Click the save button
        logging.info(f"SAVE: Clicking Update for {task.task_identifier}. Grid will reset.")
        save_btn.click(force=True)

        # Wait for the page to update (grid will reset/reload)
        page.wait_for_timeout(500)

        return True
    except Exception as e:
        logging.error(f"SAVE: Failed to save changes for {task.task_identifier}: {e}")
        return False


# ============================================================================
# Task Verification Functions
# ============================================================================

def verify_task_checkbox(page: Page, row: Locator, task_code: str,
                         worker_name: str) -> bool:
    """
    Verifies (checks) a task checkbox with safety checks and state confirmation.

    This function checks the current state of a task's verification checkbox,
    clicks it if unchecked, and confirms the state changed successfully.

    Args:
        page: Playwright Page object
        row: Locator for the task row
        task_code: Task code for logging
        worker_name: Worker name for logging

    Returns:
        True if checkbox is now checked (or was already checked), False otherwise
    """
    try:
        # Ensure the grid is ready
        wait_for_loading(page)
        page.wait_for_timeout(500)

        # Scroll the row into view to ensure checkbox isn't clipped
        row.scroll_into_view_if_needed()

        # Locate the checkbox within the row
        checkbox = row.locator("input[kendocheckbox][aria-label='verify']")
        checkbox.wait_for(state="visible", timeout=10000)
        page.wait_for_timeout(200)

        # Check current state before clicking
        was_checked_before = checkbox.is_checked()
        logging.info(
            f"CHECKBOX: {task_code} for {worker_name} - "
            f"{'already checked' if was_checked_before else 'unchecked'}"
        )

        if was_checked_before:
            return True  # Already verified, no action needed

        # Click the checkbox
        checkbox.click(force=True, timeout=5000, delay=100)
        page.wait_for_timeout(600)  # Wait for click to register and state to update

        # Verify the state actually changed
        is_checked_after = checkbox.is_checked()
        if is_checked_after:
            logging.info(f"CHECKBOX: Successfully marked {task_code} for {worker_name}")
            return True
        else:
            logging.warning(f"CHECKBOX: Click did not register for {task_code}")
            return False

    except Exception as e:
        logging.error(f"CHECKBOX: Failed for {task_code} ({worker_name}): {e}")
        return False


def verify_all_worker_tasks(page: Page, worker: WorkerInfo) -> int:
    """
    Verifies all unchecked tasks for a worker.

    This function iterates through all tasks for a worker and checks
    any that are not yet verified.

    Args:
        page: Playwright Page object
        worker: WorkerInfo object containing the worker's tasks

    Returns:
        Number of tasks that were successfully verified
    """
    verified_count = 0
    unverified_tasks = worker.get_unverified_tasks()

    for task in unverified_tasks:
        if verify_task_checkbox(page, task.row, task.code, worker.display_name):
            verified_count += 1

    return verified_count


# ============================================================================
# Worker Processing Functions
# ============================================================================

def extract_task_data_from_row(page: Page, row: Locator,
                               reference_date: dt) -> Optional[TaskInfo]:
    """
    Extracts all relevant data from a row and creates a TaskInfo object.

    This function reads the following from the grid row:
    - Task verification status (checkbox state)
    - Task code and name
    - Paid start/end times
    - Scheduled time range
    - Actual time range

    Args:
        page: Playwright Page object
        row: Locator for the task row
        reference_date: Reference datetime for date context

    Returns:
        TaskInfo object with extracted data, or None if extraction failed
    """
    try:
        # Extract basic task information
        is_verified = row.locator("input[kendocheckbox][aria-label='verify']").is_checked()
        task_code = (row.locator("td[aria-colindex='5']").text_content() or "").strip()
        task_name = (row.locator("td[aria-colindex='3']").text_content() or "").strip()

        # Extract paid times
        paid_start_str = parse_kendo_time(row.locator("td[aria-colindex='10']").text_content() or "")
        paid_end_str = parse_kendo_time(row.locator("td[aria-colindex='11']").text_content() or "")

        # Extract schedule time range
        sched_text = row.locator("td[aria-colindex='6']").text_content() or ""
        sched_range_parts = parse_time_range_str(sched_text)

        # Extract actual time range
        actual_text = row.locator("td[aria-colindex='8']").text_content() or ""
        actual_range_parts = parse_time_range_str(actual_text)

        # Create TimeRange objects
        paid_time = TimeRange(
            start_dt=parse_time_to_datetime(paid_start_str, reference_date),
            end_dt=parse_time_to_datetime(paid_end_str, reference_date),
            start_str=paid_start_str,
            end_str=paid_end_str
        )

        schedule_time = TimeRange(
            start_dt=parse_time_to_datetime(sched_range_parts[0], reference_date) if sched_range_parts else None,
            end_dt=parse_time_to_datetime(sched_range_parts[1], reference_date) if len(sched_range_parts) > 1 else None,
            start_str=sched_range_parts[0] if sched_range_parts else "",
            end_str=sched_range_parts[1] if len(sched_range_parts) > 1 else ""
        )

        actual_time = TimeRange(
            start_dt=parse_time_to_datetime(actual_range_parts[0], reference_date) if actual_range_parts else None,
            end_dt=parse_time_to_datetime(actual_range_parts[1], reference_date) if len(actual_range_parts) > 1 else None,
            start_str=actual_range_parts[0] if actual_range_parts else "",
            end_str=actual_range_parts[1] if len(actual_range_parts) > 1 else ""
        )

        # Create TaskInfo object
        task = TaskInfo(
            row=row,
            verified=is_verified,
            code=task_code,
            name=task_name,
            paid_time=paid_time,
            schedule_time=schedule_time,
            actual_time=actual_time
        )

        return task

    except Exception as e:
        logging.error(f"EXTRACT: Failed to extract task data from row: {e}")
        return None


def determine_task_adjustment_needs(
    task: TaskInfo,
    reference_date: dt,
    existing_intervals: List[Tuple[dt, dt]]
) -> None:
    """
    Determines if a task needs adjustment and what the proposed times should be.

    This function:
    1. Checks if the task should be skipped based on its type
    2. Calculates the proposed time range based on task policy
    3. Resolves any overlaps with existing (verified) intervals
    4. Checks if the time shift exceeds the maximum allowed threshold

    Args:
        task: TaskInfo object to analyze (modified in-place)
        reference_date: Reference datetime for date context
        existing_intervals: List of verified task intervals to avoid overlapping
    """
    # Determine if task should be skipped
    policy = determine_task_policy(task.code, task.name)
    if policy is None:
        task.skip_reason = f"Skip policy match: {task.code} - {task.name}"
        return

    # Calculate proposed times based on policy
    proposed_range = calculate_proposed_time_range(
        task.code,
        task.name,
        task.schedule_time,
        task.actual_time,
        reference_date
    )

    if proposed_range is None:
        task.skip_reason = "Could not calculate proposed times"
        return

    proposed_start, proposed_end = proposed_range

    # Resolve overlaps with existing verified tasks
    final_start, final_end = get_non_overlapping_interval(
        proposed_start,
        proposed_end,
        existing_intervals
    )

    # Check if the shift exceeds maximum allowed threshold
    time_shift_minutes = (final_start - proposed_start).total_seconds() / 60

    if abs(time_shift_minutes) > MAX_TIME_SHIFT_MINUTES:
        task.skip_reason = (
            f"Time shift ({time_shift_minutes:.1f} min) exceeds threshold "
            f"({MAX_TIME_SHIFT_MINUTES} min) - requires manual review"
        )
        return

    # Set the proposed time range
    task.proposed_time = TimeRange(
        start_dt=final_start,
        end_dt=final_end,
        start_str=datetime_to_time_str(final_start),
        end_str=datetime_to_time_str(final_end)
    )

    # Determine if adjustment is needed
    current_start_str = datetime_to_time_str(task.paid_time.start_dt)
    current_end_str = datetime_to_time_str(task.paid_time.end_dt)
    proposed_start_str = task.proposed_time.start_str
    proposed_end_str = task.proposed_time.end_str

    task.requires_adjustment = (
        not times_match(current_start_str, proposed_start_str) or
        not times_match(current_end_str, proposed_end_str)
    )


def process_worker_adjustments(
    page: Page,
    worker: WorkerInfo,
    reference_date: dt,
    save_btn_selector: str = "button.k-grid-save-command, button[title='Update'], button[kendogridsavecommand]",
    retry_limit: int = MAX_RETRY_ATTEMPTS
) -> Tuple[bool, Dict[str, int]]:
    """
    Processes all time adjustments needed for a worker's tasks.

    This function:
    1. Iterates through tasks needing adjustment
    2. Adjusts one task at a time (to avoid grid state issues)
    3. Saves each adjustment before proceeding to the next
    4. Tracks retry attempts for stuck tasks
    5. Returns summary of processing results

    Args:
        page: Playwright Page object
        worker: WorkerInfo object containing tasks to process
        reference_date: Reference datetime for date context
        save_btn_selector: CSS selector for the Save/Update button
        retry_limit: Maximum retry attempts per task

    Returns:
        Tuple of (success, stats) where:
        - success: True if all adjustments were made (or none needed)
        - stats: Dictionary with processing statistics
    """
    stats = {
        'tasks_processed': 0,
        'tasks_adjusted': 0,
        'tasks_skipped': 0,
        'save_failures': 0,
        'tasks_with_errors': 0
    }

    tasks_needing_adjustment = worker.get_tasks_needing_adjustment()

    if not tasks_needing_adjustment:
        logging.info(f"WORKER: No adjustments needed for {worker.display_name}")
        return True, stats

    retry_tracking: Dict[str, int] = {}

    for task in tasks_needing_adjustment:
        task_key = f"{worker.worker_id}_{task.task_identifier}"
        retry_count = retry_tracking.get(task_key, 0)

        if retry_count >= retry_limit:
            logging.error(
                f"WORKER: Task {task.task_identifier} exceeded retry limit "
                f"({retry_limit}). Skipping worker."
            )
            stats['tasks_with_errors'] += 1
            return False, stats

        logging.info(
            f"WORKER: Processing task {task.task_identifier} "
            f"(attempt {retry_count + 1}/{retry_limit})"
        )

        # Adjust the task's time range
        if task.proposed_time.is_valid:
            adjustment_success = adjust_task_time_range(
                page,
                task,
                task.proposed_time.start_dt,
                task.proposed_time.end_dt,
                reference_date
            )

            if adjustment_success:
                stats['tasks_adjusted'] += 1
            else:
                retry_tracking[task_key] = retry_count + 1
                stats['tasks_with_errors'] += 1
                continue
        else:
            logging.warning(f"WORKER: No valid proposed time for {task.task_identifier}")
            retry_tracking[task_key] = retry_count + 1
            continue

        # Save the changes
        if save_task_changes(page, task, save_btn_selector):
            stats['tasks_processed'] += 1
            # Clear retry count on success
            if task_key in retry_tracking:
                del retry_tracking[task_key]
        else:
            stats['save_failures'] += 1
            retry_tracking[task_key] = retry_count + 1

    return stats['tasks_with_errors'] == 0, stats


def wait_for_loading(page: Page, timeout_ms: int = 20000) -> None:
    """
    Waits for the ByteCurve loading overlay to disappear.

    Args:
        page: Playwright Page object
        timeout_ms: Maximum time to wait for loading to complete
    """
    page.wait_for_timeout(500)  # Brief pause to allow any dynamic loader to trigger
    try:
        # Wait for the spinner to be hidden
        page.wait_for_selector(".page-loading", state="hidden", timeout=timeout_ms)
    except Exception:
        pass  # Loading element might not exist or already hidden


# ============================================================================
# End of Refactored Core Functions
# ============================================================================