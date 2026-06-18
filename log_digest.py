"""
Post-run AI log digest using a local Ollama model.

After automation completes this module reads the session log and asks a
local LLM (llama3.2 by default) to summarise outcomes, surface failure
patterns, and suggest improvements.  The result is displayed in the
"AI Run Analysis" panel in the GUI.

Requirements:
  - Ollama desktop app running in the background  (https://ollama.com)
  - llama3.2 model pulled:  ollama pull llama3.2
  - Python package installed:  pip install ollama
"""

import logging
import os

LOG_FILE      = os.path.join("logs", "automation_activity.log")
MAX_LOG_CHARS = 20_000   # trim very long logs to stay within the model's context
DEFAULT_MODEL = "llama3.2"


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

def generate_digest(model: str = DEFAULT_MODEL) -> str:
    """
    Calls a local Ollama model to analyse the session log.

    Args:
        model: Ollama model name to use (default: "llama3.2").

    Returns:
        A formatted multi-section analysis string, or a human-readable
        error message if Ollama is unavailable.
    """
    try:
        import ollama
    except ImportError:
        return (
            "AI analysis unavailable — 'ollama' package not installed.\n\n"
            "Run the following command and restart the app:\n"
            "  pip install ollama"
        )

    log_content = _read_log()

    prompt = f"""You are a quality-assurance analyst reviewing the run log of the
ByteCurve Payroll Adjustment Automation app.

The app iterates through employees listed in a payroll portal, adjusts their
paid start/end times according to task-type rules, resolves scheduling
conflicts, and marks tasks as verified.

Operational rules the analysis must account for:
  1. The primary purpose of this app is to adjust employee schedule times
     strictly according to the task policy currently assigned to each worker.
     Any deviation from the assigned policy is a failure worth flagging.
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
        logging.info(f"DIGEST: Sending log to Ollama model '{model}'...")
        response = ollama.chat(
            model    = model,
            messages = [{"role": "user", "content": prompt}],
        )
        return response.message.content
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
        logging.error(f"DIGEST: Ollama call failed: {e}")
        return f"AI analysis failed.\n\n{hint}"
