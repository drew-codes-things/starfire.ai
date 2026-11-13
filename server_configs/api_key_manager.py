import os
import json
import logging
import stat
from typing import Dict
from cryptography.fernet import Fernet, InvalidToken

from atomic_io import atomic_write_json, holds_file_lock, set_aside_unreadable

logger = logging.getLogger(__name__)

IS_WINDOWS = os.name == "nt"
PRIVATE_FILE_MODE = 0o600

def safe_chmod(path: str, mode: int) -> bool:
    if IS_WINDOWS:
        return False
    try:
        os.chmod(path, mode)
        return True
    except OSError as error:
        logger.warning("could not set permissions %o on %s: %s", mode, path, error)
        return False

def make_private(path: str) -> None:
    if IS_WINDOWS:
        return
    current_mode = stat.S_IMODE(os.stat(path).st_mode)
    if current_mode & ~PRIVATE_FILE_MODE == 0:
        return
    if safe_chmod(path, PRIVATE_FILE_MODE):
        logger.info("tightened permissions on %s from %o to %o", path, current_mode, PRIVATE_FILE_MODE)

class APIKeyManager:
    def __init__(self, data_dir: str):
        self.data_dir = data_dir
        self.api_keys_file = os.path.join(data_dir, "api_keys.json")
        self.key_file = os.path.join(data_dir, ".key")

    @holds_file_lock("key_file")
    def get_or_create_key(self) -> bytes:
        if os.path.exists(self.key_file):
            safe_chmod(self.key_file, 0o600)
            with open(self.key_file, 'rb') as f:
                return f.read()
        else:
            key = Fernet.generate_key()
            with os.fdopen(os.open(self.key_file, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), 'wb') as f:
                f.write(key)
            safe_chmod(self.key_file, 0o600)
            return key

    def encrypt_api_key(self, api_key: str) -> str:
        if not api_key:
            return ""
        f = Fernet(self.get_or_create_key())
        return f.encrypt(api_key.encode()).decode()

    def decrypt_api_key(self, encrypted_key: str) -> str:
        if not encrypted_key:
            return ""
        f = Fernet(self.get_or_create_key())
        return f.decrypt(encrypted_key.encode()).decode()

    def _start_again_without(self, reason: str) -> Dict[str, str]:
        aside = set_aside_unreadable(self.api_keys_file)
        logger.error("API keys file could not be read because %s; moved it to %s and starting with no keys",
                     reason, aside)
        return {}

    def _load_raw(self) -> Dict[str, str]:
        if not os.path.exists(self.api_keys_file):
            return {}
        make_private(self.api_keys_file)
        try:
            with open(self.api_keys_file, 'r', encoding="utf-8") as f:
                encrypted_keys = json.load(f)
        except json.JSONDecodeError as e:
            return self._start_again_without(f"it is not valid JSON ({e})")
        if not isinstance(encrypted_keys, dict):
            return self._start_again_without(f"it holds a {type(encrypted_keys).__name__}, not an object")

        return {
            str(provider): key
            for provider, key in encrypted_keys.items()
            if isinstance(key, str)
        }

    def _write(self, keys: Dict[str, str]) -> None:
        atomic_write_json(self.api_keys_file, keys)
        safe_chmod(self.api_keys_file, PRIVATE_FILE_MODE)

    @holds_file_lock("api_keys_file")
    def save(self, provider: str, api_key: str):
        keys = self._load_raw()
        keys[provider] = self.encrypt_api_key(api_key)
        self._write(keys)

    @holds_file_lock("api_keys_file")
    def delete(self, provider: str):
        keys = self._load_raw()
        if provider not in keys:
            return
        del keys[provider]
        self._write(keys)

    def load(self) -> Dict[str, str]:
        encrypted_keys = self._load_raw()
        decrypted = {}
        for provider, key in encrypted_keys.items():
            try:
                decrypted[provider] = self.decrypt_api_key(key)
            except (InvalidToken, ValueError) as e:
                logger.warning("Failed to decrypt API key for %s: %s", provider, e)
        return decrypted
