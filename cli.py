#!/usr/bin/env python3
"""
Headless CLI runner for ByteCurve Payroll Adjustment Automation.

Credentials are resolved in this order:
    1. BYTECURVE_USER / BYTECURVE_PASS environment variables
    2. credentials.enc + secret.key files in the current directory

Usage:
    python cli.py [--date YYYY-MM-DD]
"""
import argparse
import importlib.util
import logging
import os
import platform
import signal
import sys
from datetime import datetime as dt
from playwright.sync_api import sync_playwright

# Tell the main module to skip GUI imports (customtkinter, pyautogui, tkinter).
os.environ["BYTECURVE_CLI"] = "1"

# ── Load the main automation module ──────────────────────────────────────────
# exec_module runs all module-level code (logging setup, imports) but does NOT
# launch the GUI — the if __name__ == "__main__" guard in the main file is
# never entered when the module is loaded this way.
_here = os.path.dirname(os.path.abspath(__file__))
_spec = importlib.util.spec_from_file_location(
    "bytecurve_automation",
    os.path.join(_here, "ByteCurve Payroll Adjustment Automation.py"),
)
_app = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_app)


def _chrome_executable() -> str | None:
    """Return the path to the system Chrome binary for the current OS, or None."""
    candidates = {
        "Windows": [
            r"C:\Program Files\Google\Chrome\Application\chrome.exe",
            r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        ],
        "Darwin": ["/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"],
        "Linux": [
            "/usr/bin/google-chrome",
            "/usr/bin/google-chrome-stable",
        ],
    }.get(platform.system(), [])
    return next((p for p in candidates if os.path.isfile(p)), None)


# ── Signal handler (SIGTERM from docker stop, SIGINT from Ctrl-C) ─────────
def _stop_handler(sig: int, _frame) -> None:
    logging.info(f"Signal {sig} received — setting stop flag.")
    _app.AUTOMATION_STOP_FLAG = True


def _capture_failure(page, tag: str = "failure") -> None:
    """Save a screenshot + page HTML to logs/ so a headless failure can be
    diagnosed after the fact (the container has no visible browser window)."""
    try:
        os.makedirs("logs", exist_ok=True)
        stamp = dt.now().strftime("%Y%m%d_%H%M%S")
        shot = os.path.join("logs", f"{tag}_{stamp}.png")
        html = os.path.join("logs", f"{tag}_{stamp}.html")
        page.screenshot(path=shot, full_page=True)
        with open(html, "w", encoding="utf-8") as fh:
            fh.write(page.content())
        logging.error(
            f"Captured failure artifacts at URL {page.url}: {shot}, {html}"
        )
    except Exception as cap_exc:
        logging.error(f"Could not capture failure artifacts: {cap_exc}")


