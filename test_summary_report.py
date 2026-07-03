"""
Unit tests for summary_report — stakeholder Summary Report generation
behind the GUI's "Download Report" button.

The optional ``ollama`` package is never required to run these tests: a
fake module is injected into ``sys.modules`` to simulate both the
"package not installed" case and successful / failing chat calls.
"""

import os
import shutil
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import summary_report


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _fake_ollama(content: str = None, error: Exception = None) -> MagicMock:
    """Builds a fake ``ollama`` module whose ``chat`` either returns *content*
    wrapped like the real client's response, or raises *error*."""
    mock_module = MagicMock()
    if error is not None:
        mock_module.chat.side_effect = error
    else:
        mock_module.chat.return_value = SimpleNamespace(
            message=SimpleNamespace(content=content)
        )
    return mock_module


# ---------------------------------------------------------------------------
# _read_log
# ---------------------------------------------------------------------------

class TestReadLog(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.log_path = os.path.join(self.tmp, "activity.log")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_reads_full_content_when_under_limit(self):
        with open(self.log_path, "w", encoding="utf-8") as fh:
            fh.write("line one\nline two\n")
        with patch.object(summary_report, "LOG_FILE", self.log_path):
            content = summary_report._read_log(max_chars=1000)
        self.assertEqual(content, "line one\nline two\n")

    def test_trims_from_top_when_over_limit(self):
        body = "x" * 50
        with open(self.log_path, "w", encoding="utf-8") as fh:
            fh.write(body)
        with patch.object(summary_report, "LOG_FILE", self.log_path):
            content = summary_report._read_log(max_chars=10)
        self.assertIn("...[earlier portion trimmed]...", content)
        self.assertTrue(content.endswith("x" * 10))

    def test_missing_file_returns_error_message(self):
        missing_path = os.path.join(self.tmp, "does_not_exist.log")
        with patch.object(summary_report, "LOG_FILE", missing_path):
            content = summary_report._read_log()
        self.assertIn("Could not read log file", content)


# ---------------------------------------------------------------------------
# _extract_manual_verification_employees
# ---------------------------------------------------------------------------

class TestExtractManualVerificationEmployees(unittest.TestCase):
    def test_returns_empty_list_when_no_flags(self):
        log = "WORKER: Processing Jane Doe\nSTEP4: All paid-time adjustments complete for Jane Doe.\n"
        self.assertEqual(summary_report._extract_manual_verification_employees(log), [])

    def test_extracts_single_employee(self):
        log = "MANUAL_FLAG: Skipping verification for John Doe\n"
        self.assertEqual(
            summary_report._extract_manual_verification_employees(log), ["John Doe"]
        )

    def test_dedupes_repeated_flags_preserving_first_seen_order(self):
        log = (
            "MANUAL_FLAG: Skipping verification for John Doe\n"
            "MANUAL_FLAG: Skipping verification for Jane Smith\n"
            "MANUAL_FLAG: Skipping verification for John Doe\n"
        )
        self.assertEqual(
            summary_report._extract_manual_verification_employees(log),
            ["John Doe", "Jane Smith"],
        )

    def test_strips_trailing_whitespace_from_name(self):
        log = "MANUAL_FLAG: Skipping verification for John Doe   \n"
        self.assertEqual(
            summary_report._extract_manual_verification_employees(log), ["John Doe"]
        )


# ---------------------------------------------------------------------------
# _format_manual_verification_section
# ---------------------------------------------------------------------------

class TestFormatManualVerificationSection(unittest.TestCase):
    def test_empty_list_returns_none_detected_message(self):
        result = summary_report._format_manual_verification_section([])
        self.assertIn("None", result)

    def test_formats_each_employee_as_dash_bullet(self):
        result = summary_report._format_manual_verification_section(["John Doe", "Jane Smith"])
        self.assertEqual(result, "- John Doe\n- Jane Smith")


# ---------------------------------------------------------------------------
# generate_summary_report
# ---------------------------------------------------------------------------

class TestGenerateSummaryReport(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.log_path = os.path.join(self.tmp, "activity.log")
        with open(self.log_path, "w", encoding="utf-8") as fh:
            fh.write(
                "WORKER: Processing Jane Doe\n"
                "STEP4: All paid-time adjustments complete for Jane Doe.\n"
                "WORKER: Processing John Smith\n"
                "MANUAL_FLAG: Skipping verification for John Smith\n"
            )
        self._log_file_patch = patch.object(summary_report, "LOG_FILE", self.log_path)
        self._log_file_patch.start()

    def tearDown(self):
        self._log_file_patch.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_ollama_not_installed_returns_helpful_message(self):
        with patch.dict(sys.modules, {"ollama": None}):
            result = summary_report.generate_summary_report()
        self.assertIn("pip install ollama", result)

    def test_successful_report_includes_header_and_ai_sections(self):
        fake = _fake_ollama(content="## Executive Summary\nAll good.")
        with patch.dict(sys.modules, {"ollama": fake}):
            result = summary_report.generate_summary_report(model="qwen2.5:7b")
        self.assertIn("ByteCurve Payroll Adjustment Automation — Summary Report", result)
        self.assertIn("## Executive Summary\nAll good.", result)

    def test_manual_verification_section_built_from_log_not_from_ai_response(self):
        # The AI response deliberately omits any employee names and mentions
        # a name that never appeared in the log — the final report must
        # still list exactly the employees flagged via MANUAL_FLAG, proving
        # that section is not sourced from the model's output.
        fake = _fake_ollama(content="## Executive Summary\nEverything looks fine, no issues.")
        with patch.dict(sys.modules, {"ollama": fake}):
            result = summary_report.generate_summary_report()
        self.assertIn("## Employees Requiring Manual Verification", result)
        self.assertIn("- John Smith", result)
        # Jane Doe was fully automated (STEP4), so she must not appear here.
        section = result.split("## Employees Requiring Manual Verification")[1]
        self.assertNotIn("Jane Doe", section)

    def test_no_manual_flags_reports_none_detected(self):
        with open(self.log_path, "w", encoding="utf-8") as fh:
            fh.write("WORKER: Processing Jane Doe\nSTEP4: All paid-time adjustments complete for Jane Doe.\n")
        fake = _fake_ollama(content="## Executive Summary\nAll clear.")
        with patch.dict(sys.modules, {"ollama": fake}):
            result = summary_report.generate_summary_report()
        section = result.split("## Employees Requiring Manual Verification")[1]
        self.assertIn("None", section)

    def test_prompt_includes_log_content(self):
        fake = _fake_ollama(content="ok")
        with patch.dict(sys.modules, {"ollama": fake}):
            summary_report.generate_summary_report()
        sent_prompt = fake.chat.call_args.kwargs["messages"][0]["content"]
        self.assertIn("John Smith", sent_prompt)

    def test_chat_called_with_requested_model(self):
        fake = _fake_ollama(content="ok")
        with patch.dict(sys.modules, {"ollama": fake}):
            summary_report.generate_summary_report(model="llama3.2")
        self.assertEqual(fake.chat.call_args.kwargs["model"], "llama3.2")

    def test_connection_error_gives_ollama_not_running_hint(self):
        fake = _fake_ollama(error=Exception("Connection refused"))
        with patch.dict(sys.modules, {"ollama": fake}):
            result = summary_report.generate_summary_report()
        self.assertIn("Ollama service is not running", result)

    def test_model_not_found_error_gives_pull_hint(self):
        fake = _fake_ollama(error=Exception("model 'qwen2.5:7b' not found (status code: 404)"))
        with patch.dict(sys.modules, {"ollama": fake}):
            result = summary_report.generate_summary_report(model="qwen2.5:7b")
        self.assertIn("ollama pull qwen2.5:7b", result)

    def test_generic_error_included_in_message(self):
        fake = _fake_ollama(error=Exception("boom"))
        with patch.dict(sys.modules, {"ollama": fake}):
            result = summary_report.generate_summary_report()
        self.assertIn("boom", result)


if __name__ == "__main__":
    unittest.main()
