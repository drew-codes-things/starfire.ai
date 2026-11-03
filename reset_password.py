import argparse
import getpass
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "server_configs"))

from dotenv import load_dotenv

load_dotenv()

from auth import AccountError, SessionStore, UserStore
from config import config


def main() -> int:
    parser = argparse.ArgumentParser(description="Set a new password for a starfire.ai account and log out "
                                                 "every browser that was signed in to it.")
    parser.add_argument("email", help="the account's email address")
    parser.add_argument("--password", help="the new password (prompted for if omitted)")
    args = parser.parse_args()

    users = UserStore(config.data_dir)
    user = users.find_by_email(args.email)
    if not user:
        known = ", ".join(u.email for u in users.list()) or "none"
        print(f"no account for {args.email} in {users.path} (accounts: {known})", file=sys.stderr)
        return 1

    password = args.password
    if password is None:
        password = getpass.getpass("new password: ")
        confirm = getpass.getpass("new password again: ")
    else:
        confirm = password

    try:
        users.set_password(user.id, password, confirm)
    except AccountError as e:
        print(f"not changed: {e}", file=sys.stderr)
        return 1

    SessionStore(config.data_dir).revoke_all_for(user.id)
    print(f"password updated for {user.email}; every signed-in browser has been logged out")
    return 0


if __name__ == "__main__":
    sys.exit(main())
