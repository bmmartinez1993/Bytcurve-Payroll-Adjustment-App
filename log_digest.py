"""
Post-run AI log digest using the Claude API.

After automation completes this module reads the session log and asks Claude
to summarise outcomes, surface failure patterns, and suggest improvements.
The result is displayed in the "AI Run Analysis" panel in the GUI.

Requires the ANTHROPIC_API_KEY environment variable to be set, and the
'anthropic' package to be installed (pip install anthropic).
"""

import logging
import os
from typing import Optional

LOG_FILE      = os.path.join("logs", "automation_activity.log")
MAX_LOG_CHARS = 20_000   # trim very long logs to stay within token budget


# ---------------------------------------------------------------------------
# Log reading
# ---------------------------------------------------------------------------

def _read_log(max_chars: int = MAX_LOG_CHARS) -> str:
    """Reads the session log, trimming from the top if it exceeds *max_chars*."""
    try:
        with open(LOG_FILE, "r", encoding="utf-8") as f:
            content = f.read()
        if len(content) > max_chars:
            content = "...[earlier portion trimmed]...\n" + content[-max_chars:]
        return content
    except Exception as e:
        return f"[Could not read log file '{LOG_FILE}': {e}]"


# ---------------------------------------------------------------------------
# Digest generation
# ---------------------------------------------------------------------------

def generate_digest(api_key: Optional[str] = None) -> str:
    """
    Calls the Claude claude-sonnet-4-6 API to analyse the session log.

    Args:
        api_key: Anthropic API key.  Falls back to the ANTHROPIC_API_KEY
                 environment variable if not supplied.

    Returns:
        A formatted multi-section analysis string, or a human-readable
        error message if the API is unavailable.
    """
    key = api_key or os.environ.get("ANTHROPIC_API_KEY", "")
    if not key:
        return (
            "AI analysis unavailable — API key not configured.\n\n"
            "Set the ANTHROPIC_API_KEY environment variable to enable this feature.\n"
            "Example (Windows, current session):\n"
            "  $env:ANTHROPIC_API_KEY = 'sk-ant-...'"
        )

    try:
        import anthropic
    except ImportError:
        return (
            "AI analysis unavailable — 'anthropic' package not installed.\n\n"
            "Run the following command and restart the app:\n"
            "  pip install anthropic"
        )

    log_content = _read_log()

    prompt = f"""You are a quality-assurance analyst reviewing the run log of the
ByteCurve Payroll Adjustment Automation app.

The app iterates through employees listed in a payroll portal, adjusts their
paid start/end times according to task-type rules, resolves scheduling
conflicts, and marks tasks as verified.

Key log markers to look for:
  WORKER:          employee processing started
  SCORER:          employee priority order logged
  STEP4:           all paid-time adjustments complete — employee fully automated
  MANUAL_FLAG:     employee needs human review (automation skipped verification)
  STUCK:           a task exceeded the retry limit and was abandoned
  SAVE_FAIL / ADJUST_FAIL: an individual save or cell-edit failure
  COMPLETE:        full run finished successfully
  STOP:            run was interrupted by the user

Analyse the log below and reply with exactly these four sections.
Use plain text — no markdown bold/italic, no bullet symbols other than a
leading dash (-).  Keep each section concise.

## Summary
One short paragraph: total employees attempted, how many were fully automated
vs. flagged for manual review, and whether the run completed or was stopped.

## Failures & Patterns
Dash-bulleted list.  Name specific employees or task codes that failed,
describe the failure type, and note if any pattern repeats across workers.
If nothing failed, write a single line: "- None detected."

## Recommendations
2 to 4 dash-bulleted actionable points the operator can act on to reduce
manual-review flags or save failures on the next run.

## Health Score
Single line only: "Run health: X/10"
(10 = every employee fully automated without any failure; 0 = all failed.)

---
LOG:
{log_content}"""

    try:
        client = anthropic.Anthropic(api_key=key)
        msg    = client.messages.create(
            model      = "claude-sonnet-4-6",
            max_tokens = 1024,
            messages   = [{"role": "user", "content": prompt}],
        )
        return msg.content[0].text
    except Exception as e:
        logging.error(f"DIGEST: Claude API call failed: {e}")
        return f"AI analysis failed.\n\nError: {e}"