def main() -> None:
    signal.signal(signal.SIGTERM, _stop_handler)
    signal.signal(signal.SIGINT, _stop_handler)

    parser = argparse.ArgumentParser(
        description="ByteCurve Payroll Adjustment — headless CLI runner"
    )
    parser.add_argument(
        "--date",
        metavar="YYYY-MM-DD",
        default=None,
        help="Target business date (default: previous business day)",
    )
    parser.add_argument(
        "--rotate-key",
        action="store_true",
        help=(
            "Rotate the Fernet encryption key: re-encrypt credentials.enc with a new key "
            "and store it in the OS keychain (or secret.key file as fallback), then exit."
        ),
    )
    parser.add_argument(
        "--verify-log",
        metavar="LOG_PATH",
        nargs="?",
        const=os.path.join("logs", "automation_activity.log"),
        help=(
            "Verify HMAC signatures in a log file and report valid/unsigned/tampered counts. "
            "Defaults to logs/automation_activity.log when no path is given."
        ),
    )
    args = parser.parse_args()

    # ── Log verification (early exit — no browser needed) ─────────────────────
    if args.verify_log:
        import audit_log
        import credential_store
        try:
            key = credential_store.load_key(_app.KEY_FILE)
            result = audit_log.verify_log(args.verify_log, key)
            print(
                f"Log verification: {result['valid']} valid, "
                f"{result['unsigned']} unsigned, {result['tampered']} tampered"
            )
            if result["tampered"] > 0:
                print(f"WARNING: {result['tampered']} tampered line(s) detected in {args.verify_log}")
                sys.exit(1)
        except Exception as exc:
            logging.error("Log verification failed: %s", exc)
            sys.exit(1)
        sys.exit(0)

    # ── Key rotation (early exit — no browser needed) ─────────────────────────
    if args.rotate_key:
        import credential_store
        try:
            location = credential_store.rotate_key(_app.CREDENTIAL_FILE, _app.KEY_FILE)
            print(f"Key rotation complete. New key stored in: {location}")
        except (FileNotFoundError, ValueError) as exc:
            logging.error("Key rotation failed: %s", exc)
            sys.exit(1)
        sys.exit(0)

    # ── Credentials ───────────────────────────────────────────────────────────
    username = os.environ.get("BYTECURVE_USER", "").strip()
    password = os.environ.get("BYTECURVE_PASS", "").strip()
    cred_source = "BYTECURVE_USER/PASS env vars" if (username and password) else None

    if not (username and password):
        try:
            key = _app.load_key()
            username, password = _app.decrypt_credentials(key)
            cred_source = f"{_app.CREDENTIAL_FILE} + {_app.KEY_FILE}"
        except Exception as exc:
            logging.error(f"Could not load credentials from file: {exc}")

    if not (username and password):
        logging.critical(
            "No credentials available. "
            "Set BYTECURVE_USER / BYTECURVE_PASS env vars "
            "or mount credentials.enc + secret.key."
        )
        sys.exit(1)

    # Prove which credentials were loaded without ever logging the password.
    logging.info(
        f"Credentials resolved from {cred_source}: "
        f"username='{username}', password length={len(password)}."
    )

    # Inject into module globals so login() and related functions find them.
    _app.USERNAME = username
    _app.PASSWORD = password

    # ── HMAC audit log activation ──────────────────────────────────────────────
    # Always attempt to load the Fernet key so HMAC signing is active even when
    # credentials came from env vars (which bypass the key file path above).
    import audit_log
    import credential_store
    try:
        _hmac_fernet_key = credential_store.load_key(_app.KEY_FILE)
        _app._file_handler.set_key(_hmac_fernet_key)
        logging.info("AUDIT: HMAC signing active.")
    except Exception:
        logging.warning("AUDIT: HMAC signing unavailable — key could not be loaded.")

    # ── Credential file integrity baseline ────────────────────────────────────
    _cred_hash = credential_store.hash_credential_file(_app.CREDENTIAL_FILE)
    if _cred_hash:
        logging.info("INTEGRITY: Credential file hash recorded for post-run verification.")

    target_date = args.date or _app.get_previous_business_day()

    # ── Run ───────────────────────────────────────────────────────────────────
    logging.info("=" * 60)
    logging.info(f"AUTOMATION RUN STARTED: {dt.now().strftime('%Y-%m-%d %H:%M:%S')}")
    logging.info(f"Target date: {target_date}")
    logging.info("=" * 60)

    exit_code = 0
    try:
        with sync_playwright() as pw:
            _launch_kwargs: dict = {
                "headless": False,
                "channel": "chrome",
                "args": ["--start-maximized", "--no-sandbox", "--window-size=1920,1080"],
            }
            # On Mac/Linux, Playwright may not auto-locate Chrome; supply the
            # path explicitly so the cookie-accept banner is handled correctly.
            _exe = _chrome_executable()
            if _exe and platform.system() != "Windows":
                _launch_kwargs["executable_path"] = _exe
            browser = pw.chromium.launch(**_launch_kwargs)
            context = browser.new_context(no_viewport=True)
            page = context.new_page()
            try:
                page.on("dialog", lambda d: d.accept())
                _app.login(page)
                _app.navigate_to_payroll(page)
                if not _app.AUTOMATION_STOP_FLAG:
                    _app.validate_and_process_rows(page, target_date)
                if _app.AUTOMATION_STOP_FLAG:
                    logging.warning("STOP: Automation halted before completing.")
                else:
                    logging.info("COMPLETE: Automation finished successfully.")
            except Exception as exc:
                logging.critical(f"Automation error: {exc}", exc_info=True)
                _capture_failure(page, "automation_error")
                exit_code = 1
            finally:
                browser.close()
    except Exception as exc:
        logging.critical(f"Browser launch failed: {exc}", exc_info=True)
        exit_code = 1

    # ── Post-run integrity check ───────────────────────────────────────────────
    if _cred_hash and not credential_store.verify_credential_file_integrity(
        _cred_hash, _app.CREDENTIAL_FILE
    ):
        logging.critical(
            "INTEGRITY_VIOLATION: credentials.enc was modified during the automation run."
        )
        exit_code = 1

    sys.exit(exit_code)


if __name__ == "__main__":
    main()
