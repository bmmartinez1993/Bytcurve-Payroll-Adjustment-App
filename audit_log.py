"""
HMAC-signed audit log handler for post-run tamper detection.

Each log record is suffixed with |hmac=<sha256-hex> so that any modification
of the log file after an automation run is detectable via verify_log().

Signing lifecycle
-----------------
1. The HMACFileHandler is created at module-load time (before credentials
   are available) and registered with the root logger.
2. Once the Fernet key is loaded, call handler.set_key(fernet_key) to
   activate signing.  Records written before set_key() carry no HMAC tag —
   this keeps early boot messages and is backward-compatible with any log
   reader that doesn't understand the tag format.
3. After the run, call verify_log(path, fernet_key) to detect tampering.
"""
from __future__ import annotations

import hashlib
import hmac as _hmac
import logging
from typing import Optional

HMAC_TAG = " |hmac="


def _derive_key(fernet_key: bytes) -> bytes:
    """Derive a 32-byte HMAC key from the Fernet key via SHA-256."""
    return hashlib.sha256(b"bytecurve-audit:" + fernet_key).digest()


class HMACFileHandler(logging.FileHandler):
    """
    Live-flushing file handler that appends an HMAC-SHA256 tag to each record
    after set_key() has been called.

    Flushes on every emit so that the log is tail-able in real time and so
    that a hard kill of the process leaves a complete, parseable file.
    """

    def __init__(self, filename: str, mode: str = "w", encoding: str = "utf-8"):
        super().__init__(filename, mode, encoding)
        self._hmac_key: Optional[bytes] = None

    def set_key(self, fernet_key: bytes) -> None:
        """Activate HMAC signing using a key derived from *fernet_key*."""
        self._hmac_key = _derive_key(fernet_key)

    def emit(self, record: logging.LogRecord) -> None:
        try:
            msg = self.format(record)
            if self._hmac_key is not None:
                sig = _hmac.new(self._hmac_key, msg.encode(), hashlib.sha256).hexdigest()
                line = f"{msg}{HMAC_TAG}{sig}"
            else:
                line = msg
            self.stream.write(line + self.terminator)
            self.flush()
        except Exception:
            self.handleError(record)


def verify_log(log_path: str, fernet_key: bytes) -> dict[str, int]:
    """
    Verify the HMAC tags in a log file produced by HMACFileHandler.

    Parameters
    ----------
    log_path : str
        Path to the log file.
    fernet_key : bytes
        The Fernet key used during the run — the same key is used to derive
        the HMAC signing key.

    Returns
    -------
    dict with integer counts for three categories:
        "valid"    — signed lines whose HMAC matched (not tampered)
        "tampered" — signed lines whose HMAC did not match
        "unsigned" — lines without an HMAC tag (early boot records)
    """
    hmac_key = _derive_key(fernet_key)
    counts: dict[str, int] = {"valid": 0, "tampered": 0, "unsigned": 0}

    with open(log_path, "r", encoding="utf-8") as fh:
        for raw_line in fh:
            line = raw_line.rstrip("\n")
            if HMAC_TAG not in line:
                counts["unsigned"] += 1
                continue
            body, sig = line.rsplit(HMAC_TAG, 1)
            expected = _hmac.new(hmac_key, body.encode(), hashlib.sha256).hexdigest()
            if _hmac.compare_digest(expected, sig):
                counts["valid"] += 1
            else:
                counts["tampered"] += 1

    return counts
