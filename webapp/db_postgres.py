"""
PostgreSQL data layer for PokeCollect — drop-in replacement for db.py.

Uses psycopg2 with per-call connections. Requires DATABASE_URL env var.
Public interface is identical to db.py so app.py needs only one import change.
"""
from __future__ import annotations

import os
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Optional

import psycopg2
import psycopg2.extras
from dotenv import load_dotenv

load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), "../.env"))

_DATABASE_URL = os.environ.get("DATABASE_URL")

_CARD_COLUMNS = frozenset({
    "name", "set_name", "card_number", "language", "variant", "condition",
    "is_graded", "grade_company", "grade", "purchase_price", "purchase_date",
    "current_market_price", "last_priced_at", "image_url", "photo_path",
    "notes", "product_type",
})

_TAG_COLUMNS = frozenset({"name", "color", "is_trade_tag"})


@contextmanager
def connect():
    conn = psycopg2.connect(_DATABASE_URL, cursor_factory=psycopg2.extras.RealDictCursor)
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Dataclasses (identical to db.py)
# ---------------------------------------------------------------------------

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
    image_url: Optional[str] = None
    photo_path: Optional[str] = None
    notes: Optional[str] = None
    created_at: Optional[str] = None
    tags: list["Tag"] = field(default_factory=list)
    product_type: str = "card"

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
    card_count: int = 0


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
    sealed_count: int = 0
    total_sealed_value: float = 0.0
    total_cards_value: float = 0.0


# ---------------------------------------------------------------------------
# Schema init (called at startup)
# ---------------------------------------------------------------------------

SEED_USERS = [("Ro", "#3b82f6"), ("Reid", "#10b981"), ("Ryan", "#f59e0b")]
SEED_TAGS = [
    ("for trade", "#f97316", True),
    ("favorites", "#eab308", False),
    ("binder", "#3b82f6", False),
]


def init_db():
    schema_path = os.path.join(os.path.dirname(__file__), "schema.sql")
    with open(schema_path) as f:
        schema_sql = f.read()
    with connect() as conn:
        cur = conn.cursor()
        cur.execute(schema_sql)
        for name, color in SEED_USERS:
            cur.execute(
                "INSERT INTO users (name, avatar_color) VALUES (%s, %s) ON CONFLICT (name) DO NOTHING",
                (name, color),
            )
        for row in _fetchall(cur, "SELECT id FROM users"):
            for tname, tcolor, is_trade in SEED_TAGS:
                cur.execute(
                    "INSERT INTO tags (user_id, name, color, is_trade_tag) "
                    "VALUES (%s, %s, %s, %s) ON CONFLICT (user_id, name) DO NOTHING",
                    (row["id"], tname, tcolor, is_trade),
                )
    try:
        backfill_price_history()
    except Exception:
        pass


def _fetchall(cur, sql, params=()):
    cur.execute(sql, params)
    return cur.fetchall() or []


def _fetchone(cur, sql, params=()):
    cur.execute(sql, params)
    return cur.fetchone()


# ---------------------------------------------------------------------------
# Users
# ---------------------------------------------------------------------------

def list_users() -> list[User]:
    with connect() as conn:
        cur = conn.cursor()
        rows = _fetchall(cur, "SELECT id, name, avatar_color FROM users ORDER BY id")
        return [User(**dict(r)) for r in rows]


def get_user(user_id: int) -> Optional[User]:
    with connect() as conn:
        cur = conn.cursor()
        r = _fetchone(cur, "SELECT id, name, avatar_color FROM users WHERE id = %s", (user_id,))
        return User(**dict(r)) if r else None


def create_user(name: str, avatar_color: str = "#3b82f6") -> User:
    with connect() as conn:
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO users (name, avatar_color) VALUES (%s, %s) RETURNING id",
            (name, avatar_color),
        )
        user_id = cur.fetchone()["id"]
        for tname, tcolor, is_trade in SEED_TAGS:
            cur.execute(
                "INSERT INTO tags (user_id, name, color, is_trade_tag) "
                "VALUES (%s, %s, %s, %s) ON CONFLICT DO NOTHING",
                (user_id, tname, tcolor, is_trade),
            )
        return User(id=user_id, name=name, avatar_color=avatar_color)


def delete_user(user_id: int) -> bool:
    with connect() as conn:
        cur = conn.cursor()
        cur.execute("DELETE FROM users WHERE id = %s", (user_id,))
        return cur.rowcount > 0


# ---------------------------------------------------------------------------
# Cards
# ---------------------------------------------------------------------------

