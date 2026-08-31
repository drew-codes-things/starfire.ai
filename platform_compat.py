"""Trimmed port of odysseus-dev's core/platform_compat.py — just the one
helper api_key_manager.py needs. The rest of that module (process liveness
checks, shell discovery) is odysseus-scale infra starfire doesn't use.
"""

import os

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
