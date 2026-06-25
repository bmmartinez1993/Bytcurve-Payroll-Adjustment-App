"""
Unit tests for credential_store — OS keychain integration and key rotation.

All keychain I/O is mocked so tests run without a real keychain daemon and
without the optional ``keyring`` package being available.
"""

import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, call, patch

from cryptography.fernet import Fernet


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _write_cred_file(path: str, username: str, password: str, key: bytes) -> None:
    """Encrypt and write a credential file using the supplied key."""
    token = Fernet(key).encrypt(f"{username}:{password}".encode())
    Path(path).write_bytes(token)


def _read_cred_file(path: str, key: bytes) -> str:
    """Decrypt and return the plaintext stored in a credential file."""
    return Fernet(key).decrypt(Path(path).read_bytes()).decode()


# ---------------------------------------------------------------------------
# Base class that gives each test its own temp directory
# ---------------------------------------------------------------------------

class _TempDirMixin(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.key_file = os.path.join(self.tmp, "test.key")
        self.cred_file = os.path.join(self.tmp, "test_creds.enc")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)


# ---------------------------------------------------------------------------
# is_keychain_available
# ---------------------------------------------------------------------------

class TestIsKeychainAvailable(_TempDirMixin):
    def test_returns_false_when_keyring_import_fails(self):
        import credential_store
        with patch.dict(sys.modules, {"keyring": None}):
            self.assertFalse(credential_store.is_keychain_available())

    def test_returns_false_for_fail_backend(self):
        import credential_store

        class FailKeyring:
            pass

        mock_kr = MagicMock()
        mock_kr.get_keyring.return_value = FailKeyring()
        with patch.dict(sys.modules, {"keyring": mock_kr}):
            self.assertFalse(credential_store.is_keychain_available())

    def test_returns_true_for_windows_credential_manager(self):
        import credential_store
        mock_kr = MagicMock()
        fake_backend = MagicMock()
        fake_backend.__class__ = type("WinVaultKeyring", (), {})
        mock_kr.get_keyring.return_value = fake_backend
        with patch.dict(sys.modules, {"keyring": mock_kr}):
            self.assertTrue(credential_store.is_keychain_available())


# ---------------------------------------------------------------------------
# save_key
# ---------------------------------------------------------------------------

class TestSaveKey(_TempDirMixin):
    @patch("credential_store.is_keychain_available", return_value=False)
    def test_writes_to_file_when_keychain_unavailable(self, _):
        import credential_store
        key = Fernet.generate_key()
        result = credential_store.save_key(key, self.key_file)
        self.assertEqual(result, "file")
        self.assertEqual(Path(self.key_file).read_bytes(), key)

    @patch("credential_store.is_keychain_available", return_value=True)
    def test_writes_to_keychain_when_available(self, _):
        import credential_store
        key = Fernet.generate_key()
        mock_kr = MagicMock()
        with patch.dict(sys.modules, {"keyring": mock_kr}):
            result = credential_store.save_key(key, self.key_file)
        self.assertEqual(result, "keychain")
        mock_kr.set_password.assert_called_once_with(
            credential_store.KEYCHAIN_SERVICE,
            credential_store.KEYCHAIN_ACCOUNT,
            key.decode(),
        )
        self.assertFalse(os.path.exists(self.key_file))

    @patch("credential_store.is_keychain_available", return_value=True)
    def test_falls_back_to_file_when_keychain_write_fails(self, _):
        import credential_store
        key = Fernet.generate_key()
        mock_kr = MagicMock()
        mock_kr.set_password.side_effect = Exception("keychain unavailable")
        with patch.dict(sys.modules, {"keyring": mock_kr}):
            result = credential_store.save_key(key, self.key_file)
        self.assertEqual(result, "file")
        self.assertEqual(Path(self.key_file).read_bytes(), key)


# ---------------------------------------------------------------------------
# load_key
# ---------------------------------------------------------------------------

