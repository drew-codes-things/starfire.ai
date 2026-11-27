import functools
import logging
from typing import Callable

import auto_model
from config import config

logger = logging.getLogger(__name__)
CHECKED_ROLES = (auto_model.CHAT, auto_model.CODE)


def nothing_listed() -> set[str] | None:
    return None


_local_listing: list[Callable[[], set[str] | None]] = [nothing_listed]


def use_local_listing(listing: Callable[[], set[str] | None]) -> None:
    _local_listing[0] = listing


def local_models() -> set[str] | None:
    return _local_listing[0]()


@functools.lru_cache(maxsize=64)
def _report_unusable(role: str, name: str) -> None:
    logger.warning("the saved %s model %r is not a local model starfire can see, so the main model setting "
                   "(TASK_MODEL) is used instead", role, name)


def usable_role(role: str, name: str, local: set[str] | None = None) -> str:
    if not name.strip():
        return ""
    if role not in CHECKED_ROLES:
        return name
    listed = local if local is not None else local_models()
    if listed is None:
        return ""
    if name in listed:
        return name
    _report_unusable(role, name)
    return ""


def checked_roles(roles: dict[str, str], local: set[str] | None = None) -> dict[str, str]:
    return {role: usable_role(role, name, local) for role, name in roles.items()}


def main_model() -> str:
    saved = auto_model.AutoModelSettings(config.data_dir).roles()[auto_model.CHAT]
    return usable_role(auto_model.CHAT, saved) or config.task_model
