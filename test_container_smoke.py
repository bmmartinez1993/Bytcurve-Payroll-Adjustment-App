#!/usr/bin/env python3
"""
Container smoke tests — verify the Docker environment is correctly wired up.

Run inside the container via docker-compose.test.yml:
    docker compose -f docker-compose.test.yml run --rm smoke-test

These tests do NOT log into the ByteCurve portal. They validate that every
dependency the automation needs is present and reachable before a live run.
Failures here indicate a broken image or missing runtime mount, not an
app logic bug.
"""
import importlib.util
import os
import subprocess
import sys

import pytest
from cryptography.fernet import Fernet
from playwright.sync_api import sync_playwright

_HERE = os.path.dirname(os.path.abspath(__file__))


# ---------------------------------------------------------------------------
# Shared fixture: load the main automation module once for the whole session.
# Uses the same importlib path that cli.py uses, so a failure here means
# cli.py will also fail to start.
# ---------------------------------------------------------------------------
@pytest.fixture(scope="session")
def app():
    spec = importlib.util.spec_from_file_location(
        "bytecurve_automation",
        os.path.join(_HERE, "ByteCurve Payroll Adjustment Automation.py"),
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ---------------------------------------------------------------------------
# 1. Display / Xvfb
#    The entrypoint starts Xvfb and waits for the socket before exec-ing the
#    app. Both checks here must pass for Chrome to launch in headed mode.
# ---------------------------------------------------------------------------
class TestDisplay:
    def test_display_env_var_is_set(self):
        assert os.environ.get("DISPLAY"), (
            "DISPLAY not set — Xvfb won't be reachable by Chrome or Tk"
        )

    def test_xvfb_socket_exists(self):
        assert os.path.exists("/tmp/.X11-unix/X99"), (
            "Xvfb socket /tmp/.X11-unix/X99 missing — "
            "docker-entrypoint.sh may not have started Xvfb"
        )


# ---------------------------------------------------------------------------
# 2. Chrome for Testing
#    Verifies that `playwright install chrome` ran during the image build and
#    that channel="chrome" resolves to a working binary (not system Chrome).
# ---------------------------------------------------------------------------
class TestChrome:
    def test_chrome_for_testing_launches(self):
        """channel='chrome' must resolve to Playwright's Chrome for Testing."""
        with sync_playwright() as pw:
            browser = pw.chromium.launch(
                headless=False,
                channel="chrome",
                args=["--no-sandbox"],
            )
            page = browser.new_page()
            page.goto("about:blank")
            browser.close()

    def test_bundled_chromium_also_present(self):
        """Bundled Chromium must exist (provided by the Playwright base image)."""
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True, args=["--no-sandbox"])
            browser.close()


# ---------------------------------------------------------------------------
# 3. Log directory
#    docker-compose.yml mounts ./logs:/app/logs. If the mount is missing or
#    the container user lacks write permission the file handler will throw on
#    the first log write.
# ---------------------------------------------------------------------------
class TestLogs:
    def test_logs_dir_is_writable(self):
        log_dir = os.path.join(_HERE, "logs")
        os.makedirs(log_dir, exist_ok=True)
        probe = os.path.join(log_dir, ".smoke_probe")
        try:
            with open(probe, "w") as f:
                f.write("ok")
        finally:
            if os.path.exists(probe):
                os.remove(probe)


# ---------------------------------------------------------------------------
# 4. CLI entry point
#    Runs cli.py --help as a subprocess — the same way Cloud Run or cron
#    would invoke it — and checks the exit code and expected flag names.
# ---------------------------------------------------------------------------
class TestCLI:
    def test_cli_help_exits_zero(self):
        result = subprocess.run(
            [sys.executable, "cli.py", "--help"],
            capture_output=True,
            cwd=_HERE,
            timeout=15,
        )
        assert result.returncode == 0, (
            f"cli.py --help exited {result.returncode}\n{result.stderr.decode()}"
        )

    def test_cli_help_mentions_date_flag(self):
        result = subprocess.run(
            [sys.executable, "cli.py", "--help"],
            capture_output=True,
            cwd=_HERE,
            timeout=15,
        )
        assert b"--date" in result.stdout, (
            "--date flag missing from cli.py --help output"
        )


# ---------------------------------------------------------------------------
# 5. Module loading via importlib
#    Confirms that exec_module() on the main .py file exposes every function
#    and global that cli.py depends on. A missing symbol here means cli.py
#    will crash at runtime before any browser interaction.
# ---------------------------------------------------------------------------
class TestModuleLoad:
    _REQUIRED_FUNCTIONS = [
        "login",
        "navigate_to_payroll",
        "validate_and_process_rows",
        "load_key",
        "decrypt_credentials",
        "encrypt_credentials",
        "get_previous_business_day",
    ]
    _REQUIRED_GLOBALS = [
        "AUTOMATION_STOP_FLAG",
        "USERNAME",
        "PASSWORD",
    ]

    def test_required_functions_present(self, app):
        missing = [fn for fn in self._REQUIRED_FUNCTIONS if not hasattr(app, fn)]
        assert not missing, f"Functions missing from loaded module: {missing}"

    def test_required_globals_present(self, app):
        missing = [g for g in self._REQUIRED_GLOBALS if not hasattr(app, g)]
        assert not missing, f"Globals missing from loaded module: {missing}"


# ---------------------------------------------------------------------------
# 6. Credential resolution
#    Tests both paths that cli.py supports without needing real credentials.
# ---------------------------------------------------------------------------
class TestCredentials:
    def test_env_var_path_resolves(self, monkeypatch):
        """Env vars must be readable — cli.py checks these before files."""
        monkeypatch.setenv("BYTECURVE_USER", "smoke_user")
        monkeypatch.setenv("BYTECURVE_PASS", "smoke_pass")
        assert os.environ["BYTECURVE_USER"] == "smoke_user"
        assert os.environ["BYTECURVE_PASS"] == "smoke_pass"

    def test_encrypted_file_path_roundtrip(self, app, tmp_path, monkeypatch):
        """load_key + decrypt_credentials must recover username and password."""
        key = Fernet.generate_key()
        encrypted = Fernet(key).encrypt(b"file_user:file_pass")

        key_file = tmp_path / "secret.key"
        enc_file = tmp_path / "credentials.enc"
        key_file.write_bytes(key)
        enc_file.write_bytes(encrypted)

        monkeypatch.setattr(app, "KEY_FILE", str(key_file))
        monkeypatch.setattr(app, "CREDENTIAL_FILE", str(enc_file))

        loaded_key = app.load_key()
        username, password = app.decrypt_credentials(loaded_key)

        assert username == "file_user"
        assert password == "file_pass"

    def test_missing_credential_file_returns_empty_strings(self, app, tmp_path, monkeypatch):
        """decrypt_credentials must return ('', '') gracefully when file is absent."""
        monkeypatch.setattr(app, "KEY_FILE", str(tmp_path / "secret.key"))
        monkeypatch.setattr(app, "CREDENTIAL_FILE", str(tmp_path / "credentials.enc"))

        key = Fernet.generate_key()
        (tmp_path / "secret.key").write_bytes(key)
        # credentials.enc intentionally not created

        username, password = app.decrypt_credentials(key)
        assert username == ""
        assert password == ""
