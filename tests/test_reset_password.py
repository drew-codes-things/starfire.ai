import os
import subprocess
import sys

import bcrypt

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "server_configs"))

from auth import SessionStore, UserStore


def fast_hash(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt(rounds=4)).decode()


def run_reset(data_dir, *args):
    return subprocess.run([sys.executable, os.path.join(ROOT, "reset_password.py"), *args],
                          env={**os.environ, "DATA_DIR": str(data_dir)}, cwd=ROOT, capture_output=True, text=True)


def test_reset_changes_the_password_and_logs_everyone_out(tmp_path):
    users = UserStore(str(tmp_path))
    user = users.upsert("drew@example.com", "Drew", "admin", fast_hash("old password here"))
    sessions = SessionStore(str(tmp_path))
    token = sessions.create(user.id)
    assert sessions.user_id_for(token) == user.id

    result = run_reset(tmp_path, "drew@example.com", "--password", "brand new password")
    assert result.returncode == 0, result.stderr
    assert "password updated for drew@example.com" in result.stdout
    assert UserStore(str(tmp_path)).verify("drew@example.com", "brand new password")
    assert not UserStore(str(tmp_path)).verify("drew@example.com", "old password here")
    assert SessionStore(str(tmp_path)).user_id_for(token) is None


def test_reset_rejects_unknown_accounts_and_short_passwords(tmp_path):
    UserStore(str(tmp_path)).upsert("drew@example.com", "Drew", "admin", fast_hash("old password here"))
    assert run_reset(tmp_path, "nobody@example.com", "--password", "long enough password").returncode == 1
    short = run_reset(tmp_path, "drew@example.com", "--password", "short")
    assert short.returncode == 1 and "at least" in short.stderr
