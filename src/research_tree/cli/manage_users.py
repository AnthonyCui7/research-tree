"""`research-tree-users`: the operator's view of the account table.

Run inside the deployed container (`az containerapp exec`) or locally against
the same database URL. Until emailed verification exists, `--verify` is how
an account the operator has checked out of band becomes eligible for a
sponsored allowance.
"""

from __future__ import annotations

import argparse
import sys

from research_tree.auth.accounts import list_users, set_user_flags
from research_tree.db import DATABASE_URL_ENV, database_url


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="research-tree-users", description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--list", action="store_true", help="list every account")
    group.add_argument("--verify", metavar="USER_ID", help="mark an account's email as verified")
    group.add_argument("--set-admin", metavar="USER_ID", help="grant operator rights")
    group.add_argument("--unset-admin", metavar="USER_ID", help="remove operator rights")
    group.add_argument(
        "--deactivate", metavar="USER_ID", help="disable sign-in and revoke sessions"
    )
    group.add_argument("--activate", metavar="USER_ID", help="re-enable a deactivated account")
    args = parser.parse_args(argv)

    if database_url() is None:
        print(f"{DATABASE_URL_ENV} is not set.", file=sys.stderr)
        return 2

    if args.list:
        users = list_users()
        if not users:
            print("no accounts")
            return 0
        for user in users:
            flags = [
                name
                for name, value in (
                    ("admin", user["is_superuser"]),
                    ("verified", user["is_verified"]),
                    ("inactive", not user["is_active"]),
                )
                if value
            ]
            last_seen = user["last_seen_at"].isoformat() if user["last_seen_at"] else "never"
            print(
                f"{user['id']}  {user['email']:<40}  {' '.join(flags) or '-':<18}  "
                f"last seen {last_seen}"
            )
        return 0

    actions = {
        "verify": ("is_verified", True),
        "set_admin": ("is_superuser", True),
        "unset_admin": ("is_superuser", False),
        "deactivate": ("is_active", False),
        "activate": ("is_active", True),
    }
    for option, (flag, value) in actions.items():
        user_id = getattr(args, option)
        if user_id:
            if set_user_flags(user_id, **{flag: value}):
                print(f"{option.replace('_', ' ')}: {user_id}")
                return 0
            print(f"no account with id {user_id}", file=sys.stderr)
            return 1
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
