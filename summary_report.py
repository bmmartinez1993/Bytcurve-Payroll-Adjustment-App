"""
Post-run AI Summary Report using a local Ollama model.

Generates a formatted report for the "Download Report" GUI action, distinct
from the shorter "AI Run Analysis" digest (see log_digest.py). The report is
meant to be saved to disk and shared with stakeholders, so it favors a more
formal structure:

  1. Executive Summary
  2. Issues Found
  3. Recommendations and Insights
  4. Employees Requiring Manual Verification

Requirements:
  - Ollama desktop app running in the background  (https://ollama.com)
  - qwen2.5:7b model pulled:  ollama pull qwen2.5:7b
  - llama3.2 model pulled:  ollama pull llama3.2
  - Python package installed:  pip install ollama
"""

import logging
import os
import re
from datetime import datetime as dt

LOG_FILE      = os.path.join("logs", "automation_activity.log")
MAX_LOG_CHARS = 20_000   # trim very long logs to stay within the model's context
DEFAULT_MODEL = "qwen2.5:7b"  # Ollama model to use for analysis
ALTERNATIVE_MODEL = "llama3.2"  # Alternative Ollama model to use for analysis

_MANUAL_FLAG_RE = re.compile(r"MANUAL_FLAG: Skipping verification for (.+)")


# ---------------------------------------------------------------------------
# Log reading
# ---------------------------------------------------------------------------

def _read_log(max_chars: int = MAX_LOG_CHARS, log_file: str | None = None) -> str:
    """Reads the session log, trimming from the top if it exceeds *max_chars*."""
    if log_file is None:
        log_file = LOG_FILE
    try:
        with open(log_file, "r", encoding="utf-8") as f:
            content = f.read()
        if len(content) > max_chars:
            content = "...[earlier portion trimmed]...\n" + content[-max_chars:]
        return content
    except Exception as e:
        return f"[Could not read log file '{log_file}': {e}]"


def _extract_manual_verification_employees(log_content: str) -> list[str]:
    """
    Pulls the authoritative list of employees flagged for manual verification
    straight from MANUAL_FLAG log lines, preserving first-seen order and
    de-duplicating. This list is compiled programmatically (not by the LLM)
    so the report can't hallucinate or drop names in a payroll-sensitive
    section.
    """
    seen: list[str] = []
    for match in _MANUAL_FLAG_RE.finditer(log_content):
        name = match.group(1).strip()
        if name and name not in seen:
            seen.append(name)
    return seen


def _format_manual_verification_section(employees: list[str]) -> str:
    if not employees:
        return "- None. All employees were fully automated without a manual-review flag."
    return "\n".join(f"- {name}" for name in employees)


# ---------------------------------------------------------------------------
# Report generation
# ---------------------------------------------------------------------------

def generate_summary_report(model: str = DEFAULT_MODEL or ALTERNATIVE_MODEL, log_file: str | None = None) -> str:
    """
    Calls a local Ollama model to build a stakeholder-facing summary report
    from the session log.

    Args:
        model: Ollama model name to use (default: "qwen2.5:7b").
        log_file: Path to the run's log file (defaults to the module constant,
            but the caller should pass the current run's timestamped log path).

    Returns:
        A formatted multi-section report string, or a human-readable error
        message if Ollama is unavailable.
    """
    try:
        import ollama
    except ImportError:
        return (
            "Summary report unavailable — 'ollama' package not installed.\n\n"
            "Run the following command and restart the app:\n"
            "  pip install ollama"
        )

    log_content = _read_log(log_file=log_file)
    manual_verification_employees = _extract_manual_verification_employees(log_content)

    prompt = f"""You are preparing a Summary Report for stakeholders about a completed
run of the ByteCurve Payroll Adjustment Automation app.

The app iterates through employees listed in a payroll portal, adjusts their
paid start/end times according to task-type rules, resolves scheduling
conflicts, and marks tasks as verified.

Operational rules the report must account for:
  1. The primary purpose of this app is to adjust employee schedule times
     strictly according to the task policy currently assigned to each worker.
     Any deviation from the assigned policy is an issue worth flagging.
  2. This automation is designed to run every business day (Monday through
     Friday). Flag any indication that a run was skipped, ran on a weekend,
     or ran more than once in a single business day as a scheduling anomaly.

Key log markers to look for:
  WORKER:          employee processing started
  SCORER:          employee priority order logged
  STEP4:           all paid-time adjustments complete — employee fully automated
  MANUAL_FLAG:     employee needs human review (automation skipped verification)
  STUCK:           a task exceeded the retry limit and was abandoned
  SAVE_FAIL / ADJUST_FAIL: an individual save or cell-edit failure
  HIDDEN_EMPLOYEE: dropdown filter returned empty — employee processed via unfiltered fallback
  COMPLETE:        full run finished successfully
  STOP:            run was interrupted by the user

Analyse the log below and reply with exactly these three sections, in this
order. Use plain text — no markdown bold/italic, no bullet symbols other
than a leading dash (-). Write for a non-technical stakeholder audience;
keep each section concise and business-focused. Do NOT include a section
about employees requiring manual verification — that section is appended
separately from verified data, so leave it out of your reply entirely.

## Executive Summary
One short paragraph: total employees attempted, how many were fully
automated vs. flagged for manual review, whether the run completed or was
stopped, and the overall health of the run in plain language.

## Issues Found
Dash-bulleted list. Name specific employees or task codes that failed,
describe the failure type, and note if any pattern repeats across workers
or any scheduling anomaly occurred. If nothing failed, write a single line:
"- None detected."

## Recommendations and Insights
2 to 4 dash-bulleted actionable points the operator or stakeholders can act
on to reduce manual-review flags or save failures on future runs, plus any
notable trend worth calling out.

---
LOG:
{log_content}"""

    try:
        logging.info(f"REPORT: Sending log to Ollama model '{model}'...")
        response = ollama.chat(
            model    = model,
            messages = [{"role": "user", "content": prompt}],
        )
        ai_sections = response.message.content.strip()
    except Exception as e:
        # Common causes: Ollama service not running, model not pulled.
        err = str(e)
        if "connect" in err.lower() or "connection" in err.lower():
            hint = (
                "Ollama service is not running.\n"
                "Start it via the Ollama desktop app or run: ollama serve"
            )
        elif "not found" in err.lower() or "404" in err:
            hint = (
                f"Model '{model}' is not available locally.\n"
                f"Pull it first by running: ollama pull {model}"
            )
        else:
            hint = f"Error: {e}"
        logging.error(f"REPORT: Ollama call failed: {e}")
        return f"Summary report generation failed.\n\n{hint}"

    manual_verification_section = _format_manual_verification_section(manual_verification_employees)

    header = (
        "ByteCurve Payroll Adjustment Automation — Summary Report\n"
        f"Generated: {dt.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
        + "=" * 60
    )

    return (
        f"{header}\n\n"
        f"{ai_sections}\n\n"
        "## Employees Requiring Manual Verification\n"
        f"{manual_verification_section}\n"
    )
