import os
import json
import logging
from typing import Dict
from cryptography.fernet import Fernet, InvalidToken

logger = logging.getLogger(__name__)

IS_WINDOWS = os.name == "nt"

def safe_chmod(path: str, mode: int) -> bool:
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
        if os.path.exists(self.key_file):
            safe_chmod(self.key_file, 0o600)
            with open(self.key_file, 'rb') as f:
                return f.read()
        else:
            key = Fernet.generate_key()
            with open(self.key_file, 'wb') as f:
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

    def _load_raw(self) -> Dict[str, str]:
        if not os.path.exists(self.api_keys_file):
            return {}
        try:
            with open(self.api_keys_file, 'r', encoding="utf-8") as f:
                encrypted_keys = json.load(f)
        except (json.JSONDecodeError, OSError) as e:
            logger.warning("Failed to read API keys file: %s", e)
            return {}
        if not isinstance(encrypted_keys, dict):
            logger.warning("API keys file has unexpected shape (%s); ignoring", type(encrypted_keys).__name__)
            return {}

        return {
            str(provider): key
            for provider, key in encrypted_keys.items()
            if isinstance(key, str)
        }

    def save(self, provider: str, api_key: str):
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
            try:
                os.remove(tmp_file)
            except OSError:
                pass
            raise

    def delete(self, provider: str):
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
        encrypted_keys = self._load_raw()
        decrypted = {}
        for provider, key in encrypted_keys.items():
            try:
                decrypted[provider] = self.decrypt_api_key(key)
            except (InvalidToken, ValueError) as e:
                logger.warning("Failed to decrypt API key for %s: %s", provider, e)
        return decrypted
