"""
One-time migration: SQLite (pokemon_trading.sqlite) → Supabase PostgreSQL.

Run from webapp/ directory after setting DATABASE_URL in .env:
    python migrate_sqlite_to_postgres.py [--sqlite PATH] [--dry-run]

Migration order (FK-safe):
    users → tags → cards → card_tags → price_history
"""
import argparse
import sqlite3
import os
import sys
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(dotenv_path=str(Path(__file__).parent.parent / ".env"))

sys.path.insert(0, str(Path(__file__).parent))
import db_postgres as pg


def migrate(sqlite_path: str, dry_run: bool = False):
    src = sqlite3.connect(sqlite_path)
    src.row_factory = sqlite3.Row

    # -- Users --
    users = src.execute("SELECT * FROM users ORDER BY id").fetchall()
    user_id_map: dict[int, int] = {}  # sqlite id → postgres id
    print(f"Migrating {len(users)} users...")
    for u in users:
        if dry_run:
            print(f"  [dry] would insert user: {u['name']}")
            continue
        existing = next((x for x in pg.list_users() if x.name == u["name"]), None)
        if existing:
            user_id_map[u["id"]] = existing.id
            print(f"  ~ user '{u['name']}' already exists → pg id {existing.id}")
        else:
            new_user = pg.create_user(u["name"], u["avatar_color"])
            user_id_map[u["id"]] = new_user.id
            print(f"  ✓ user '{u['name']}' → pg id {new_user.id}")

    if dry_run:
        print("\nDry run complete — no data written.")
        return

    # -- Tags --
    tags = src.execute("SELECT * FROM tags ORDER BY id").fetchall()
    tag_id_map: dict[int, int] = {}
    print(f"\nMigrating {len(tags)} tags...")
    for t in tags:
        pg_user_id = user_id_map.get(t["user_id"])
        if not pg_user_id:
            print(f"  ~ skipping tag '{t['name']}' (user not migrated)")
            continue
        existing_tags = pg.list_tags(pg_user_id)
        existing = next((x for x in existing_tags if x.name == t["name"]), None)
        if existing:
            tag_id_map[t["id"]] = existing.id
            print(f"  ~ tag '{t['name']}' already exists → pg id {existing.id}")
        else:
            new_tag = pg.create_tag(pg_user_id, t["name"], t["color"],
                                    is_trade_tag=bool(t["is_trade_tag"]))
            tag_id_map[t["id"]] = new_tag.id
            print(f"  ✓ tag '{t['name']}' → pg id {new_tag.id}")

    # -- Cards --
    cards = src.execute("SELECT * FROM cards ORDER BY id").fetchall()
    card_id_map: dict[int, int] = {}
    print(f"\nMigrating {len(cards)} cards...")
    for c in cards:
        pg_user_id = user_id_map.get(c["user_id"])
        if not pg_user_id:
            print(f"  ~ skipping card '{c['name']}' (user not migrated)")
            continue
        from db_postgres import Card
        new_card = pg.create_card(Card(
            id=None,
            user_id=pg_user_id,
            name=c["name"],
            set_name=c["set_name"],
            card_number=c["card_number"],
            language=c["language"] or "english",
            variant=c["variant"],
            condition=c["condition"] or "NM",
            is_graded=bool(c["is_graded"]),
            grade_company=c["grade_company"],
            grade=c["grade"],
            purchase_price=c["purchase_price"],
            purchase_date=c["purchase_date"],
            current_market_price=c["current_market_price"],
            last_priced_at=c["last_priced_at"],
            image_url=c["image_url"],
            photo_path=c["photo_path"],
            notes=c["notes"],
            product_type=dict(c).get("product_type") or "card",
        ))
        card_id_map[c["id"]] = new_card.id
        print(f"  ✓ card '{c['name']}' → pg id {new_card.id}")

    # -- Card tags (join table) --
    ct_rows = src.execute("SELECT * FROM card_tags").fetchall()
    print(f"\nMigrating {len(ct_rows)} card-tag associations...")
    linked = 0
    for ct in ct_rows:
        pg_card_id = card_id_map.get(ct["card_id"])
        pg_tag_id  = tag_id_map.get(ct["tag_id"])
        if pg_card_id and pg_tag_id:
            pg.add_tag_to_card(pg_card_id, pg_tag_id)
            linked += 1
    print(f"  ✓ {linked} associations linked")

    # -- Price history --
    ph_rows = src.execute(
        "SELECT * FROM price_history ORDER BY recorded_at ASC"
    ).fetchall()
    print(f"\nMigrating {len(ph_rows)} price history rows...")
    logged = 0
    for ph in ph_rows:
        pg_card_id = card_id_map.get(ph["card_id"])
        if pg_card_id:
            pg.log_price(pg_card_id, float(ph["price_usd"]),
                         source=dict(ph).get("source"), at=ph["recorded_at"])
            logged += 1
    print(f"  ✓ {logged} price history rows logged")

    print("\n" + "="*40)
    print("Migration complete.")
    print(f"  Users:         {len(user_id_map)}")
    print(f"  Tags:          {len(tag_id_map)}")
    print(f"  Cards:         {len(card_id_map)}")
    print(f"  Card-tag links:{linked}")
    print(f"  Price history: {logged}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Migrate SQLite data to Supabase PostgreSQL."
    )
    parser.add_argument(
        "--sqlite",
        default="pokemon_trading.sqlite",
        help="Path to source SQLite file (default: pokemon_trading.sqlite)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print what would be migrated without writing to PostgreSQL",
    )
    args = parser.parse_args()

    if not os.environ.get("DATABASE_URL"):
        print("ERROR: DATABASE_URL not set. Add it to .env before running.")
        sys.exit(1)

    migrate(args.sqlite, dry_run=args.dry_run)
