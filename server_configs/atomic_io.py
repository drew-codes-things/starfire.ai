import json
import os
import tempfile

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
        try:
            os.remove(tmp_file)
        except OSError:
            pass
        raise
