"""Attach legacy Ro/Reid/Ryan profiles (account_id IS NULL) to one account.

Login used to claim every unowned profile for whoever signed in first. That
auto-claim is gone. Existing cards stay on those profiles, but the profiles
are invisible until you attach them to your Supabase account.

Do this once, before anyone else signs up on a database that still has
NULL account_id rows:

1. Sign in to the app once (or look up your user in the Supabase
   Authentication dashboard). That creates your row in `accounts`.
2. Copy that user id (the JWT `sub`, same as `accounts.id`).
3. From webapp/, with DATABASE_URL set:

       LEGACY_ACCOUNT_ID="<that uuid>" python claim_legacy_profiles.py --dry-run
       LEGACY_ACCOUNT_ID="<that uuid>" python claim_legacy_profiles.py

The script refuses to run if that accounts row does not exist, so a typo
cannot stick the collection to an id nobody will ever log in as. It only
updates rows where account_id IS NULL, so running it again is a no-op once
the profiles are attached.

If you already logged in under the old code, your profiles are already
claimed and this script prints nothing to do.
"""
from __future__ import annotations

import os
import sys

from dotenv import load_dotenv

load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), "../.env"))

import db_postgres as db


def main() -> int:
    account_id = os.environ.get("LEGACY_ACCOUNT_ID", "").strip()
    if not account_id:
        print("Set LEGACY_ACCOUNT_ID to your Supabase user id (accounts.id).", file=sys.stderr)
        return 2
    dry_run = "--dry-run" in sys.argv
    try:
        rows = db.claim_unowned_profiles(account_id, dry_run=dry_run)
    except ValueError as e:
        print(str(e), file=sys.stderr)
        return 1
    if not rows:
        print("No unowned profiles. Nothing to do.")
        return 0
    verb = "Would attach" if dry_run else "Attached"
    for row in rows:
        print(f"{verb} profile {row['id']} ({row['name']}) to account {account_id}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
