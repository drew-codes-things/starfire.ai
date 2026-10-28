import os

from pydantic_settings import BaseSettings, SettingsConfigDict

class AppConfig(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    port: int = 8080
    # Loopback only by default: there's no login, and the browser chooses which tools
    # (including run_shell) a chat may use, so anyone who can reach the port could run
    # commands on this machine. Set BIND_HOST=0.0.0.0 only behind your own auth.
    bind_host: str = "127.0.0.1"
    data_dir: str = "./data"
    ollama_base_url: str | None = None

config = AppConfig()

os.makedirs(config.data_dir, exist_ok=True)

