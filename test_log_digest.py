"""
Unit tests for log_digest — post-run AI log analysis via Ollama.

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

import log_digest


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
        with patch.object(log_digest, "LOG_FILE", self.log_path):
            content = log_digest._read_log(max_chars=1000)
        self.assertEqual(content, "line one\nline two\n")

    def test_trims_from_top_when_over_limit(self):
        body = "x" * 50
        with open(self.log_path, "w", encoding="utf-8") as fh:
            fh.write(body)
        with patch.object(log_digest, "LOG_FILE", self.log_path):
            content = log_digest._read_log(max_chars=10)
        self.assertIn("...[earlier portion trimmed]...", content)
        self.assertTrue(content.endswith("x" * 10))

    def test_missing_file_returns_error_message(self):
        missing_path = os.path.join(self.tmp, "does_not_exist.log")
        with patch.object(log_digest, "LOG_FILE", missing_path):
            content = log_digest._read_log()
        self.assertIn("Could not read log file", content)


# ---------------------------------------------------------------------------
# generate_digest
# ---------------------------------------------------------------------------

class TestGenerateDigest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.log_path = os.path.join(self.tmp, "activity.log")
        with open(self.log_path, "w", encoding="utf-8") as fh:
            fh.write("WORKER: Processing Jane Doe\nSTEP4: All paid-time adjustments complete for Jane Doe.\n")
        self._log_file_patch = patch.object(log_digest, "LOG_FILE", self.log_path)
        self._log_file_patch.start()

    def tearDown(self):
        self._log_file_patch.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_ollama_not_installed_returns_helpful_message(self):
        with patch.dict(sys.modules, {"ollama": None}):
            result = log_digest.generate_digest()
        self.assertIn("pip install ollama", result)

    def test_successful_chat_returns_model_content(self):
        fake = _fake_ollama(content="## Summary\nAll good.")
        with patch.dict(sys.modules, {"ollama": fake}):
            result = log_digest.generate_digest(model="qwen2.5:7b")
        self.assertEqual(result, "## Summary\nAll good.")

    def test_prompt_includes_log_content(self):
        fake = _fake_ollama(content="ok")
        with patch.dict(sys.modules, {"ollama": fake}):
            log_digest.generate_digest()
        sent_prompt = fake.chat.call_args.kwargs["messages"][0]["content"]
        self.assertIn("Jane Doe", sent_prompt)

    def test_chat_called_with_requested_model(self):
        fake = _fake_ollama(content="ok")
        with patch.dict(sys.modules, {"ollama": fake}):
            log_digest.generate_digest(model="llama3.2")
        self.assertEqual(fake.chat.call_args.kwargs["model"], "llama3.2")

    def test_connection_error_gives_ollama_not_running_hint(self):
        fake = _fake_ollama(error=Exception("Connection refused"))
        with patch.dict(sys.modules, {"ollama": fake}):
            result = log_digest.generate_digest()
        self.assertIn("Ollama service is not running", result)

    def test_model_not_found_error_gives_pull_hint(self):
        fake = _fake_ollama(error=Exception("model 'qwen2.5:7b' not found (status code: 404)"))
        with patch.dict(sys.modules, {"ollama": fake}):
            result = log_digest.generate_digest(model="qwen2.5:7b")
        self.assertIn("ollama pull qwen2.5:7b", result)

    def test_generic_error_included_in_message(self):
        fake = _fake_ollama(error=Exception("boom"))
        with patch.dict(sys.modules, {"ollama": fake}):
            result = log_digest.generate_digest()
        self.assertIn("boom", result)


if __name__ == "__main__":
    unittest.main()
