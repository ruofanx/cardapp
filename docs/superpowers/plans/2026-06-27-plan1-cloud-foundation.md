# PokeCollect — Plan 1: Cloud Foundation

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the local SQLite/LAN setup with a cloud-hosted PostgreSQL database on Supabase, deploy FastAPI to Railway, and migrate the existing family data — producing a working app accessible at a public URL with all existing features intact.

**Architecture:** FastAPI on Railway serves the existing React/Babel frontend unchanged. `db.py` (SQLite) is replaced by `db_postgres.py` (psycopg2 → Supabase PostgreSQL). Card photos move to Supabase Storage. eBay/PriceCharting caches remain as local SQLite on the Railway instance. No auth changes in this plan — the existing `user_id`-based API is preserved; auth + profiles come in Plan 2.

**Tech Stack:** FastAPI, Supabase (PostgreSQL + Storage), psycopg2-binary, python-dotenv, Railway

---

## File Map

| Action | Path | Purpose |
|---|---|---|
| Create | `webapp/schema.sql` | Full PostgreSQL schema (mirrors SQLite schema) |
| Create | `webapp/db_postgres.py` | Drop-in replacement for `db.py` using psycopg2 |
| Create | `webapp/supabase_storage.py` | Card photo upload/delete via Supabase Storage API |
| Create | `webapp/migrate_sqlite_to_postgres.py` | One-time migration script |
| Create | `webapp/tests/test_db_postgres.py` | Integration tests for PostgreSQL data layer |
| Modify | `webapp/app.py` | Switch `import db` → `import db_postgres as db`; add photo storage |
| Modify | `webapp/requirements.txt` | Add psycopg2-binary, python-dotenv, requests |
| Create | `.env.example` | Document all required environment variables |
| Create | `Procfile` | Railway process definition |
| Create | `railway.json` | Railway build config |

---

## Task 1: Supabase Project + Environment Setup

**Files:** Create `.env.example`, modify `webapp/requirements.txt`

