import functools
import json
import logging
import os
import tempfile
import threading
import time

logger = logging.getLogger(__name__)

_locks_by_path: dict[str, threading.RLock] = {}
_locks_by_path_guard = threading.Lock()

def lock_for(path: str) -> threading.RLock:
    resolved = os.path.realpath(path)
    with _locks_by_path_guard:
        return _locks_by_path.setdefault(resolved, threading.RLock())

def holds_file_lock(path_attribute: str = "path"):
    def decorate(method):
        @functools.wraps(method)
        def locked(self, *args, **kwargs):
            with lock_for(getattr(self, path_attribute)):
                return method(self, *args, **kwargs)
        return locked
    return decorate

def discard_temp_file(path: str) -> None:
    try:
        os.remove(path)
    except FileNotFoundError:
        logger.debug("temp file %s was already gone", path)
    except OSError as error:
        logger.warning("could not remove temp file %s: %s", path, error)

def report_unreadable_store(path: str, error: Exception) -> None:
    logger.error("could not read %s, so it is treated as empty and the next save replaces it: %s", path, error)

def atomic_write_json(path: str, data) -> None:
    directory, name = os.path.split(os.path.abspath(path))
    descriptor, tmp_file = tempfile.mkstemp(dir=directory, prefix=f".{name}.", suffix=".tmp")
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_file, path)
    except BaseException:
        discard_temp_file(tmp_file)
        raise

def set_aside_unreadable(path: str) -> str:
    aside = f"{path}.unreadable-{time.strftime('%Y%m%d-%H%M%S')}"
    os.replace(path, aside)
    return aside
