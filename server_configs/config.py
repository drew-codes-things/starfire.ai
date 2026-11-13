import os

from pydantic_settings import BaseSettings, SettingsConfigDict

class AppConfig(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    port: int = 8080
    bind_host: str = "127.0.0.1"
    data_dir: str = "./data"
    ollama_base_url: str | None = None
    cookie_secure: bool = True
    task_model: str = ""
    task_num_ctx: int = 0
    own_tools_models: str = "Arty,assistant-core,arty/*"
    inline_media_prefixes: str = "/arty-files/"
    arty_base_url: str = ""
    arty_phone_url: str = ""
    arty_access_client_id: str = ""
    arty_access_client_secret: str = ""
    model_voices: str = ""
    model_voices_dir: str = ""
    trusted_proxies: str = "127.0.0.1,::1"
    client_ip_header: str = "CF-Connecting-IP"

    def own_tools_patterns(self) -> list[str]:
        return [p.strip() for p in self.own_tools_models.split(",") if p.strip()]

    def media_prefixes(self) -> list[str]:
        return [p.strip() for p in self.inline_media_prefixes.split(",") if p.strip()]

    def voices_folder(self) -> str:
        return self.model_voices_dir or os.path.join(self.data_dir, "voices")

config = AppConfig()

os.makedirs(config.data_dir, exist_ok=True)

