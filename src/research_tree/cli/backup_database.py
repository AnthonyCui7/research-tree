"""`research-tree-backup`: `pg_dump` the database into the backups container.

Supabase's free plan keeps no backups, so this is the only copy. The dump is
Postgres' custom format (compressed, restorable table by table) streamed
straight into Blob Storage; the newest `--keep` dumps are retained.

Restore, from a machine with `pg_restore` 17 and the admin DSN:

    az storage blob download --account-name researchtree7 -c backups \\
        -n researchtree-YYYYMMDD.dump -f backup.dump --auth-mode login
    pg_restore --clean --if-exists --no-owner -d "$ADMIN_DSN" backup.dump
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

from research_tree.artifact_store import blob_account_url
from research_tree.db import DATABASE_URL_ENV, database_url, plain_postgres_dsn

BACKUPS_CONTAINER = "backups"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="research-tree-backup", description=__doc__)
    parser.add_argument("--keep", type=int, default=8, help="how many dumps to retain (default 8)")
    parser.add_argument("--to-file", default=None, help="write the dump to a local file instead of Blob Storage")
    args = parser.parse_args(argv)

    url = database_url()
    if not url:
        print(f"{DATABASE_URL_ENV} is not set.", file=sys.stderr)
        return 2
    name = f"researchtree-{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}.dump"
    dump = run_pg_dump(plain_postgres_dsn(url))
    if args.to_file:
        Path(args.to_file).write_bytes(dump)
        print(f"wrote {args.to_file} ({len(dump)} bytes)")
        return 0
    account_url = blob_account_url()
    if not account_url:
        print("RESEARCH_TREE_BLOB_ACCOUNT_URL is not set; use --to-file.", file=sys.stderr)
        return 2
    upload_backup(account_url, name, dump, keep=args.keep)
    print(f"uploaded {name} ({len(dump)} bytes) to {BACKUPS_CONTAINER}")
    return 0


def run_pg_dump(dsn: str) -> bytes:
    # --no-owner/--no-privileges: the restore target's roles differ from the
    # source's, and neither matters for the data. --schema=public: everything
    # of ours lives there, and it is all the app role can read, so the worker
    # takes the backup with its own credentials.
    completed = subprocess.run(
        [
            "pg_dump",
            "--format=custom",
            "--no-owner",
            "--no-privileges",
            "--schema=public",
            "--dbname",
            dsn,
        ],
        check=False,
        capture_output=True,
    )
    if completed.returncode != 0:
        raise RuntimeError(f"pg_dump failed: {completed.stderr.decode('utf-8', 'replace')[:2000]}")
    return completed.stdout


def upload_backup(account_url: str, name: str, dump: bytes, *, keep: int) -> None:
    from azure.identity import DefaultAzureCredential
    from azure.storage.blob import ContainerClient

    client = ContainerClient(
        account_url=account_url, container_name=BACKUPS_CONTAINER, credential=DefaultAzureCredential()
    )
    client.upload_blob(name, dump, overwrite=False)
    existing = sorted(
        (blob.name for blob in client.list_blobs(name_starts_with="researchtree-")),
        reverse=True,
    )
    for stale in existing[max(keep, 1):]:
        client.delete_blob(stale)


if __name__ == "__main__":
    raise SystemExit(main())
