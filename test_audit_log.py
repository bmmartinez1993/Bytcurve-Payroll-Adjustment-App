"""
Unit tests for audit_log — HMACFileHandler and verify_log.

All tests use temporary files so no real log output is produced.
"""

import hashlib
import hmac as _hmac
import logging
import os
import shutil
import tempfile
import unittest

from cryptography.fernet import Fernet

import audit_log


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_handler(path: str) -> audit_log.HMACFileHandler:
    handler = audit_log.HMACFileHandler(path, mode="w", encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(message)s"))
    return handler


def _read_lines(path: str) -> list[str]:
    with open(path, "r", encoding="utf-8") as fh:
        return [ln.rstrip("\n") for ln in fh.readlines()]


# ---------------------------------------------------------------------------
# _derive_key
# ---------------------------------------------------------------------------

class TestDeriveKey(unittest.TestCase):
    def test_same_key_produces_same_hmac_key(self):
        fernet_key = Fernet.generate_key()
        k1 = audit_log._derive_key(fernet_key)
        k2 = audit_log._derive_key(fernet_key)
        self.assertEqual(k1, k2)

    def test_different_fernet_keys_produce_different_hmac_keys(self):
        k1 = audit_log._derive_key(Fernet.generate_key())
        k2 = audit_log._derive_key(Fernet.generate_key())
        self.assertNotEqual(k1, k2)

    def test_derived_key_is_32_bytes(self):
        key = audit_log._derive_key(Fernet.generate_key())
        self.assertEqual(len(key), 32)

    def test_derived_key_differs_from_fernet_key(self):
        fernet_key = Fernet.generate_key()
        derived = audit_log._derive_key(fernet_key)
        self.assertNotEqual(derived, fernet_key)


# ---------------------------------------------------------------------------
# HMACFileHandler — emit behaviour
# ---------------------------------------------------------------------------

class TestHMACFileHandler(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.log_path = os.path.join(self.tmp, "test.log")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_writes_plain_line_when_no_key_set(self):
        handler = _make_handler(self.log_path)
        record = logging.LogRecord("test", logging.INFO, "", 0, "hello world", (), None)
        handler.emit(record)
        handler.close()
        lines = _read_lines(self.log_path)
        self.assertEqual(len(lines), 1)
        self.assertEqual(lines[0], "hello world")
        self.assertNotIn(audit_log.HMAC_TAG, lines[0])

    def test_writes_hmac_tagged_line_after_set_key(self):
        fernet_key = Fernet.generate_key()
        handler = _make_handler(self.log_path)
        handler.set_key(fernet_key)
        record = logging.LogRecord("test", logging.INFO, "", 0, "signed message", (), None)
        handler.emit(record)
        handler.close()
        lines = _read_lines(self.log_path)
        self.assertEqual(len(lines), 1)
        self.assertIn(audit_log.HMAC_TAG, lines[0])

    def test_hmac_tag_is_valid_sha256_hex(self):
        fernet_key = Fernet.generate_key()
        handler = _make_handler(self.log_path)
        handler.set_key(fernet_key)
        record = logging.LogRecord("test", logging.INFO, "", 0, "check tag", (), None)
        handler.emit(record)
        handler.close()
        line = _read_lines(self.log_path)[0]
        body, sig = line.rsplit(audit_log.HMAC_TAG, 1)
        self.assertEqual(len(sig), 64)
        int(sig, 16)  # must be valid hex — raises ValueError if not

    def test_unsigned_lines_before_key_signed_lines_after(self):
        fernet_key = Fernet.generate_key()
        handler = _make_handler(self.log_path)

        r1 = logging.LogRecord("test", logging.INFO, "", 0, "boot message", (), None)
        handler.emit(r1)

        handler.set_key(fernet_key)

        r2 = logging.LogRecord("test", logging.INFO, "", 0, "signed message", (), None)
        handler.emit(r2)
        handler.close()

        lines = _read_lines(self.log_path)
        self.assertEqual(len(lines), 2)
        self.assertNotIn(audit_log.HMAC_TAG, lines[0])
        self.assertIn(audit_log.HMAC_TAG, lines[1])

    def test_each_record_gets_unique_tag(self):
        fernet_key = Fernet.generate_key()
        handler = _make_handler(self.log_path)
        handler.set_key(fernet_key)
        for msg in ("alpha", "beta", "gamma"):
            record = logging.LogRecord("test", logging.INFO, "", 0, msg, (), None)
            handler.emit(record)
        handler.close()
        lines = _read_lines(self.log_path)
        sigs = [ln.rsplit(audit_log.HMAC_TAG, 1)[1] for ln in lines]
        self.assertEqual(len(set(sigs)), 3)

    def test_file_flushed_after_each_emit(self):
        fernet_key = Fernet.generate_key()
        handler = _make_handler(self.log_path)
        handler.set_key(fernet_key)
        record = logging.LogRecord("test", logging.INFO, "", 0, "flush check", (), None)
        handler.emit(record)
        # Do NOT close — file must be readable right after emit
        size = os.path.getsize(self.log_path)
        self.assertGreater(size, 0)
        handler.close()


# ---------------------------------------------------------------------------
# verify_log
# ---------------------------------------------------------------------------

class TestVerifyLog(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.log_path = os.path.join(self.tmp, "test.log")
        self.fernet_key = Fernet.generate_key()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _write_log(self, messages: list[str], sign: bool = True) -> None:
        handler = _make_handler(self.log_path)
        if sign:
            handler.set_key(self.fernet_key)
        for msg in messages:
            record = logging.LogRecord("test", logging.INFO, "", 0, msg, (), None)
            handler.emit(record)
        handler.close()

    def test_all_valid_signed_lines(self):
        self._write_log(["line one", "line two", "line three"])
        result = audit_log.verify_log(self.log_path, self.fernet_key)
        self.assertEqual(result["valid"], 3)
        self.assertEqual(result["tampered"], 0)
        self.assertEqual(result["unsigned"], 0)

    def test_all_unsigned_lines(self):
        self._write_log(["boot msg", "another boot msg"], sign=False)
        result = audit_log.verify_log(self.log_path, self.fernet_key)
        self.assertEqual(result["valid"], 0)
        self.assertEqual(result["tampered"], 0)
        self.assertEqual(result["unsigned"], 2)

    def test_mixed_unsigned_then_signed(self):
        handler = _make_handler(self.log_path)
        r_unsigned = logging.LogRecord("test", logging.INFO, "", 0, "boot", (), None)
        handler.emit(r_unsigned)
        handler.set_key(self.fernet_key)
        for msg in ("signed A", "signed B"):
            handler.emit(logging.LogRecord("test", logging.INFO, "", 0, msg, (), None))
        handler.close()

        result = audit_log.verify_log(self.log_path, self.fernet_key)
        self.assertEqual(result["unsigned"], 1)
        self.assertEqual(result["valid"], 2)
        self.assertEqual(result["tampered"], 0)

    def test_tampered_line_detected(self):
        self._write_log(["original content"])
        # Overwrite the body of the line, keeping the HMAC tag intact
        lines = _read_lines(self.log_path)
        body, sig = lines[0].rsplit(audit_log.HMAC_TAG, 1)
        tampered_body = body.replace("original content", "TAMPERED content")
        with open(self.log_path, "w", encoding="utf-8") as fh:
            fh.write(f"{tampered_body}{audit_log.HMAC_TAG}{sig}\n")

        result = audit_log.verify_log(self.log_path, self.fernet_key)
        self.assertEqual(result["tampered"], 1)
        self.assertEqual(result["valid"], 0)

    def test_wrong_key_marks_all_lines_tampered(self):
        self._write_log(["line A", "line B"])
        wrong_key = Fernet.generate_key()
        result = audit_log.verify_log(self.log_path, wrong_key)
        self.assertEqual(result["tampered"], 2)
        self.assertEqual(result["valid"], 0)

    def test_empty_log_file_returns_all_zeros(self):
        open(self.log_path, "w").close()
        result = audit_log.verify_log(self.log_path, self.fernet_key)
        self.assertEqual(result, {"valid": 0, "tampered": 0, "unsigned": 0})

    def test_verify_log_returns_dict_with_correct_keys(self):
        self._write_log(["check keys"])
        result = audit_log.verify_log(self.log_path, self.fernet_key)
        self.assertIn("valid", result)
        self.assertIn("tampered", result)
        self.assertIn("unsigned", result)

    def test_injected_fake_tag_line_is_caught_as_tampered(self):
        # Write a line that looks signed but whose HMAC is wrong
        with open(self.log_path, "w", encoding="utf-8") as fh:
            fh.write(f"fake log message{audit_log.HMAC_TAG}{'0' * 64}\n")
        result = audit_log.verify_log(self.log_path, self.fernet_key)
        self.assertEqual(result["tampered"], 1)


if __name__ == "__main__":
    unittest.main()