def list_cards(user_id: int, *, tag_id: Optional[int] = None) -> list[Card]:
    with connect() as conn:
        cur = conn.cursor()
        if tag_id is not None:
            rows = _fetchall(cur,
                "SELECT c.* FROM cards c "
                "JOIN card_tags ct ON ct.card_id = c.id "
                "WHERE c.user_id = %s AND ct.tag_id = %s "
                "ORDER BY c.created_at DESC",
                (user_id, tag_id),
            )
        else:
            rows = _fetchall(cur,
                "SELECT * FROM cards WHERE user_id = %s ORDER BY created_at DESC",
                (user_id,),
            )
        cards = [_row_to_card(r) for r in rows]
        if cards:
            _attach_tags(cur, cards)
        return cards


def get_card(card_id: int) -> Optional[Card]:
    with connect() as conn:
        cur = conn.cursor()
        r = _fetchone(cur, "SELECT * FROM cards WHERE id = %s", (card_id,))
        if not r:
            return None
        c = _row_to_card(r)
        _attach_tags(cur, [c])
        return c


def create_card(card: Card) -> Card:
    fields = {k: v for k, v in asdict(card).items()
              if k not in ("id", "created_at", "tags")}
    cols = ", ".join(fields.keys())
    placeholders = ", ".join(["%s"] * len(fields))
    with connect() as conn:
        cur = conn.cursor()
        cur.execute(
            f"INSERT INTO cards ({cols}) VALUES ({placeholders}) RETURNING id",
            tuple(fields.values()),
        )
        new_id = cur.fetchone()["id"]
    return get_card(new_id)


def update_card(card_id: int, **changes) -> Optional[Card]:
    if not changes:
        return get_card(card_id)
    unknown = set(changes) - _CARD_COLUMNS
    if unknown:
        raise ValueError(f"unknown card columns: {unknown}")
    cols = ", ".join(f"{k} = %s" for k in changes)
    with connect() as conn:
        cur = conn.cursor()
        cur.execute(f"UPDATE cards SET {cols} WHERE id = %s",
                    (*changes.values(), card_id))
    return get_card(card_id)


def delete_card(card_id: int) -> bool:
    with connect() as conn:
        cur = conn.cursor()
        cur.execute("DELETE FROM cards WHERE id = %s", (card_id,))
        return cur.rowcount > 0


def update_market_price(card_id: int, price: float) -> Optional[Card]:
    card = update_card(card_id, current_market_price=price,
                       last_priced_at=datetime.now(timezone.utc).isoformat())
    try:
        log_price(card_id, price)
    except Exception:
        pass
    return card


def _row_to_card(r) -> Card:
    d = dict(r)
    d["is_graded"] = bool(d.get("is_graded", False))
    d.setdefault("product_type", "card")
    if d.get("last_priced_at") and not isinstance(d["last_priced_at"], str):
        d["last_priced_at"] = d["last_priced_at"].isoformat()
    if d.get("created_at") and not isinstance(d["created_at"], str):
        d["created_at"] = d["created_at"].isoformat()
    return Card(**d, tags=[])


def _attach_tags(cur, cards: list[Card]) -> None:
    ids = [c.id for c in cards if c.id is not None]
    if not ids:
        return
    placeholders = ", ".join(["%s"] * len(ids))
    rows = _fetchall(cur,
        f"SELECT ct.card_id, t.id, t.user_id, t.name, t.color, t.is_trade_tag "
        f"FROM card_tags ct JOIN tags t ON t.id = ct.tag_id "
        f"WHERE ct.card_id IN ({placeholders})",
        ids,
    )
    by_card: dict[int, list[Tag]] = {}
    for r in rows:
        by_card.setdefault(r["card_id"], []).append(Tag(
            id=r["id"], user_id=r["user_id"], name=r["name"],
            color=r["color"], is_trade_tag=bool(r["is_trade_tag"]),
        ))
    for c in cards:
        c.tags = by_card.get(c.id or -1, [])


# ---------------------------------------------------------------------------
# Price history
# ---------------------------------------------------------------------------

def log_price(card_id: int, price_usd: float, source: Optional[str] = None,
              at: Optional[str] = None) -> None:
    if price_usd is None:
        return
    with connect() as conn:
        cur = conn.cursor()
        last = _fetchone(cur,
            "SELECT price_usd FROM price_history "
            "WHERE card_id = %s ORDER BY recorded_at DESC LIMIT 1",
            (card_id,),
        )
        if last and abs(float(last["price_usd"]) - float(price_usd)) < 0.005:
            return
        if at:
            cur.execute(
                "INSERT INTO price_history (card_id, recorded_at, price_usd, source) "
                "VALUES (%s, %s, %s, %s)",
                (card_id, at, float(price_usd), source),
            )
        else:
            cur.execute(
                "INSERT INTO price_history (card_id, price_usd, source) VALUES (%s, %s, %s)",
                (card_id, float(price_usd), source),
            )


