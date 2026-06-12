"""
Per-employee priority scoring for the ByteCurve Payroll Adjustment Automation.

Maintains a rolling history of each employee's processing outcomes across runs.
Employees with higher recent success rates are sorted to the front of the
processing queue so that the maximum number of clean employees is handled
fully even when a run is cut short.

History is persisted to a local JSON file (employee_history.json) in the
working directory.  Each entry records whether the run completed without a
manual-review flag.  Entries are weighted by recency via exponential decay
so that recent behaviour is more influential than older runs.
"""

import json
import logging
import os
from datetime import date
from typing import Optional

HISTORY_FILE  = "employee_history.json"
DECAY_FACTOR  = 0.85   # weight of each older run relative to the more recent one
MAX_HISTORY   = 20     # cap on stored entries per employee to bound file growth
DEFAULT_SCORE = 0.50   # score for employees with no recorded history


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------

def load_history() -> dict:
    """Returns the full history dict from disk, or {} if the file is absent/corrupt."""
    if not os.path.exists(HISTORY_FILE):
        return {}
    try:
        with open(HISTORY_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        logging.warning(f"SCORER: Could not load history from '{HISTORY_FILE}': {e}")
        return {}


def save_history(history: dict) -> None:
    """Persists the history dict to disk, overwriting the previous file."""
    try:
        with open(HISTORY_FILE, "w", encoding="utf-8") as f:
            json.dump(history, f, indent=2, ensure_ascii=False)
    except Exception as e:
        logging.warning(f"SCORER: Could not save history to '{HISTORY_FILE}': {e}")


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------

def score_employee(name: str, history: dict) -> float:
    """
    Returns a success-probability score in [0, 1] for one employee.

    Uses exponential decay weighting: the most recent run has weight 1.0,
    the one before it has weight DECAY_FACTOR, the one before that
    DECAY_FACTOR^2, and so on.  This means an employee who has always
    succeeded but failed once last run will quickly drop in priority until
    they recover — preventing the automation from endlessly retrying a
    chronically difficult worker first.
    """
    runs = history.get(name, [])
    if not runs:
        return DEFAULT_SCORE

    weight_sum = 0.0
    score_sum  = 0.0
    for i, run in enumerate(reversed(runs)):   # i=0 is the most recent run
        w           = DECAY_FACTOR ** i
        score_sum  += w * (1.0 if run.get("success") else 0.0)
        weight_sum += w

    return score_sum / weight_sum if weight_sum else DEFAULT_SCORE


def sort_employees_by_priority(names: list, history: dict) -> list:
    """
    Returns a copy of *names* sorted by descending success score.

    Python's sort is stable, so employees sharing the same score (including
    all new employees at DEFAULT_SCORE=0.50) keep their original relative
    order from the dropdown.
    """
    sorted_names = sorted(names, key=lambda n: score_employee(n, history), reverse=True)

    top = ", ".join(
        f"{n} ({score_employee(n, history):.2f})" for n in sorted_names[:5]
    )
    logging.info(
        "SCORER: Employee processing order (top 5 by priority score): "
        + top + (" ..." if len(sorted_names) > 5 else "")
    )
    return sorted_names


# ---------------------------------------------------------------------------
# Recording
# ---------------------------------------------------------------------------

def record_outcome(name: str, success: bool, manual_flag: bool,
                   history: dict) -> None:
    """
    Appends today's processing outcome for *name* to the in-memory *history* dict.

    Call save_history() once after the full run to flush all updates to disk
    in a single write rather than one write per employee.

    Args:
        name:        Employee name as it appears in the dropdown (the dict key).
        success:     True when all adjustments completed without a manual-review flag.
        manual_flag: True when at least one task was flagged for human review.
        history:     The in-memory history dict (mutated in-place).
    """
    runs = history.setdefault(name, [])
    runs.append({
        "date":        date.today().isoformat(),
        "success":     success,
        "manual_flag": manual_flag,
    })
    if len(runs) > MAX_HISTORY:
        history[name] = runs[-MAX_HISTORY:]