class TestLoadKey(_TempDirMixin):
    @patch("credential_store.is_keychain_available", return_value=False)
    def test_loads_from_file_when_keychain_unavailable(self, _):
        import credential_store
        key = Fernet.generate_key()
        Path(self.key_file).write_bytes(key)
        self.assertEqual(credential_store.load_key(self.key_file), key)

    @patch("credential_store.is_keychain_available", return_value=True)
    def test_loads_from_keychain_when_available(self, _):
        import credential_store
        key = Fernet.generate_key()
        mock_kr = MagicMock()
        mock_kr.get_password.return_value = key.decode()
        with patch.dict(sys.modules, {"keyring": mock_kr}):
            result = credential_store.load_key(self.key_file)
        self.assertEqual(result, key)
        self.assertFalse(os.path.exists(self.key_file))

    @patch("credential_store.is_keychain_available", return_value=True)
    def test_falls_back_to_file_when_keychain_returns_none(self, _):
        import credential_store
        key = Fernet.generate_key()
        Path(self.key_file).write_bytes(key)
        mock_kr = MagicMock()
        mock_kr.get_password.return_value = None
        with patch.dict(sys.modules, {"keyring": mock_kr}):
            result = credential_store.load_key(self.key_file)
        self.assertEqual(result, key)

    @patch("credential_store.is_keychain_available", return_value=True)
    def test_falls_back_to_file_when_keychain_read_raises(self, _):
        import credential_store
        key = Fernet.generate_key()
        Path(self.key_file).write_bytes(key)
        mock_kr = MagicMock()
        mock_kr.get_password.side_effect = Exception("backend error")
        with patch.dict(sys.modules, {"keyring": mock_kr}):
            result = credential_store.load_key(self.key_file)
        self.assertEqual(result, key)

    @patch("credential_store.is_keychain_available", return_value=False)
    def test_generates_and_persists_new_key_when_none_exists(self, _):
        import credential_store
        self.assertFalse(os.path.exists(self.key_file))
        key = credential_store.load_key(self.key_file)
        self.assertIsInstance(key, bytes)
        self.assertTrue(os.path.exists(self.key_file))
        self.assertEqual(Path(self.key_file).read_bytes(), key)

    @patch("credential_store.is_keychain_available", return_value=False)
    def test_strips_trailing_newline_from_file(self, _):
        import credential_store
        key = Fernet.generate_key()
        Path(self.key_file).write_bytes(key + b"\n")
        result = credential_store.load_key(self.key_file)
        self.assertEqual(result, key)


# ---------------------------------------------------------------------------
# delete_key
# ---------------------------------------------------------------------------

class TestDeleteKey(_TempDirMixin):
    @patch("credential_store.is_keychain_available", return_value=False)
    def test_removes_key_file(self, _):
        import credential_store
        Path(self.key_file).write_bytes(Fernet.generate_key())
        credential_store.delete_key(self.key_file)
        self.assertFalse(os.path.exists(self.key_file))

    @patch("credential_store.is_keychain_available", return_value=False)
    def test_noop_when_file_absent(self, _):
        import credential_store
        credential_store.delete_key(self.key_file)  # must not raise

    @patch("credential_store.is_keychain_available", return_value=True)
    def test_deletes_from_keychain_and_file(self, _):
        import credential_store
        Path(self.key_file).write_bytes(Fernet.generate_key())
        mock_kr = MagicMock()
        with patch.dict(sys.modules, {"keyring": mock_kr}):
            credential_store.delete_key(self.key_file)
        mock_kr.delete_password.assert_called_once_with(
            credential_store.KEYCHAIN_SERVICE, credential_store.KEYCHAIN_ACCOUNT
        )
        self.assertFalse(os.path.exists(self.key_file))

    @patch("credential_store.is_keychain_available", return_value=True)
    def test_swallows_keychain_delete_error(self, _):
        import credential_store
        mock_kr = MagicMock()
        mock_kr.delete_password.side_effect = Exception("not found")
        with patch.dict(sys.modules, {"keyring": mock_kr}):
            credential_store.delete_key(self.key_file)  # must not raise


# ---------------------------------------------------------------------------
# rotate_key
# ---------------------------------------------------------------------------