def get_price_history(card_id: int, *, since: Optional[str] = None,
                      limit: Optional[int] = None) -> list[dict]:
    with connect() as conn:
        cur = conn.cursor()
        if since and limit:
            rows = _fetchall(cur,
                "SELECT recorded_at, price_usd, source, source_url FROM price_history "
                "WHERE card_id = %s AND recorded_at >= %s ORDER BY recorded_at DESC LIMIT %s",
                (card_id, since, limit),
            )
            rows = list(reversed(rows))
        elif since:
            rows = _fetchall(cur,
                "SELECT recorded_at, price_usd, source, source_url FROM price_history "
                "WHERE card_id = %s AND recorded_at >= %s ORDER BY recorded_at ASC",
                (card_id, since),
            )
        elif limit:
            rows = _fetchall(cur,
                "SELECT recorded_at, price_usd, source, source_url FROM price_history "
                "WHERE card_id = %s ORDER BY recorded_at DESC LIMIT %s",
                (card_id, limit),
            )
            rows = list(reversed(rows))
        else:
            rows = _fetchall(cur,
                "SELECT recorded_at, price_usd, source, source_url FROM price_history "
                "WHERE card_id = %s ORDER BY recorded_at ASC",
                (card_id,),
            )
        return [{"at": r["recorded_at"].isoformat() if hasattr(r["recorded_at"], "isoformat") else r["recorded_at"],
                 "price": float(r["price_usd"]),
                 "source": r["source"],
                 "source_url": r["source_url"]}
                for r in rows]


def backfill_price_history() -> int:
    inserted = 0
    with connect() as conn:
        cur = conn.cursor()
        rows = _fetchall(cur,
            "SELECT c.id, c.current_market_price, "
            "       COALESCE(c.last_priced_at, c.created_at) AS at "
            "FROM cards c "
            "LEFT JOIN price_history ph ON ph.card_id = c.id "
            "WHERE c.current_market_price IS NOT NULL "
            "GROUP BY c.id, c.current_market_price, c.last_priced_at, c.created_at "
            "HAVING COUNT(ph.id) = 0",
        )
        for r in rows:
            cur.execute(
                "INSERT INTO price_history (card_id, recorded_at, price_usd, source) "
                "VALUES (%s, %s, %s, %s)",
                (r["id"], r["at"], float(r["current_market_price"]), "backfill"),
            )
            inserted += 1
    return inserted


# ---------------------------------------------------------------------------
# Tags
# ---------------------------------------------------------------------------

def list_tags(user_id: int) -> list[Tag]:
    with connect() as conn:
        cur = conn.cursor()
        rows = _fetchall(cur,
            "SELECT t.id, t.user_id, t.name, t.color, t.is_trade_tag, "
            "       COUNT(ct.card_id) AS card_count "
            "FROM tags t LEFT JOIN card_tags ct ON ct.tag_id = t.id "
            "WHERE t.user_id = %s "
            "GROUP BY t.id "
            "ORDER BY t.is_trade_tag DESC, t.name ASC",
            (user_id,),
        )
        return [Tag(
            id=r["id"], user_id=r["user_id"], name=r["name"],
            color=r["color"], is_trade_tag=bool(r["is_trade_tag"]),
            card_count=r["card_count"],
        ) for r in rows]


def get_tag(tag_id: int) -> Optional[Tag]:
    with connect() as conn:
        cur = conn.cursor()
        r = _fetchone(cur,
            "SELECT id, user_id, name, color, is_trade_tag FROM tags WHERE id = %s",
            (tag_id,),
        )
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
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO tags (user_id, name, color, is_trade_tag) "
            "VALUES (%s, %s, %s, %s) RETURNING id",
            (user_id, name, color, is_trade_tag),
        )
        tag_id = cur.fetchone()["id"]
    return get_tag(tag_id)


def delete_tag(tag_id: int) -> bool:
    with connect() as conn:
        cur = conn.cursor()
        cur.execute("DELETE FROM tags WHERE id = %s", (tag_id,))
        return cur.rowcount > 0


def update_tag(tag_id: int, **changes) -> Optional[Tag]:
    if not changes:
        return get_tag(tag_id)
    unknown = set(changes) - _TAG_COLUMNS
    if unknown:
        raise ValueError(f"unknown tag columns: {unknown}")
    cols = ", ".join(f"{k} = %s" for k in changes)
    with connect() as conn:
        cur = conn.cursor()
        cur.execute(f"UPDATE tags SET {cols} WHERE id = %s",
                    (*changes.values(), tag_id))
    return get_tag(tag_id)


def add_tag_to_card(card_id: int, tag_id: int) -> bool:
    with connect() as conn:
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO card_tags (card_id, tag_id) VALUES (%s, %s) ON CONFLICT DO NOTHING",
            (card_id, tag_id),
        )
        return cur.rowcount > 0


