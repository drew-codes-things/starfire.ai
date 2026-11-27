import json
import logging
import os
from typing import ClassVar

from pydantic_settings import BaseSettings, SettingsConfigDict

logger = logging.getLogger(__name__)


def _names_map(setting: str, raw: str) -> dict[str, str]:
    if not raw.strip():
        return {}
    try:
        parsed = json.loads(raw)
    except ValueError as error:
        logger.warning("%s is not JSON, so it is ignored: %s", setting, error)
        return {}
    if not isinstance(parsed, dict):
        logger.warning("%s must be a JSON object of names, so it is ignored", setting)
        return {}
    return {key: value.strip() for key, value in parsed.items()
            if isinstance(key, str) and isinstance(value, str) and value.strip()}


class AppConfig(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    port: int = 8080
    bind_host: str = "127.0.0.1"
    data_dir: str = "./data"
    ollama_base_url: str | None = None
    cookie_secure: bool = True
    task_model: str = ""
    task_num_ctx: int = 24576
    model_labels: str = ""
    hidden_models: str = ""
    model_renames: str = ""
    side_model: str = ""
    side_model_label: str = ""
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
    session_check_hosts: str = ""
    public_base_url: str = ""
    max_document_mb: int = 20
    max_attachments_mb: int = 1024
    max_documents_mb: int = 512
    max_restore_mb: int = 1024
    min_free_disk_mb: int = 512
    edit_allowed_roots: str = ""
    restricted_models: str = "*-abl,*-abl-*,*abliterated*,*uncensored*,*heretic*"
    remote_rules_connections: str = ""
    remote_tools: str = "search_web,deep_research,find_image"
    generations_per_minute: int = 20
    generations_per_day: int = 400
    mail_requests_per_minute: int = 60
    app_key_requests_per_minute: int = 30
    desktop_update_check: bool = True

    def own_tools_patterns(self) -> list[str]:
        return [p.strip() for p in self.own_tools_models.split(",") if p.strip()]

    def media_prefixes(self) -> list[str]:
        return [p.strip() for p in self.inline_media_prefixes.split(",") if p.strip()]

    def identity_check_hosts(self) -> list[str]:
        local = [f"127.0.0.1:{self.port}", f"localhost:{self.port}", f"[::1]:{self.port}"]
        return local + [host.strip() for host in self.session_check_hosts.split(",") if host.strip()]

    def edit_roots(self) -> list[str]:
        return [os.path.abspath(root.strip()) for root in self.edit_allowed_roots.split(",") if root.strip()]

    def restricted_model_patterns(self) -> list[str]:
        return [p.strip() for p in self.restricted_models.split(",") if p.strip()]

    def remote_rules_connection_ids(self) -> list[str]:
        return [connection.strip() for connection in self.remote_rules_connections.split(",") if connection.strip()]

    def remote_tool_names(self) -> list[str]:
        return [name.strip() for name in self.remote_tools.split(",") if name.strip()]

    SIDE_MODEL_PLACEHOLDER: ClassVar[str] = "{SIDE_MODEL}"

    def side_model_id(self) -> str:
        return self.side_model.strip()

    def with_side_model(self, text: str) -> str:
        side = self.side_model_id()
        return text.replace(self.SIDE_MODEL_PLACEHOLDER, side) if side else text

    def labels(self) -> dict[str, str]:
        side, side_label = self.side_model_id(), self.side_model_label.strip()
        side_labels = {side: side_label} if side and side_label else {}
        return {**side_labels, **_names_map("MODEL_LABELS", self.model_labels)}

    def hidden_model_patterns(self) -> list[str]:
        patterns = [p.strip() for p in self.hidden_models.split(",") if p.strip()]
        side = self.side_model_id()
        return patterns + [side] if side and side not in patterns else patterns

    def renamed(self, model: str) -> str:
        renames = _names_map("MODEL_RENAMES", self.model_renames)
        target = self.with_side_model(renames.get(model, model))
        return model if self.SIDE_MODEL_PLACEHOLDER in target else target

    def voice_rules_text(self) -> str:
        return self.with_side_model(self.model_voices)

    def voices_folder(self) -> str:
        return self.model_voices_dir or os.path.join(self.data_dir, "voices")

config = AppConfig()

os.makedirs(config.data_dir, exist_ok=True)

