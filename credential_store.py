"""
Credential key store with OS keychain integration and key rotation.

Key lookup priority:
  1. OS keychain (Windows Credential Manager / macOS Keychain / libsecret)
  2. secret.key file (legacy fallback, used in Docker / CI / headless environments)
"""
from __future__ import annotations

import logging
import os
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken

KEYCHAIN_SERVICE = "bytecurve-payroll"
KEYCHAIN_ACCOUNT = "fernet-key"


def is_keychain_available() -> bool:
    """Return True if a real OS keychain backend is accessible on this machine."""
    try:
        import keyring
        backend = keyring.get_keyring()
        class_name = type(backend).__name__.lower()
        return "fail" not in class_name and "null" not in class_name
    except ImportError:
        return False


def save_key(key: bytes, key_file: str = "secret.key") -> str:
    """
    Persist a Fernet key, preferring the OS keychain over a plain file.

    Returns 'keychain' if stored in the OS keychain, 'file' otherwise.
    Falls back silently to file storage when the keychain write fails.
    """
    if is_keychain_available():
        try:
            import keyring
            keyring.set_password(KEYCHAIN_SERVICE, KEYCHAIN_ACCOUNT, key.decode())
            logging.info("Fernet key saved to OS keychain.")
            return "keychain"
        except Exception as exc:
            logging.warning("Keychain write failed (%s); falling back to file.", exc)

    Path(key_file).write_bytes(key)
    logging.info("Fernet key saved to file: %s", key_file)
    return "file"


def load_key(key_file: str = "secret.key") -> bytes:
    """
    Load the Fernet key, checking the OS keychain first, then the key file.

    If neither source has a key, generates a new one and persists it.
    """
    if is_keychain_available():
        try:
            import keyring
            stored = keyring.get_password(KEYCHAIN_SERVICE, KEYCHAIN_ACCOUNT)
            if stored:
                logging.info("Fernet key loaded from OS keychain.")
                return stored.encode()
        except Exception as exc:
            logging.warning("Keychain read failed (%s); falling back to file.", exc)

    if os.path.exists(key_file):
        key = Path(key_file).read_bytes().strip()
        logging.info("Fernet key loaded from file: %s", key_file)
        return key

    key = Fernet.generate_key()
    save_key(key, key_file)
    return key


def delete_key(key_file: str = "secret.key") -> None:
    """Remove the Fernet key from both the keychain and the key file, if present."""
    if is_keychain_available():
        try:
            import keyring
            keyring.delete_password(KEYCHAIN_SERVICE, KEYCHAIN_ACCOUNT)
        except Exception:
            pass
    if os.path.exists(key_file):
        os.remove(key_file)


def rotate_key(
    credential_file: str = "credentials.enc",
    key_file: str = "secret.key",
) -> str:
    """
    Generate a new Fernet key and re-encrypt the credential file with it.

    Steps
    -----
    1. Load the current key (keychain → file).
    2. Decrypt the existing credential file with the current key.
    3. Generate a fresh Fernet key.
    4. Re-encrypt credentials with the new key and overwrite the file.
    5. Persist the new key (keychain → file).

    Returns
    -------
    str
        'keychain' or 'file', indicating where the new key was stored.

    Raises
    ------
    FileNotFoundError
        If ``credential_file`` does not exist.
    ValueError
        If the current key cannot decrypt ``credential_file`` (key/file mismatch).
    """
    current_key = load_key(key_file)

    cred_path = Path(credential_file)
    if not cred_path.exists():
        raise FileNotFoundError(
            f"Credential file not found: {credential_file}. "
            "Save credentials first before rotating the key."
        )

    try:
        plaintext = Fernet(current_key).decrypt(cred_path.read_bytes())
    except InvalidToken as exc:
        raise ValueError(
            f"Could not decrypt '{credential_file}' with the current key. "
            "The key and credential file may be out of sync."
        ) from exc

    new_key = Fernet.generate_key()
    cred_path.write_bytes(Fernet(new_key).encrypt(plaintext))

    location = save_key(new_key, key_file)
    logging.info("Key rotation complete. New key stored in: %s", location)
    return location