def remove_tag_from_card(card_id: int, tag_id: int) -> bool:
    with connect() as conn:
        cur = conn.cursor()
        cur.execute(
            "DELETE FROM card_tags WHERE card_id = %s AND tag_id = %s",
            (card_id, tag_id),
        )
        return cur.rowcount > 0


# ---------------------------------------------------------------------------
# Portfolio
# ---------------------------------------------------------------------------

def portfolio_summary(user_id: int) -> PortfolioSummary:
    user = get_user(user_id)
    if not user:
        raise ValueError(f"unknown user_id {user_id}")
    cards = list_cards(user_id)
    card_items   = [c for c in cards if c.product_type == "card"]
    sealed_items = [c for c in cards if c.product_type != "card"]
    total_purchase    = sum(c.purchase_price or 0.0 for c in cards)
    total_market      = sum(c.current_market_price or 0.0 for c in cards)
    total_cards_value  = sum(c.current_market_price or 0.0 for c in card_items)
    total_sealed_value = sum(c.current_market_price or 0.0 for c in sealed_items)
    bulk      = sum(1 for c in card_items if (c.current_market_price or 0) < 5)
    untracked = sum(1 for c in cards if c.current_market_price is None)
    gain = total_market - total_purchase
    pct  = (gain / total_purchase * 100) if total_purchase > 0 else 0.0
    return PortfolioSummary(
        user_id=user_id, user_name=user.name,
        card_count=len(card_items),
        sealed_count=len(sealed_items),
        total_purchase_price=round(total_purchase, 2),
        total_market_value=round(total_market, 2),
        total_cards_value=round(total_cards_value, 2),
        total_sealed_value=round(total_sealed_value, 2),
        unrealized_gain_loss=round(gain, 2),
        gain_loss_pct=round(pct, 2),
        bulk_count=bulk,
        untracked_count=untracked,
    )


# ---------------------------------------------------------------------------
# Accounts & scan usage
# ---------------------------------------------------------------------------

def get_account(uid: str) -> dict | None:
    with connect() as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT id, email, plan, trial_ends_at FROM accounts WHERE id = %s", (uid,)
        )
        row = cur.fetchone()
        if not row:
            return None
        return {"id": str(row["id"]), "email": row["email"], "plan": row["plan"], "trial_ends_at": row["trial_ends_at"]}


def create_account(uid: str, email: str) -> dict:
    from datetime import datetime, timezone, timedelta
    trial_ends = datetime.now(timezone.utc) + timedelta(days=14)
    with connect() as conn:
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO accounts (id, email, plan, trial_ends_at) VALUES (%s, %s, 'free', %s) "
            "ON CONFLICT (id) DO UPDATE SET email = EXCLUDED.email "
            "RETURNING id, email, plan, trial_ends_at",
            (uid, email, trial_ends),
        )
        row = cur.fetchone()
        return {"id": str(row["id"]), "email": row["email"], "plan": row["plan"], "trial_ends_at": row["trial_ends_at"]}


def _row_to_profile(row) -> dict:
    return {
        "id": row["id"],
        "name": row["name"],
        "account_id": str(row["account_id"]) if row["account_id"] else None,
        "avatar_color": row.get("avatar_color", "#34d399"),
    }


def list_profiles(account_id: str) -> list[dict]:
    with connect() as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT id, name, account_id, avatar_color FROM users WHERE account_id = %s ORDER BY id",
            (account_id,),
        )
        return [_row_to_profile(r) for r in cur.fetchall()]


def get_profile(account_id: str, profile_id: int) -> dict | None:
    with connect() as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT id, name, account_id, avatar_color FROM users WHERE id = %s AND account_id = %s",
            (profile_id, account_id),
        )
        row = cur.fetchone()
        return _row_to_profile(row) if row else None


def create_profile(account_id: str, name: str, avatar_color: str = "#34d399") -> dict:
    with connect() as conn:
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO users (account_id, name, avatar_color) VALUES (%s, %s, %s) "
            "RETURNING id, name, account_id, avatar_color",
            (account_id, name, avatar_color),
        )
        return _row_to_profile(cur.fetchone())


def count_profiles(account_id: str) -> int:
    with connect() as conn:
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM users WHERE account_id = %s", (account_id,))
        return cur.fetchone()["count"]


def get_scan_count(account_id: str, month: str) -> int:
    with connect() as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT scan_count FROM scan_usage WHERE account_id = %s AND month = %s",
            (account_id, month),
        )
        row = cur.fetchone()
        return row[0] if row else 0


def increment_scan_count(account_id: str, month: str) -> int:
    with connect() as conn:
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO scan_usage (account_id, month, scan_count) VALUES (%s, %s, 1) "
            "ON CONFLICT (account_id, month) DO UPDATE SET scan_count = scan_usage.scan_count + 1 "
            "RETURNING scan_count",
            (account_id, month),
        )
        return cur.fetchone()["scan_count"]
