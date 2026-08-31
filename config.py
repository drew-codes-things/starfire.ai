"""Application configuration.

A single flat settings object is enough at this scale — odysseus-dev splits
config into DataConfig/LLMConfig/SecurityConfig because it is a multi-user,
multi-subsystem app; starfire is one process with no auth surface, so that
split would just be indirection here.
"""

import os

from pydantic_settings import BaseSettings, SettingsConfigDict


class AppConfig(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    port: int = 8080
    bind_host: str = "0.0.0.0"
    data_dir: str = "./data"
    # Pure connectivity override, not a secret — hosted-provider keys go
    # through the encrypted store (api_key_manager.py) instead, via the UI.
    ollama_base_url: str | None = None


config = AppConfig()

# create_directories()-equivalent from odysseus's src/config.py, done inline
# since starfire only has the one data directory to worry about.
os.makedirs(config.data_dir, exist_ok=True)

