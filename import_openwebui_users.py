import argparse
import os
import sqlite3
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "server_configs"))

from dotenv import load_dotenv

load_dotenv()

from auth import ROLES, UserStore
from config import config

ACCOUNTS_QUERY = """
    select user.email, user.name, user.role, auth.password
    from auth join user on user.id = auth.id
    where auth.active = 1
"""


def read_openwebui_accounts(database_path: str) -> list[tuple[str, str, str, str]]:
    if not os.path.isfile(database_path):
        raise FileNotFoundError(f"no Open WebUI database at {database_path}")
    connection = sqlite3.connect(f"file:{database_path}?mode=ro", uri=True)
    try:
        return connection.execute(ACCOUNTS_QUERY).fetchall()
    finally:
        connection.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="Copy Open WebUI logins into starfire.ai. "
                                                 "Passwords stay the same; only their bcrypt hashes are copied.")
    parser.add_argument("database", help="path to Open WebUI's webui.db")
    args = parser.parse_args()

    users = UserStore(config.data_dir)
    imported, skipped = [], []
    for email, name, role, password_hash in read_openwebui_accounts(args.database):
        if role not in ROLES:
            skipped.append(f"{name or email} (role {role})")
            continue
        user = users.upsert(email=email, name=name or "", role=role, password_hash=password_hash)
        imported.append(f"{user.name} ({user.role})")

    print(f"imported {len(imported)} account(s): {', '.join(imported) or 'none'}")
    if skipped:
        print(f"skipped {len(skipped)} account(s) that aren't approved users: {', '.join(skipped)}")
    return 0 if imported else 1


if __name__ == "__main__":
    sys.exit(main())
