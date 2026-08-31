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
