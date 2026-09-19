"""`research-tree-grant`: let an account spend a bounded amount of the platform key.

Run inside the deployed container (`az containerapp exec`) or locally against
the same database URL. The allowance is granted to an email; it attaches to
the account with that email once the account is verified, and the API charges
each sponsored model call against it.
"""

from __future__ import annotations

import argparse
import sys
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation

# numeric(12, 6) holds six decimal places, so six integer digits.
MAX_ALLOWANCE_USD = Decimal("1000000")

from research_tree.billing.allowances import grant_allowance, list_allowances, revoke_allowance
from research_tree.db import DATABASE_URL_ENV, database_url


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="research-tree-grant", description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--email", help="grant an allowance to this email")
    group.add_argument("--list", action="store_true", help="list every allowance")
    group.add_argument("--revoke", metavar="ALLOWANCE_ID", help="revoke one allowance")
    parser.add_argument(
        "--usd",
        help="how much to add, e.g. 5 or 12.50; a negative amount takes credit away",
    )
    parser.add_argument(
        "--monthly", action="store_true", help="reset the amount every month instead of once"
    )
    parser.add_argument("--expires", metavar="YYYY-MM-DD", help="stop honouring it after this day")
    parser.add_argument("--note", help="why it was granted")
    parser.add_argument("--by", default="cli", help="who granted it (recorded as is)")
    args = parser.parse_args(argv)

    if database_url() is None:
        print(f"{DATABASE_URL_ENV} is not set.", file=sys.stderr)
        return 2

    if args.list:
        allowances = list_allowances()
        if not allowances:
            print("no allowances")
            return 0
        for item in allowances:
            state = item["status"]
            if state == "active" and item["exhausted"]:
                state = "exhausted"
            attached = "attached" if item["linked"] else "waiting for a verified account"
            expires = f", expires {item['expires_at'][:10]}" if item["expires_at"] else ""
            print(
                f"{item['id']}  {item['email']:<40}  ${item['spent_usd']:.2f} of "
                f"${item['limit_usd']:.2f} {item['period']}{expires}  {state}, {attached}"
                + (f"  ({item['note']})" if item["note"] else "")
            )
        return 0

    if args.revoke:
        if revoke_allowance(args.revoke):
            print(f"revoked {args.revoke}")
            return 0
        print(f"no active allowance with id {args.revoke}", file=sys.stderr)
        return 1

    if not args.usd:
        parser.error("--email needs --usd")
    try:
        amount = Decimal(args.usd)
    except InvalidOperation:
        parser.error(f"--usd {args.usd!r} is not an amount")
    # `limit_usd` is numeric(12, 6), so anything larger is a database error
    # rather than a grant. Say so here instead of printing a stack trace.
    if not amount.is_finite() or abs(amount) >= MAX_ALLOWANCE_USD:
        parser.error(f"--usd must be between -{MAX_ALLOWANCE_USD:,} and {MAX_ALLOWANCE_USD:,}")
    expires_at = None
    if args.expires:
        try:
            expires_at = datetime.strptime(args.expires, "%Y-%m-%d").replace(
                hour=23, minute=59, second=59, tzinfo=UTC
            )
        except ValueError:
            parser.error(f"--expires {args.expires!r} is not a YYYY-MM-DD date")
    try:
        allowance_id = grant_allowance(
            email=args.email,
            limit_usd=amount,
            period="monthly" if args.monthly else None,
            expires_at=expires_at,
            granted_by=args.by,
            note=args.note,
        )
    except ValueError as error:
        print(str(error), file=sys.stderr)
        return 1
    print(f"granted {allowance_id}: ${amount:.2f} {'monthly' if args.monthly else 'once'} to {args.email}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
