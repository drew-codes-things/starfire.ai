"""Encrypted local API-key storage.

Ported near-verbatim from odysseus-dev's src/api_key_manager.py — the user
asked for starfire's API-key handling to mirror odysseus directly, and this
module is small, generic, and has no odysseus-specific coupling beyond the
safe_chmod helper below (trimmed from odysseus's core/platform_compat.py,
which also has process-liveness/shell-discovery helpers this app has no use
for — folded in here since this is its only caller).
"""

import os
import json
import logging
from typing import Dict
from cryptography.fernet import Fernet, InvalidToken

logger = logging.getLogger(__name__)

IS_WINDOWS = os.name == "nt"


def safe_chmod(path: str, mode: int) -> bool:
    """chmod that no-ops (rather than raising) on Windows or on failure.

    Windows files are already ACL-restricted to the owning user, so a Unix
    permission bit has nothing to do there.
    """
    if IS_WINDOWS:
        return False
    try:
        os.chmod(path, mode)
        return True
    except OSError:
        return False


class APIKeyManager:
    def __init__(self, data_dir: str):
        self.data_dir = data_dir
        self.api_keys_file = os.path.join(data_dir, "api_keys.json")
        self.key_file = os.path.join(data_dir, ".key")

    def get_or_create_key(self) -> bytes:
        """Get or create encryption key for API keys"""
        if os.path.exists(self.key_file):
            # Older versions wrote .key with the process umask (often 0o644,
            # i.e. group/world-readable). Re-restrict on read so existing
            # installs heal without needing the key to be regenerated.
            safe_chmod(self.key_file, 0o600)
            with open(self.key_file, 'rb') as f:
                return f.read()
        else:
            key = Fernet.generate_key()
            with open(self.key_file, 'wb') as f:
                f.write(key)
            # This key decrypts every stored provider credential, so restrict it
            # to the owner (0o600) — it must not be group/world-readable. No-op
            # on Windows (files there are ACL-restricted to the user already).
            safe_chmod(self.key_file, 0o600)
            return key

    def encrypt_api_key(self, api_key: str) -> str:
        """Encrypt an API key"""
        if not api_key:
            return ""
        f = Fernet(self.get_or_create_key())
        return f.encrypt(api_key.encode()).decode()

    def decrypt_api_key(self, encrypted_key: str) -> str:
        """Decrypt an API key"""
        if not encrypted_key:
            return ""
        f = Fernet(self.get_or_create_key())
        return f.decrypt(encrypted_key.encode()).decode()

    def _load_raw(self) -> Dict[str, str]:
        """Load the raw, still-encrypted keys dict from disk.

        Tolerates a missing/corrupt/wrong-shaped file by returning {} — the
        same robustness load() relies on at startup.
        """
        if not os.path.exists(self.api_keys_file):
            return {}
        try:
            with open(self.api_keys_file, 'r', encoding="utf-8") as f:
                encrypted_keys = json.load(f)
        except (json.JSONDecodeError, OSError) as e:
            # A corrupt/truncated api_keys.json must not crash load() (called on
            # startup) — treat it as no stored keys.
            logger.warning("Failed to read API keys file: %s", e)
            return {}
        if not isinstance(encrypted_keys, dict):
            # Legacy/wrong shape (e.g. a list) — .items() would raise. Ignore it.
            logger.warning("API keys file has unexpected shape (%s); ignoring", type(encrypted_keys).__name__)
            return {}

        return {
            str(provider): key
            for provider, key in encrypted_keys.items()
            if isinstance(key, str)
        }

    def save(self, provider: str, api_key: str):
        """Save encrypted API key to file.

        Operates on the raw (still-encrypted) on-disk dict so other providers'
        keys stay encrypted. Loading via load() first would decrypt them and
        write them back as plaintext, which then fails to decrypt on the next
        load() and silently drops those providers.

        Uses atomic write (temp file + os.replace) so a crash, disk-full, or
        mid-write error never truncates the existing keys file.
        """
        keys = self._load_raw()
        keys[provider] = self.encrypt_api_key(api_key)
        tmp_file = self.api_keys_file + ".tmp"
        try:
            with open(tmp_file, 'w', encoding="utf-8") as f:
                json.dump(keys, f)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp_file, self.api_keys_file)
        except OSError:
            # Clean up temp file on failure; re-raise so callers see the error
            try:
                os.remove(tmp_file)
            except OSError:
                pass
            raise

    def delete(self, provider: str):
        """Remove one provider's key, if present."""
        keys = self._load_raw()
        if provider not in keys:
            return
        del keys[provider]
        tmp_file = self.api_keys_file + ".tmp"
        try:
            with open(tmp_file, 'w', encoding="utf-8") as f:
                json.dump(keys, f)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp_file, self.api_keys_file)
        except OSError:
            try:
                os.remove(tmp_file)
            except OSError:
                pass
            raise

    def load(self) -> Dict[str, str]:
        """Load and decrypt API keys"""
        encrypted_keys = self._load_raw()
        decrypted = {}
        for provider, key in encrypted_keys.items():
            try:
                decrypted[provider] = self.decrypt_api_key(key)
            except (InvalidToken, ValueError) as e:
                logger.warning("Failed to decrypt API key for %s: %s", provider, e)
        return decrypted
