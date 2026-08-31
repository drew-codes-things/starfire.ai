"""Shared atomic JSON write.

Every *_store.py module used to reimplement the same temp-file + fsync +
os.replace pattern independently. Factored into one place here, mirroring
odysseus-dev's own core/atomic_io.py — one spot to fix if the write strategy
ever needs to change, instead of eight.
"""

import json
import os


def atomic_write_json(path: str, data) -> None:
    tmp_file = path + ".tmp"
    try:
        with open(tmp_file, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_file, path)
    except OSError:
        try:
            os.remove(tmp_file)
        except OSError:
            pass
        raise