- [ ] **Step 1: Create a Supabase project**

  Go to [supabase.com](https://supabase.com) → New project. Name it `pokecollect`. Choose a region close to your users (e.g. US East). Save the database password — you'll need it once.

- [ ] **Step 2: Collect credentials from Supabase dashboard**

  In your project: Settings → API. Copy:
  - **Project URL** (looks like `https://abcxyz.supabase.co`)
  - **anon/public key** (starts with `eyJ...`)
  - **service_role key** (starts with `eyJ...` — keep secret)

  Settings → Database → Connection string → URI. It looks like:
  `postgresql://postgres:[YOUR-PASSWORD]@db.abcxyz.supabase.co:5432/postgres`

- [ ] **Step 3: Create `.env.example`**

  Create at repo root `~/claude/CardApp/.env.example`:

  ```
  # Supabase
  SUPABASE_URL=https://your-project.supabase.co
  SUPABASE_ANON_KEY=eyJ...
  SUPABASE_SERVICE_KEY=eyJ...
  DATABASE_URL=postgresql://postgres:[password]@db.your-project.supabase.co:5432/postgres

  # Supabase Storage bucket name (create in Supabase dashboard → Storage)
  SUPABASE_STORAGE_BUCKET=card-photos

  # Existing keys (unchanged)
  ANTHROPIC_API_KEY=sk-ant-...
  GOOGLE_API_KEY=AIza...
  SCRAPERAPI_KEY=
  ```

  Copy to `.env` and fill in your values:
  ```bash
  cp .env.example .env
  ```

- [ ] **Step 4: Add to `.gitignore`**

  ```bash
  echo ".env" >> ~/claude/CardApp/.gitignore
  ```

- [ ] **Step 5: Add dependencies**

  Edit `webapp/requirements.txt` — append these lines:

  ```
  psycopg2-binary>=2.9
  python-dotenv>=1.0
  requests>=2.31
  ```

- [ ] **Step 6: Install and verify**

  ```bash
  cd ~/claude/CardApp/webapp
  pip install -r requirements.txt
  python -c "import psycopg2; print('psycopg2 ok')"
  ```

  Expected: `psycopg2 ok`

- [ ] **Step 7: Commit**

  ```bash
  cd ~/claude/CardApp
  git add .env.example .gitignore webapp/requirements.txt
  git commit -m "chore: add Supabase env template and psycopg2 dependency"
  ```

---

## Task 2: PostgreSQL Schema

**Files:** Create `webapp/schema.sql`

- [ ] **Step 1: Write the schema**

  Create `webapp/schema.sql`:

  ```sql
  -- Users (same concept as SQLite users — will be renamed to profiles in Plan 2)
  CREATE TABLE IF NOT EXISTS users (
      id           SERIAL PRIMARY KEY,
      name         TEXT NOT NULL UNIQUE,
      avatar_color TEXT NOT NULL DEFAULT '#3b82f6',
      created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
  );

  CREATE TABLE IF NOT EXISTS cards (
      id                   SERIAL PRIMARY KEY,
      user_id              INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
      name                 TEXT NOT NULL,
      set_name             TEXT,
      card_number          TEXT,
      language             TEXT NOT NULL DEFAULT 'english',
      variant              TEXT,
      condition            TEXT NOT NULL DEFAULT 'NM',
      is_graded            BOOLEAN NOT NULL DEFAULT FALSE,
      grade_company        TEXT,
      grade                REAL,
      purchase_price       REAL,
      purchase_date        TEXT,
      current_market_price REAL,
      last_priced_at       TIMESTAMPTZ,
      image_url            TEXT,
      photo_path           TEXT,
      notes                TEXT,
      product_type         TEXT NOT NULL DEFAULT 'card',
      created_at           TIMESTAMPTZ NOT NULL DEFAULT NOW()
  );

  CREATE INDEX IF NOT EXISTS idx_cards_user ON cards(user_id);

  CREATE TABLE IF NOT EXISTS tags (
      id           SERIAL PRIMARY KEY,
      user_id      INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
      name         TEXT NOT NULL,
      color        TEXT NOT NULL DEFAULT '#94a3b8',
      is_trade_tag BOOLEAN NOT NULL DEFAULT FALSE,
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

  CREATE TABLE IF NOT EXISTS price_history (
      id          SERIAL PRIMARY KEY,
      card_id     INTEGER NOT NULL REFERENCES cards(id) ON DELETE CASCADE,
      recorded_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
      price_usd   REAL NOT NULL,
      source      TEXT,
      source_url  TEXT
  );

  CREATE INDEX IF NOT EXISTS idx_price_history_card_time
      ON price_history(card_id, recorded_at);
  ```

- [ ] **Step 2: Apply the schema to Supabase**

  In Supabase dashboard → SQL Editor → New query. Paste `schema.sql` contents and click Run.

  Expected: "Success. No rows returned" for each statement.

- [ ] **Step 3: Verify in Supabase Table Editor**

  Dashboard → Table Editor. You should see: `users`, `cards`, `tags`, `card_tags`, `price_history`.

- [ ] **Step 4: Commit**

  ```bash
  cd ~/claude/CardApp
  git add webapp/schema.sql
  git commit -m "feat: add PostgreSQL schema for cloud deployment"
  ```

---

## Task 3: PostgreSQL Data Layer

**Files:** Create `webapp/db_postgres.py`, create `webapp/tests/test_db_postgres.py`

This module has the same public interface as `db.py` so `app.py` can switch with a one-line import change.

- [ ] **Step 1: Write the failing tests**

  Create `webapp/tests/test_db_postgres.py`:

  ```python
  """Integration tests for db_postgres — requires TEST_DATABASE_URL env var."""
  import os
  import pytest
  from dotenv import load_dotenv

  load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), "../../.env"))

  if not os.environ.get("TEST_DATABASE_URL") and not os.environ.get("DATABASE_URL"):
      pytest.skip("No DATABASE_URL set — skipping PostgreSQL integration tests",
                  allow_module_level=True)

  import sys
  sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
  import db_postgres as db

  @pytest.fixture(autouse=True)
  def clean_test_user(request):
      """Create a test user, yield, then delete it (cascades to cards/tags)."""
      user = db.create_user("_test_user_", "#ff0000")
      request.addfinalizer(lambda: db.delete_user(user.id))
      return user

  def test_create_and_get_user(clean_test_user):
      u = db.get_user(clean_test_user.id)
      assert u is not None
      assert u.name == "_test_user_"
      assert u.avatar_color == "#ff0000"

  def test_list_users_includes_test_user(clean_test_user):
      users = db.list_users()
      names = [u.name for u in users]
      assert "_test_user_" in names

  def test_create_and_get_card(clean_test_user):
      from db_postgres import Card
      card = db.create_card(Card(
          id=None, user_id=clean_test_user.id,
          name="Charizard", set_name="Base Set",
          card_number="4/102", language="english",
      ))
      assert card.id is not None
      assert card.name == "Charizard"
      fetched = db.get_card(card.id)
      assert fetched is not None
      assert fetched.name == "Charizard"

  def test_update_card(clean_test_user):
      from db_postgres import Card
      card = db.create_card(Card(
          id=None, user_id=clean_test_user.id, name="Pikachu", language="english"
      ))
      updated = db.update_card(card.id, current_market_price=25.50)
      assert updated.current_market_price == 25.50

  def test_delete_card(clean_test_user):
      from db_postgres import Card
      card = db.create_card(Card(
          id=None, user_id=clean_test_user.id, name="Mewtwo", language="english"
      ))
      assert db.delete_card(card.id) is True
      assert db.get_card(card.id) is None

  def test_price_history(clean_test_user):
      from db_postgres import Card
      card = db.create_card(Card(
          id=None, user_id=clean_test_user.id, name="Blastoise", language="english"
      ))
      db.log_price(card.id, 100.0, source="test")
      db.log_price(card.id, 110.0, source="test")
      history = db.get_price_history(card.id)
      assert len(history) == 2
      assert history[0]["price"] == 100.0
      assert history[1]["price"] == 110.0

  def test_log_price_dedup(clean_test_user):
      from db_postgres import Card
      card = db.create_card(Card(
          id=None, user_id=clean_test_user.id, name="Venusaur", language="english"
      ))
      db.log_price(card.id, 50.0, source="test")
      db.log_price(card.id, 50.004, source="test")  # within $0.005 tolerance
      history = db.get_price_history(card.id)
      assert len(history) == 1  # deduped

  def test_tags(clean_test_user):
      tag = db.create_tag(clean_test_user.id, "for trade", "#f97316", is_trade_tag=True)
      assert tag.id is not None
      from db_postgres import Card
      card = db.create_card(Card(
          id=None, user_id=clean_test_user.id, name="Raichu", language="english"
      ))
      db.add_tag_to_card(card.id, tag.id)
      fetched = db.get_card(card.id)
      assert any(t.id == tag.id for t in fetched.tags)

  def test_portfolio_summary(clean_test_user):
      from db_postgres import Card
      db.create_card(Card(
          id=None, user_id=clean_test_user.id, name="Snorlax",
          language="english", purchase_price=10.0, current_market_price=15.0
      ))
      summary = db.portfolio_summary(clean_test_user.id)
      assert summary.card_count == 1
      assert summary.total_market_value == 15.0
  ```

- [ ] **Step 2: Run tests to confirm they fail**

  ```bash
  cd ~/claude/CardApp/webapp
  pytest tests/test_db_postgres.py -v 2>&1 | head -20
  ```

  Expected: `ModuleNotFoundError: No module named 'db_postgres'`

- [ ] **Step 3: Write `db_postgres.py`**

  Create `webapp/db_postgres.py`:

  ```python
  """
  PostgreSQL data layer for PokeCollect — drop-in replacement for db.py.

  Uses psycopg2 with a connection pool. Requires DATABASE_URL env var.
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

  load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), "../../.env"))

  _DATABASE_URL = os.environ.get("DATABASE_URL") or os.environ.get("TEST_DATABASE_URL")


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
          for u in _fetchall(cur, "SELECT id FROM users"):
              for tname, tcolor, is_trade in SEED_TAGS:
                  cur.execute(
                      "INSERT INTO tags (user_id, name, color, is_trade_tag) "
                      "VALUES (%s, %s, %s, %s) ON CONFLICT (user_id, name) DO NOTHING",
                      (u["id"], tname, tcolor, is_trade),
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
          if since:
              rows = _fetchall(cur,
                  "SELECT recorded_at, price_usd, source, source_url FROM price_history "
                  "WHERE card_id = %s AND recorded_at >= %s ORDER BY recorded_at ASC",
                  (card_id, since),
              )
          else:
              rows = _fetchall(cur,
                  "SELECT recorded_at, price_usd, source, source_url FROM price_history "
                  "WHERE card_id = %s ORDER BY recorded_at ASC",
                  (card_id,),
              )
          out = [{"at": r["recorded_at"].isoformat() if hasattr(r["recorded_at"], "isoformat") else r["recorded_at"],
                  "price": float(r["price_usd"]),
                  "source": r["source"],
                  "source_url": r["source_url"]}
                 for r in rows]
          if limit and len(out) > limit:
              out = out[-limit:]
          return out


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
  ```

- [ ] **Step 4: Run the tests**

  ```bash
  cd ~/claude/CardApp/webapp
  pytest tests/test_db_postgres.py -v
  ```

  Expected: all 9 tests pass. If `DATABASE_URL` is not set, tests are skipped (not failed).

- [ ] **Step 5: Commit**

  ```bash
  cd ~/claude/CardApp
  git add webapp/db_postgres.py webapp/tests/test_db_postgres.py
  git commit -m "feat: add PostgreSQL data layer (db_postgres.py) with integration tests"
  ```

---

## Task 4: Supabase Storage for Card Photos

**Files:** Create `webapp/supabase_storage.py`

The current app saves card photos to `webapp/uploads/<filename>`. In the cloud, Railway's filesystem is ephemeral — files disappear on redeploy. Supabase Storage provides durable object storage.

- [ ] **Step 1: Create the Storage bucket in Supabase**

  Dashboard → Storage → New bucket. Name: `card-photos`. Set to **private** (the backend fetches signed URLs). Click Create.

- [ ] **Step 2: Write `supabase_storage.py`**

  Create `webapp/supabase_storage.py`:

  ```python
  """
  Card photo storage via Supabase Storage REST API.

  Replaces local uploads/ directory. Photos are stored in the
  SUPABASE_STORAGE_BUCKET bucket and accessed via signed URLs.
  """
  import os
  import requests
  from dotenv import load_dotenv

  load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), "../../.env"))

  _SUPABASE_URL = os.environ.get("SUPABASE_URL", "")
  _SERVICE_KEY  = os.environ.get("SUPABASE_SERVICE_KEY", "")
  _BUCKET       = os.environ.get("SUPABASE_STORAGE_BUCKET", "card-photos")


  def _headers():
      return {
          "Authorization": f"Bearer {_SERVICE_KEY}",
          "apikey": _SERVICE_KEY,
      }


  def upload_photo(filename: str, data: bytes, content_type: str = "image/jpeg") -> str:
      """Upload photo bytes to Supabase Storage. Returns the storage path."""
      url = f"{_SUPABASE_URL}/storage/v1/object/{_BUCKET}/{filename}"
      resp = requests.post(
          url,
          headers={**_headers(), "Content-Type": content_type},
          data=data,
      )
      resp.raise_for_status()
      return filename  # storage path, stored in cards.photo_path


  def get_signed_url(storage_path: str, expires_in: int = 3600) -> str:
      """Return a time-limited signed URL for a stored photo."""
      url = f"{_SUPABASE_URL}/storage/v1/object/sign/{_BUCKET}/{storage_path}"
      resp = requests.post(
          url,
          headers={**_headers(), "Content-Type": "application/json"},
          json={"expiresIn": expires_in},
      )
      resp.raise_for_status()
      signed = resp.json().get("signedURL", "")
      return f"{_SUPABASE_URL}/storage/v1{signed}" if signed.startswith("/") else signed


  def delete_photo(storage_path: str) -> bool:
      """Delete a photo from storage. Returns True if deleted."""
      url = f"{_SUPABASE_URL}/storage/v1/object/{_BUCKET}/{storage_path}"
      resp = requests.delete(url, headers=_headers())
      return resp.status_code in (200, 204)


  def is_configured() -> bool:
      """True if Supabase Storage env vars are set."""
      return bool(_SUPABASE_URL and _SERVICE_KEY)
  ```

- [ ] **Step 3: Commit**

  ```bash
  cd ~/claude/CardApp
  git add webapp/supabase_storage.py
  git commit -m "feat: add Supabase Storage module for cloud card photos"
  ```

---

## Task 5: Switch app.py to PostgreSQL

**Files:** Modify `webapp/app.py`

- [ ] **Step 1: Update the db import**

  In `webapp/app.py`, find line 38:
  ```python
  import db
  ```
  Replace with:
  ```python
  try:
      import db_postgres as db
  except Exception:
      import db  # local SQLite fallback during development
  ```

- [ ] **Step 2: Update startup handler to load .env**

  At the top of `webapp/app.py`, after the existing imports, add:
  ```python
  from dotenv import load_dotenv
  load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), "../../.env"))
  ```

- [ ] **Step 3: Update photo upload route to use Supabase Storage**

  Find the route `POST /api/cards/{card_id}/photo`. Add cloud storage alongside the existing local save. After the existing file-write block, add:

  ```python
  # Also upload to Supabase Storage if configured
  from supabase_storage import is_configured, upload_photo, get_signed_url
  if is_configured():
      storage_path = f"cards/{card_id}/{filename}"
      upload_photo(storage_path, content, content_type=photo.content_type or "image/jpeg")
      db.update_card(card_id, photo_path=storage_path)
  ```

- [ ] **Step 4: Add signed URL resolution helper to app.py**

  Add this helper near the top of `webapp/app.py` (after imports):

  ```python
  from supabase_storage import is_configured as _storage_configured, get_signed_url as _sign_photo

  def _resolve_photo(d: dict) -> dict:
      """Rewrite Supabase storage paths to signed URLs in card dicts."""
      path = d.get("photo_path")
      if path and _storage_configured() and not path.startswith("/uploads") and not path.startswith("http"):
          try:
              d["photo_path"] = _sign_photo(path)
          except Exception:
              pass
      return d
  ```

  Then in `app.py`, find the helper function `_card_dict` (or wherever cards are serialized to dicts before being returned — search for `.dict()` or `asdict`). Wrap each card dict with `_resolve_photo(card_dict)` before returning. The two main call sites are the list-cards and get-card routes.

- [ ] **Step 5: Smoke test locally with PostgreSQL**

  ```bash
  cd ~/claude/CardApp/webapp
  DATABASE_URL="$(grep DATABASE_URL ../../.env | cut -d= -f2-)" uvicorn app:app --port 8001
  ```

  In another terminal:
  ```bash
  curl http://localhost:8001/api/users
  ```

  Expected: `[{"id":1,"name":"Ro",...},{"id":2,"name":"Reid",...},{"id":3,"name":"Ryan",...}]`

  (The Supabase DB will be empty initially — users are seeded on startup by `init_db()`.)

- [ ] **Step 6: Commit**

  ```bash
  cd ~/claude/CardApp
  git add webapp/app.py
  git commit -m "feat: switch app.py to PostgreSQL backend with Supabase Storage"
  ```

---

## Task 6: Data Migration Script

**Files:** Create `webapp/migrate_sqlite_to_postgres.py`

- [ ] **Step 1: Write the migration script**

  Create `webapp/migrate_sqlite_to_postgres.py`:

  ```python
  """
  One-time migration: SQLite (pokemon_trading.sqlite) → Supabase PostgreSQL.

  Run from webapp/ directory:
    python migrate_sqlite_to_postgres.py [--sqlite PATH] [--dry-run]

  The script:
    1. Reads all users, cards, tags, card_tags, price_history from SQLite
    2. Inserts into PostgreSQL in FK-safe order
    3. Remaps integer IDs (SERIAL in PostgreSQL starts fresh)
    4. Skips rows that already exist by name/unique constraint
  """
  import argparse
  import sqlite3
  import os
  import sys
  from pathlib import Path
  from dotenv import load_dotenv

  load_dotenv(dotenv_path=str(Path(__file__).parent.parent.parent / ".env"))

  sys.path.insert(0, str(Path(__file__).parent))
  import db_postgres as pg


  def migrate(sqlite_path: str, dry_run: bool = False):
      src = sqlite3.connect(sqlite_path)
      src.row_factory = sqlite3.Row

      # -- Users --
      users = src.execute("SELECT * FROM users ORDER BY id").fetchall()
      user_id_map = {}  # old SQLite id → new PG id
      print(f"Migrating {len(users)} users...")
      for u in users:
          if dry_run:
              print(f"  [dry] would insert user: {u['name']}")
              continue
          try:
              new_user = pg.create_user(u["name"], u["avatar_color"])
              user_id_map[u["id"]] = new_user.id
              print(f"  ✓ user {u['name']} → pg id {new_user.id}")
          except Exception as e:
              # User already exists — look up existing id
              existing = next((x for x in pg.list_users() if x.name == u["name"]), None)
              if existing:
                  user_id_map[u["id"]] = existing.id
                  print(f"  ~ user {u['name']} already exists, id {existing.id}")
              else:
                  print(f"  ✗ user {u['name']} failed: {e}")

      if dry_run:
          print("Dry run complete — no data written.")
          return

      # -- Tags (create any that don't exist yet) --
      tags = src.execute("SELECT * FROM tags ORDER BY id").fetchall()
      tag_id_map = {}
      print(f"\nMigrating {len(tags)} tags...")
      for t in tags:
          pg_user_id = user_id_map.get(t["user_id"])
          if not pg_user_id:
              print(f"  ~ skipping tag '{t['name']}' — user not migrated")
              continue
          existing_tags = pg.list_tags(pg_user_id)
          existing = next((x for x in existing_tags if x.name == t["name"]), None)
          if existing:
              tag_id_map[t["id"]] = existing.id
              print(f"  ~ tag '{t['name']}' already exists")
          else:
              new_tag = pg.create_tag(pg_user_id, t["name"], t["color"],
                                      is_trade_tag=bool(t["is_trade_tag"]))
              tag_id_map[t["id"]] = new_tag.id
              print(f"  ✓ tag '{t['name']}' → pg id {new_tag.id}")

      # -- Cards --
      cards = src.execute("SELECT * FROM cards ORDER BY id").fetchall()
      card_id_map = {}
      print(f"\nMigrating {len(cards)} cards...")
      for c in cards:
          pg_user_id = user_id_map.get(c["user_id"])
          if not pg_user_id:
              print(f"  ~ skipping card '{c['name']}' — user not migrated")
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
              product_type=c.get("product_type", "card"),
          ))
          card_id_map[c["id"]] = new_card.id
          print(f"  ✓ card '{c['name']}' → pg id {new_card.id}")

      # -- Card tags (join table) --
      ct_rows = src.execute("SELECT * FROM card_tags").fetchall()
      print(f"\nMigrating {len(ct_rows)} card-tag associations...")
      for ct in ct_rows:
          pg_card_id = card_id_map.get(ct["card_id"])
          pg_tag_id  = tag_id_map.get(ct["tag_id"])
          if pg_card_id and pg_tag_id:
              pg.add_tag_to_card(pg_card_id, pg_tag_id)

      # -- Price history --
      ph_rows = src.execute(
          "SELECT * FROM price_history ORDER BY recorded_at ASC"
      ).fetchall()
      print(f"\nMigrating {len(ph_rows)} price history rows...")
      for ph in ph_rows:
          pg_card_id = card_id_map.get(ph["card_id"])
          if pg_card_id:
              pg.log_price(pg_card_id, float(ph["price_usd"]),
                           source=ph["source"], at=ph["recorded_at"])

      print("\nMigration complete.")
      print(f"  Users:   {len(user_id_map)}")
      print(f"  Tags:    {len(tag_id_map)}")
      print(f"  Cards:   {len(card_id_map)}")
      print(f"  History: {len(ph_rows)}")


  if __name__ == "__main__":
      parser = argparse.ArgumentParser()
      parser.add_argument("--sqlite", default="pokemon_trading.sqlite",
                          help="Path to source SQLite file")
      parser.add_argument("--dry-run", action="store_true",
                          help="Print what would be migrated without writing")
      args = parser.parse_args()
      migrate(args.sqlite, dry_run=args.dry_run)
  ```

- [ ] **Step 2: Dry-run first**

  ```bash
  cd ~/claude/CardApp/webapp
  python migrate_sqlite_to_postgres.py --dry-run
  ```

  Expected output ends with `Dry run complete — no data written.`

- [ ] **Step 3: Run the real migration**

  ```bash
  python migrate_sqlite_to_postgres.py --sqlite pokemon_trading.sqlite
  ```

  Expected: all users, cards, tags, price history printed with `✓` checkmarks.

- [ ] **Step 4: Verify in Supabase Table Editor**

  Dashboard → Table Editor → `cards`. You should see all cards from the family's collection.

- [ ] **Step 5: Commit**

  ```bash
  cd ~/claude/CardApp
  git add webapp/migrate_sqlite_to_postgres.py
  git commit -m "feat: add SQLite → PostgreSQL migration script"
  ```

---

## Task 7: Railway Deployment

**Files:** Create `Procfile`, create `railway.json`

- [ ] **Step 1: Create `Procfile`**

  Create at `~/claude/CardApp/Procfile`:

  ```
  web: cd webapp && uvicorn app:app --host 0.0.0.0 --port $PORT
  ```

- [ ] **Step 2: Create `railway.json`**

  Create at `~/claude/CardApp/railway.json`:

  ```json
  {
    "$schema": "https://railway.app/railway.schema.json",
    "build": {
      "builder": "NIXPACKS"
    },
    "deploy": {
      "startCommand": "cd webapp && uvicorn app:app --host 0.0.0.0 --port $PORT",
      "restartPolicyType": "ON_FAILURE"
    }
  }
  ```

- [ ] **Step 3: Create `webapp/runtime.txt`** (tells Railway/Nixpacks the Python version)

  ```
  python-3.11
  ```

- [ ] **Step 4: Sign up for Railway and create project**

  Go to [railway.app](https://railway.app) → New Project → Deploy from GitHub repo. Select `CardApp`. Railway auto-detects Python via `Procfile`.

- [ ] **Step 5: Set environment variables in Railway**

  Railway dashboard → your project → Variables. Add each key from `.env.example`:

  ```
  SUPABASE_URL=https://your-project.supabase.co
  SUPABASE_ANON_KEY=eyJ...
  SUPABASE_SERVICE_KEY=eyJ...
  DATABASE_URL=postgresql://postgres:[password]@db.your-project.supabase.co:5432/postgres
  SUPABASE_STORAGE_BUCKET=card-photos
  ANTHROPIC_API_KEY=sk-ant-...
  GOOGLE_API_KEY=AIza...
  ```

- [ ] **Step 6: Deploy and verify**

  Railway auto-deploys on push. Wait for build to complete (~2 min). Open the Railway-provided URL (e.g. `https://pokecollect.up.railway.app`).

  ```bash
  curl https://pokecollect.up.railway.app/api/users
  ```

  Expected: JSON array with Ro, Reid, Ryan.

- [ ] **Step 7: Verify the full app in browser**

  Open `https://pokecollect.up.railway.app` in a mobile browser. Test:
  - [ ] Home screen loads with portfolio summary
  - [ ] Browse shows all cards
  - [ ] Card detail with price chart works
  - [ ] Scan screen can search cards

- [ ] **Step 8: Commit deployment files**

  ```bash
  cd ~/claude/CardApp
  git add Procfile railway.json webapp/runtime.txt
  git commit -m "feat: add Railway deployment config"
  ```

---

## Task 8: End-to-End Verification

- [ ] **Step 1: Run all existing tests against PostgreSQL**

  ```bash
  cd ~/claude/CardApp/webapp
  pytest tests/ -v --ignore=tests/test_db_postgres.py -x
  ```

  Expected: existing tests still pass (they use local SQLite, unchanged).

  ```bash
  pytest tests/test_db_postgres.py -v
  ```

  Expected: all 9 PostgreSQL tests pass.

- [ ] **Step 2: Confirm eBay and PriceCharting caches still work**

  ```bash
  curl -X POST https://pokecollect.up.railway.app/api/refresh-price \
    -H "Content-Type: application/json" \
    -d '{"name":"Charizard","set":"Base Set","language":"english","condition":"NM"}'
  ```

  Expected: `{"estimated_price": ..., "source": "..."}`

- [ ] **Step 3: Confirm card photo upload works**

  From the app: navigate to any card → Upload photo. Verify it appears after reload (confirming Supabase Storage round-trip).

- [ ] **Step 4: Final commit**

  ```bash
  cd ~/claude/CardApp
  git add -A
  git commit -m "chore: Plan 1 complete — PokeCollect running on Railway + Supabase PostgreSQL"
  ```
