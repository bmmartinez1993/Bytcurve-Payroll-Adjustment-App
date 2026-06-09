# ByteCurve Payroll Adjustment Automation

Playwright-based automation tool that logs into the ByteCurve 360 portal, validates employee timesheet entries against task-specific policies, adjusts paid time ranges where needed, and bulk-verifies completed records. Runs via a CustomTkinter GUI with live logging and encrypted credential storage.

---

## Project Structure

```
Bytcurve-Payroll-Adjustment-App/
├── ByteCurve Payroll Adjustment Automation.py  # Main script: GUI + orchestration
├── automation_core_refactored.py               # Core library: data models, time utils, grid interactions
├── test_automation_core.py                     # Unit tests for the core library (88 tests)
├── ByteCurve Inspector.py                      # Playwright Inspector launcher for selector debugging
├── diagnose_dialog.py                          # Dialog diagnostics utility
├── requirements.txt                            # Python dependencies
├── .env.example                                # Environment variable template
├── .gitignore
└── credentials.enc / secret.key               # Encrypted credential files (gitignored)
```

---

## Architecture

```
GUI (CustomTkinter)
    └── Automation thread
            ├── login()
            ├── navigate_to_payroll()
            └── validate_and_process_rows()  ← main loop
                    ├── _get_all_employees_from_dropdown()
                    └── for each employee:
                            ├── _filter_grid_by_employee()
                            ├── process_worker_adjustments()   ← automation_core_refactored
                            │       ├── extract_task_data_from_row()
                            │       ├── determine_task_adjustment_needs()
                            │       ├── calculate_proposed_time_range()
                            │       └── adjust_task_time_range()
                            ├── save_task_changes()
                            └── verify_all_worker_tasks()
```

The main script handles browser orchestration, GUI, and credential management. `automation_core_refactored.py` contains all pure-logic functions (time math, overlap resolution, task classification) and is independently unit-testable without a browser.

---

## Prerequisites

- Python 3.8+
- A ByteCurve 360 portal account

---

## Installation

```bash
# 1. Clone or download the repository
# 2. Install Python dependencies
pip install -r requirements.txt

# 3. Install the Chromium browser for Playwright
playwright install chromium
```

---

## Credential Setup

Credentials can be provided two ways. The app prefers encrypted storage over environment variables when both exist.

**Option A — Encrypted file (recommended for repeated use)**

On first launch, enter your credentials in the GUI and check "Save Credentials". The app encrypts them with Fernet and writes `credentials.enc` + `secret.key` to the project directory. These files are gitignored.

**Option B — Environment variable**

Copy `.env.example` to `.env` and fill in your credentials:

```text
BYTECURVE_USER=your_username
BYTECURVE_PASS=your_password
```

> `.env` is gitignored and never committed.

---

## Usage

### Run the main automation

```bash
python "ByteCurve Payroll Adjustment Automation.py"
```

A GUI window opens. Enter credentials (or load saved ones), then click **Start**. The automation will:

1. Log into ByteCurve 360 and navigate to **Verify Hours**.
2. Set the view to the previous business day.
3. Iterate over every employee in the dropdown.
4. For each employee, read all task rows from the Kendo grid.
5. Calculate the correct paid time range per task policy (see below).
6. Enter adjusted times into the grid and save.
7. Check all verification checkboxes and click **Verify**.

Click **Stop** at any time for a graceful shutdown after the current employee finishes.

### Debug selectors with the Inspector

```bash
python "ByteCurve Inspector.py"
```

Opens Playwright Inspector with the portal pre-loaded and logged in, letting you interactively identify CSS selectors.

### Diagnose dialog issues

```bash
python diagnose_dialog.py
```

Runs targeted checks against confirmation/save dialogs to verify button presence, visibility, and click behaviour.

---

## Running Tests

```bash
python -m pytest test_automation_core.py -v
# or
python test_automation_core.py
```

88 unit tests cover time parsing, interval/overlap logic, data models, task classification, and proposed-time calculation — all without requiring a browser.

---

## Task Adjustment Policies

Each task code maps to a `TaskPolicy` that determines how the paid time range is calculated:

| Policy | Trigger keywords | Paid time rule |
|---|---|---|
| `EXTRA_WORK` | "Extra Work" | Schedule start → +1 minute |
| `S2S_CHARTER` | "S2S Charter" | Schedule start → +1 minute |
| `SPARE_CDL` | "Spare CDL" | Exact schedule time |
| `SPARE_MONITOR` | "Spare Monitor" | Exact schedule time |
| `HTS_UNITS` | task name contains "Units" | Exact schedule time |
| `HTS_HOURS` | task name contains "Hrs"/"Hours" | Exact schedule time |
| `REGULAR` | everything else | Actual time, bounded by schedule |
| *(skip)* | "Bridge Charter" | Not processed |

When multiple tasks for the same employee would produce overlapping paid intervals, the automation cascades the later task's start time to `previous_end + 1 minute`, preserving duration. If the required shift exceeds `MAX_TIME_SHIFT_MINUTES` (15 min), the task is flagged for manual review.

---

## Configuration Reference

Key constants in `automation_core_refactored.py`:

| Constant | Default | Description |
|---|---|---|
| `MAX_RETRY_ATTEMPTS` | 3 | Retries per stuck task |
| `MAX_TIME_SHIFT_MINUTES` | 15 | Max allowed cascade shift before manual flag |
| `TIME_ENTRY_TIMEOUT_MS` | 10 000 | Timeout for Kendo timepicker operations |
| `COL_PAID_START` | 10 | Kendo grid column index for paid start |
| `COL_PAID_END` | 11 | Kendo grid column index for paid end |

The portal URL and all CSS/Kendo selectors are defined in the `BYTECURVE_URL` and `SELECTORS` dict at the top of the main automation script.
