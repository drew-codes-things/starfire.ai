import os

from pydantic_settings import BaseSettings, SettingsConfigDict

class AppConfig(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    port: int = 8080
    bind_host: str = "0.0.0.0"
    data_dir: str = "./data"
    ollama_base_url: str | None = None

config = AppConfig()

os.makedirs(config.data_dir, exist_ok=True)

