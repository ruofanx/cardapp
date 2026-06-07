"""
SQLite-backed data layer for the Pokemon Trading webapp.

Schema: users (id, name, avatar_color), cards (id, user_id, name, set_name,
card_number, language, variant, condition, is_graded, grade_company, grade,
purchase_price, purchase_date, current_market_price, last_priced_at,
photo_path, notes, created_at).

Three users seeded on first run: Ro, Reid, Ryan.
"""
from __future__ import annotations

import os
import sqlite3
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional

DB_PATH = Path(os.environ.get("POKEMON_DB", str(Path(__file__).parent / "pokemon_trading.sqlite")))

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    name         TEXT NOT NULL UNIQUE,
    avatar_color TEXT NOT NULL DEFAULT '#3b82f6'
);

CREATE TABLE IF NOT EXISTS cards (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id              INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    name                 TEXT NOT NULL,
    set_name             TEXT,
    card_number          TEXT,
    language             TEXT NOT NULL DEFAULT 'english',
    variant              TEXT,
    condition            TEXT NOT NULL DEFAULT 'NM',
    is_graded            INTEGER NOT NULL DEFAULT 0,
    grade_company        TEXT,
    grade                REAL,
    purchase_price       REAL,
    purchase_date        TEXT,
    current_market_price REAL,
    last_priced_at       TEXT,
    image_url            TEXT,
    photo_path           TEXT,
    notes                TEXT,
    created_at           TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_cards_user ON cards(user_id);

-- Tags (one tag per row, scoped to a user). is_trade_tag=1 marks the
-- "available for trade" tag — the trade proposer pulls candidates from
-- this tag's cards by default.
CREATE TABLE IF NOT EXISTS tags (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id      INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    name         TEXT NOT NULL,
    color        TEXT NOT NULL DEFAULT '#94a3b8',
    is_trade_tag INTEGER NOT NULL DEFAULT 0,
    UNIQUE (user_id, name)
);

CREATE INDEX IF NOT EXISTS idx_tags_user ON tags(user_id);

CREATE TABLE IF NOT EXISTS card_tags (
    card_id INTEGER NOT NULL REFERENCES cards(id) ON DELETE CASCADE,
    tag_id  INTEGER NOT NULL REFERENCES tags(id) ON DELETE CASCADE,
    PRIMARY KEY (card_id, tag_id)
);

CREATE INDEX IF NOT EXISTS idx_card_tags_card ON card_tags(card_id);
CREATE INDEX IF NOT EXISTS idx_card_tags_tag  ON card_tags(tag_id);

-- Point-in-time price log. One row per recorded snapshot. The Detail
-- screen reads this to draw the price chart. Rows are appended whenever
-- a card's current_market_price changes (via PATCH or refresh-price flow).
CREATE TABLE IF NOT EXISTS price_history (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    card_id     INTEGER NOT NULL REFERENCES cards(id) ON DELETE CASCADE,
    recorded_at TEXT    NOT NULL DEFAULT (datetime('now')),
    price_usd   REAL    NOT NULL,
    source      TEXT
);

CREATE INDEX IF NOT EXISTS idx_price_history_card_time
    ON price_history(card_id, recorded_at);
"""

SEED_USERS = [("Ro", "#3b82f6"), ("Reid", "#10b981"), ("Ryan", "#f59e0b")]

# Defaults each user gets on first run. The "for trade" tag is the
# trade-proposer's default candidate pool.
SEED_TAGS = [
    ("for trade", "#f97316", True),     # orange, marked as trade tag
    ("favorites", "#eab308", False),    # yellow
    ("binder",    "#3b82f6", False),    # blue
]


@dataclass
class User:
    id: int
    name: str
    avatar_color: str


@dataclass
class Card:
    id: Optional[int]
    user_id: int
    name: str
    set_name: Optional[str] = None
    card_number: Optional[str] = None
    language: str = "english"
    variant: Optional[str] = None
    condition: str = "NM"
    is_graded: bool = False
    grade_company: Optional[str] = None
    grade: Optional[float] = None
    purchase_price: Optional[float] = None
    purchase_date: Optional[str] = None
    current_market_price: Optional[float] = None
    last_priced_at: Optional[str] = None
    image_url: Optional[str] = None      # catalogue image (TCGplayer / TCGdex / Pokemon TCG API)
    photo_path: Optional[str] = None     # user-uploaded photo, if any
    notes: Optional[str] = None
    created_at: Optional[str] = None
    tags: list["Tag"] = field(default_factory=list)

    def gain_loss(self):
        if self.purchase_price is None or self.current_market_price is None:
            return None
        return self.current_market_price - self.purchase_price

    def gain_loss_pct(self):
        if not self.purchase_price or self.current_market_price is None:
            return None
        return (self.current_market_price - self.purchase_price) / self.purchase_price * 100


@dataclass
class Tag:
    id: int
    user_id: int
    name: str
    color: str
    is_trade_tag: bool = False
    card_count: int = 0   # populated by list_tags via a join


def init_db():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with connect() as conn:
        conn.executescript(SCHEMA)
        # Lightweight migration: add image_url to existing DBs that
        # predate this column. SQLite raises if it already exists.
        try:
            conn.execute("ALTER TABLE cards ADD COLUMN image_url TEXT")
        except sqlite3.OperationalError:
            pass
        for name, color in SEED_USERS:
            conn.execute("INSERT OR IGNORE INTO users (name, avatar_color) VALUES (?, ?)",
                         (name, color))
        # Seed default tags for every existing user (idempotent).
        for u in conn.execute("SELECT id FROM users").fetchall():
            for tname, tcolor, is_trade in SEED_TAGS:
                conn.execute(
                    "INSERT OR IGNORE INTO tags (user_id, name, color, is_trade_tag) "
                    "VALUES (?, ?, ?, ?)",
                    (u["id"], tname, tcolor, 1 if is_trade else 0),
                )
        conn.commit()
    # Backfill outside the schema transaction so its own commit is clean.
    try:
        backfill_price_history()
    except Exception:
        # Backfill is convenience-only — don't crash startup if it fails.
        pass


@contextmanager
def connect():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
    finally:
        conn.close()


def list_users() -> list[User]:
    with connect() as conn:
        rows = conn.execute("SELECT * FROM users ORDER BY id").fetchall()
        return [User(**dict(r)) for r in rows]


def get_user(user_id: int) -> Optional[User]:
    with connect() as conn:
        r = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        return User(**dict(r)) if r else None


def list_cards(user_id: int, *, tag_id: Optional[int] = None) -> list[Card]:
    """List a user's cards, optionally restricted to those bearing `tag_id`.
    Each Card has its tags list populated."""
    with connect() as conn:
        if tag_id is not None:
            rows = conn.execute(
                "SELECT c.* FROM cards c "
                "JOIN card_tags ct ON ct.card_id = c.id "
                "WHERE c.user_id = ? AND ct.tag_id = ? "
                "ORDER BY c.created_at DESC",
                (user_id, tag_id),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM cards WHERE user_id = ? ORDER BY created_at DESC",
                (user_id,),
            ).fetchall()
        cards = [_row_to_card(r) for r in rows]
        if cards:
            _attach_tags(conn, cards)
        return cards


def get_card(card_id: int) -> Optional[Card]:
    with connect() as conn:
        r = conn.execute("SELECT * FROM cards WHERE id = ?", (card_id,)).fetchone()
        if not r:
            return None
        c = _row_to_card(r)
        _attach_tags(conn, [c])
        return c


def _attach_tags(conn, cards: list[Card]) -> None:
    """Populate Card.tags for each card in `cards` (one query)."""
    ids = [c.id for c in cards if c.id is not None]
    if not ids:
        return
    placeholders = ",".join("?" * len(ids))
    rows = conn.execute(
        f"SELECT ct.card_id, t.id, t.user_id, t.name, t.color, t.is_trade_tag "
        f"FROM card_tags ct JOIN tags t ON t.id = ct.tag_id "
        f"WHERE ct.card_id IN ({placeholders})",
        ids,
    ).fetchall()
    by_card: dict[int, list[Tag]] = {}
    for r in rows:
        by_card.setdefault(r[0], []).append(Tag(
            id=r[1], user_id=r[2], name=r[3], color=r[4],
            is_trade_tag=bool(r[5]),
        ))
    for c in cards:
        c.tags = by_card.get(c.id or -1, [])


def create_card(card: Card) -> Card:
    fields = {k: v for k, v in asdict(card).items()
              if k not in ("id", "created_at", "tags")}  # tags is a join, not a column
    placeholders = ",".join(["?"] * len(fields))
    cols = ",".join(fields.keys())
    with connect() as conn:
        cur = conn.execute(f"INSERT INTO cards ({cols}) VALUES ({placeholders})",
                           tuple(fields.values()))
        conn.commit()
        return get_card(cur.lastrowid)  # type: ignore[return-value]


def update_card(card_id: int, **changes) -> Optional[Card]:
    if not changes:
        return get_card(card_id)
    cols = ",".join(f"{k}=?" for k in changes)
    with connect() as conn:
        conn.execute(f"UPDATE cards SET {cols} WHERE id = ?", (*changes.values(), card_id))
        conn.commit()
    return get_card(card_id)


def delete_card(card_id: int) -> bool:
    with connect() as conn:
        cur = conn.execute("DELETE FROM cards WHERE id = ?", (card_id,))
        conn.commit()
        return cur.rowcount > 0


def update_market_price(card_id: int, price: float) -> Optional[Card]:
    card = update_card(card_id, current_market_price=price,
                       last_priced_at=datetime.utcnow().isoformat())
    # Best-effort log; never block the update if logging fails.
    try:
        log_price(card_id, price)
    except Exception:
        pass
    return card


# ---------------------------------------------------------------------------
# Price history
# ---------------------------------------------------------------------------

def log_price(card_id: int, price_usd: float, source: Optional[str] = None,
              at: Optional[str] = None) -> None:
    """Append a row to price_history for this card. Idempotent against
    duplicates: if the most-recent row has the same price within the last
    60 seconds we skip — protects against double-logging when the UI
    re-fires refresh in quick succession."""
    if price_usd is None:
        return
    with connect() as conn:
        last = conn.execute(
            "SELECT recorded_at, price_usd FROM price_history "
            "WHERE card_id = ? ORDER BY recorded_at DESC LIMIT 1",
            (card_id,),
        ).fetchone()
        if last and abs(float(last["price_usd"]) - float(price_usd)) < 0.005:
            # Same price as last logged snapshot — don't spam the history.
            return
        if at:
            conn.execute(
                "INSERT INTO price_history (card_id, recorded_at, price_usd, source) "
                "VALUES (?, ?, ?, ?)",
                (card_id, at, float(price_usd), source),
            )
        else:
            conn.execute(
                "INSERT INTO price_history (card_id, price_usd, source) "
                "VALUES (?, ?, ?)",
                (card_id, float(price_usd), source),
            )
        conn.commit()


def get_price_history(card_id: int, *, since: Optional[str] = None,
                      limit: Optional[int] = None) -> list[dict]:
    """Return [{at, price}] for a card, oldest first. Optionally filter
    by ISO timestamp `since` and cap rows with `limit` (newest kept)."""
    with connect() as conn:
        if since:
            rows = conn.execute(
                "SELECT recorded_at, price_usd FROM price_history "
                "WHERE card_id = ? AND recorded_at >= ? "
                "ORDER BY recorded_at ASC",
                (card_id, since),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT recorded_at, price_usd FROM price_history "
                "WHERE card_id = ? ORDER BY recorded_at ASC",
                (card_id,),
            ).fetchall()
        out = [{"at": r["recorded_at"], "price": float(r["price_usd"])} for r in rows]
        if limit and len(out) > limit:
            out = out[-limit:]
        return out


def backfill_price_history() -> int:
    """Seed price_history for cards that have current_market_price but no
    history rows yet. Inserts a single row dated at last_priced_at (or
    created_at) so the chart shows at least one anchor point. Returns the
    number of rows inserted. Idempotent."""
    inserted = 0
    with connect() as conn:
        rows = conn.execute(
            "SELECT c.id, c.current_market_price, "
            "       COALESCE(c.last_priced_at, c.created_at) AS at "
            "FROM cards c "
            "LEFT JOIN price_history ph ON ph.card_id = c.id "
            "WHERE c.current_market_price IS NOT NULL "
            "GROUP BY c.id "
            "HAVING COUNT(ph.id) = 0"
        ).fetchall()
        for r in rows:
            conn.execute(
                "INSERT INTO price_history (card_id, recorded_at, price_usd, source) "
                "VALUES (?, ?, ?, ?)",
                (r["id"], r["at"], float(r["current_market_price"]), "backfill"),
            )
            inserted += 1
        if inserted:
            conn.commit()
    return inserted


# ---------------------------------------------------------------------------
# Tags
# ---------------------------------------------------------------------------

def list_tags(user_id: int) -> list[Tag]:
    """List a user's tags with card_count populated. Sorted: trade tag first,
    then alphabetical."""
    with connect() as conn:
        rows = conn.execute(
            "SELECT t.id, t.user_id, t.name, t.color, t.is_trade_tag, "
            "       COUNT(ct.card_id) AS card_count "
            "FROM tags t LEFT JOIN card_tags ct ON ct.tag_id = t.id "
            "WHERE t.user_id = ? "
            "GROUP BY t.id "
            "ORDER BY t.is_trade_tag DESC, t.name ASC",
            (user_id,),
        ).fetchall()
        return [Tag(
            id=r["id"], user_id=r["user_id"], name=r["name"],
            color=r["color"], is_trade_tag=bool(r["is_trade_tag"]),
            card_count=r["card_count"],
        ) for r in rows]


def get_tag(tag_id: int) -> Optional[Tag]:
    with connect() as conn:
        r = conn.execute(
            "SELECT id, user_id, name, color, is_trade_tag FROM tags WHERE id = ?",
            (tag_id,),
        ).fetchone()
        if not r:
            return None
        return Tag(id=r["id"], user_id=r["user_id"], name=r["name"],
                   color=r["color"], is_trade_tag=bool(r["is_trade_tag"]))


def create_tag(user_id: int, name: str, color: str = "#94a3b8",
               is_trade_tag: bool = False) -> Tag:
    name = name.strip()
    if not name:
        raise ValueError("tag name required")
    with connect() as conn:
        cur = conn.execute(
            "INSERT INTO tags (user_id, name, color, is_trade_tag) "
            "VALUES (?, ?, ?, ?)",
            (user_id, name, color, 1 if is_trade_tag else 0),
        )
        conn.commit()
        return get_tag(cur.lastrowid)  # type: ignore[return-value]


def delete_tag(tag_id: int) -> bool:
    with connect() as conn:
        cur = conn.execute("DELETE FROM tags WHERE id = ?", (tag_id,))
        conn.commit()
        return cur.rowcount > 0


def update_tag(tag_id: int, **changes) -> Optional[Tag]:
    if not changes:
        return get_tag(tag_id)
    if "is_trade_tag" in changes:
        changes["is_trade_tag"] = 1 if changes["is_trade_tag"] else 0
    cols = ",".join(f"{k}=?" for k in changes)
    with connect() as conn:
        conn.execute(f"UPDATE tags SET {cols} WHERE id = ?",
                     (*changes.values(), tag_id))
        conn.commit()
    return get_tag(tag_id)


def add_tag_to_card(card_id: int, tag_id: int) -> bool:
    with connect() as conn:
        try:
            conn.execute(
                "INSERT OR IGNORE INTO card_tags (card_id, tag_id) VALUES (?, ?)",
                (card_id, tag_id),
            )
            conn.commit()
            return True
        except sqlite3.IntegrityError:
            return False


def remove_tag_from_card(card_id: int, tag_id: int) -> bool:
    with connect() as conn:
        cur = conn.execute(
            "DELETE FROM card_tags WHERE card_id = ? AND tag_id = ?",
            (card_id, tag_id),
        )
        conn.commit()
        return cur.rowcount > 0


# ---------------------------------------------------------------------------
# Portfolio summary
# ---------------------------------------------------------------------------

@dataclass
class PortfolioSummary:
    user_id: int
    user_name: str
    card_count: int
    total_purchase_price: float
    total_market_value: float
    unrealized_gain_loss: float
    gain_loss_pct: float
    bulk_count: int
    untracked_count: int


def portfolio_summary(user_id: int) -> PortfolioSummary:
    user = get_user(user_id)
    if not user:
        raise ValueError(f"unknown user_id {user_id}")
    cards = list_cards(user_id)
    total_purchase = sum(c.purchase_price or 0.0 for c in cards)
    total_market = sum(c.current_market_price or 0.0 for c in cards)
    bulk = sum(1 for c in cards if (c.current_market_price or 0) < 5)
    untracked = sum(1 for c in cards if c.current_market_price is None)
    gain = total_market - total_purchase
    pct = (gain / total_purchase * 100) if total_purchase > 0 else 0.0
    return PortfolioSummary(
        user_id=user_id, user_name=user.name,
        card_count=len(cards),
        total_purchase_price=round(total_purchase, 2),
        total_market_value=round(total_market, 2),
        unrealized_gain_loss=round(gain, 2),
        gain_loss_pct=round(pct, 2),
        bulk_count=bulk,
        untracked_count=untracked,
    )


def _row_to_card(r) -> Card:
    d = dict(r)
    d["is_graded"] = bool(d.get("is_graded", 0))
    # `tags` is populated separately by _attach_tags; the schema has no column.
    return Card(**d, tags=[])