class TestRotateKey(_TempDirMixin):
    @patch("credential_store.is_keychain_available", return_value=False)
    def test_raises_file_not_found_when_no_credential_file(self, _):
        import credential_store
        Path(self.key_file).write_bytes(Fernet.generate_key())
        with self.assertRaises(FileNotFoundError):
            credential_store.rotate_key(self.cred_file, self.key_file)

    @patch("credential_store.is_keychain_available", return_value=False)
    def test_raises_value_error_when_key_does_not_match_credentials(self, _):
        import credential_store
        real_key = Fernet.generate_key()
        wrong_key = Fernet.generate_key()
        _write_cred_file(self.cred_file, "user", "pass", real_key)
        Path(self.key_file).write_bytes(wrong_key)
        with self.assertRaises(ValueError):
            credential_store.rotate_key(self.cred_file, self.key_file)

    @patch("credential_store.is_keychain_available", return_value=False)
    def test_new_key_differs_from_old_key(self, _):
        import credential_store
        old_key = Fernet.generate_key()
        _write_cred_file(self.cred_file, "user", "pass", old_key)
        Path(self.key_file).write_bytes(old_key)
        credential_store.rotate_key(self.cred_file, self.key_file)
        new_key = Path(self.key_file).read_bytes()
        self.assertNotEqual(new_key, old_key)

    @patch("credential_store.is_keychain_available", return_value=False)
    def test_credentials_still_correct_after_rotation(self, _):
        import credential_store
        key = Fernet.generate_key()
        _write_cred_file(self.cred_file, "alice", "s3cr3t!", key)
        Path(self.key_file).write_bytes(key)
        credential_store.rotate_key(self.cred_file, self.key_file)
        new_key = Path(self.key_file).read_bytes()
        plaintext = _read_cred_file(self.cred_file, new_key)
        self.assertEqual(plaintext, "alice:s3cr3t!")

    @patch("credential_store.is_keychain_available", return_value=False)
    def test_returns_file_when_keychain_unavailable(self, _):
        import credential_store
        key = Fernet.generate_key()
        _write_cred_file(self.cred_file, "user", "pass", key)
        Path(self.key_file).write_bytes(key)
        result = credential_store.rotate_key(self.cred_file, self.key_file)
        self.assertEqual(result, "file")

    @patch("credential_store.is_keychain_available", return_value=True)
    def test_stores_new_key_in_keychain_when_available(self, _):
        import credential_store
        old_key = Fernet.generate_key()
        _write_cred_file(self.cred_file, "user", "pass", old_key)
        # Simulate: keychain holds the old key, then accepts the new one
        keychain_store: dict[str, str] = {
            credential_store.KEYCHAIN_ACCOUNT: old_key.decode()
        }

        def _get(service, account):
            return keychain_store.get(account)

        def _set(service, account, value):
            keychain_store[account] = value

        mock_kr = MagicMock()
        mock_kr.get_password.side_effect = _get
        mock_kr.set_password.side_effect = _set

        with patch.dict(sys.modules, {"keyring": mock_kr}):
            result = credential_store.rotate_key(self.cred_file, self.key_file)

        self.assertEqual(result, "keychain")
        new_key = keychain_store[credential_store.KEYCHAIN_ACCOUNT].encode()
        plaintext = _read_cred_file(self.cred_file, new_key)
        self.assertEqual(plaintext, "user:pass")

    @patch("credential_store.is_keychain_available", return_value=False)
    def test_rotated_credential_file_is_valid_fernet_token(self, _):
        import credential_store
        key = Fernet.generate_key()
        _write_cred_file(self.cred_file, "u", "p", key)
        Path(self.key_file).write_bytes(key)
        credential_store.rotate_key(self.cred_file, self.key_file)
        new_key = Path(self.key_file).read_bytes()
        # decrypt() raises InvalidToken if the file is corrupted
        data = Fernet(new_key).decrypt(Path(self.cred_file).read_bytes())
        self.assertEqual(data, b"u:p")

    @patch("credential_store.is_keychain_available", return_value=False)
    def test_rotation_is_idempotent_when_run_twice(self, _):
        import credential_store
        key = Fernet.generate_key()
        _write_cred_file(self.cred_file, "user", "pass", key)
        Path(self.key_file).write_bytes(key)
        credential_store.rotate_key(self.cred_file, self.key_file)
        credential_store.rotate_key(self.cred_file, self.key_file)
        final_key = Path(self.key_file).read_bytes()
        plaintext = _read_cred_file(self.cred_file, final_key)
        self.assertEqual(plaintext, "user:pass")


# ---------------------------------------------------------------------------
# Round-trip integration: save_key → load_key → rotate_key
# ---------------------------------------------------------------------------

class TestRoundTrip(_TempDirMixin):
    @patch("credential_store.is_keychain_available", return_value=False)
    def test_full_file_based_round_trip(self, _):
        import credential_store
        # 1. Generate and save a key
        key = Fernet.generate_key()
        credential_store.save_key(key, self.key_file)

        # 2. Write credentials with that key
        _write_cred_file(self.cred_file, "bob", "hunter2", key)

        # 3. Load the key back
        loaded = credential_store.load_key(self.key_file)
        self.assertEqual(loaded, key)

        # 4. Rotate — new key + re-encrypted creds
        credential_store.rotate_key(self.cred_file, self.key_file)
        rotated_key = credential_store.load_key(self.key_file)
        self.assertNotEqual(rotated_key, key)

        # 5. Credentials still readable
        plaintext = _read_cred_file(self.cred_file, rotated_key)
        self.assertEqual(plaintext, "bob:hunter2")

    @patch("credential_store.is_keychain_available", return_value=True)
    def test_full_keychain_round_trip(self, _):
        import credential_store
        key = Fernet.generate_key()
        keychain_store: dict[str, str] = {}

        def _get(svc, acc):
            return keychain_store.get(acc)

        def _set(svc, acc, val):
            keychain_store[acc] = val

        mock_kr = MagicMock()
        mock_kr.get_password.side_effect = _get
        mock_kr.set_password.side_effect = _set

        with patch.dict(sys.modules, {"keyring": mock_kr}):
            credential_store.save_key(key, self.key_file)
            _write_cred_file(self.cred_file, "carol", "p@$$", key)
            loaded = credential_store.load_key(self.key_file)
            self.assertEqual(loaded, key)
            credential_store.rotate_key(self.cred_file, self.key_file)
            rotated_key = credential_store.load_key(self.key_file).encode() \
                if isinstance(credential_store.load_key(self.key_file), str) \
                else credential_store.load_key(self.key_file)

        # Verify credentials with whatever is in the keychain now
        new_key_str = keychain_store[credential_store.KEYCHAIN_ACCOUNT]
        plaintext = _read_cred_file(self.cred_file, new_key_str.encode())
        self.assertEqual(plaintext, "carol:p@$$")


if __name__ == "__main__":
    unittest.main()
