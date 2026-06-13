"""Tests for price_history_refresh: dedup-by-date, cutoff filtering, and the
summary dict returned by refresh_all()."""
from __future__ import annotations

import asyncio
import os
import sys
import time

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

import db as _db
import price_history_refresh as phr


@pytest.fixture
def test_db(tmp_path, monkeypatch):
    db_path = tmp_path / "test.sqlite"
    monkeypatch.setattr(_db, 'DB_PATH', db_path)
    _db.init_db()
    return db_path


def _ms(days_ago: float) -> float:
    return (time.time() - days_ago * 86400) * 1000


def _make_card(**overrides):
    users = _db.list_users()
    fields = dict(
        id=None, user_id=users[0].id, name="Pikachu", set_name="Base Set",
        card_number="58/102", language="english",
    )
    fields.update(overrides)
    return _db.create_card(_db.Card(**fields))


def test_refresh_one_dedups_by_date_and_applies_cutoff(test_db):
    card = _make_card()

    existing_date = time.strftime("%Y-%m-%d", time.gmtime(_ms(30) / 1000))
    with _db.connect() as conn:
        conn.execute(
            "INSERT INTO price_history (card_id, recorded_at, price_usd, source) "
            "VALUES (?, ?, ?, ?)",
            (card.id, f"{existing_date} 00:00:00", 99.0, "manual"),
        )
        conn.commit()

    points = [
        (_ms(30), 100.0),   # same date as existing row -> skipped
        (_ms(15), 120.0),   # new -> inserted
        (_ms(200), 80.0),   # older than cutoff -> skipped
    ]

    async def fake_fetch():
        return points, "https://www.pricecharting.com/game/pokemon-base-set/pikachu-58"

    cutoff_ms = time.time() * 1000 - 90 * 86400_000
    inserted = asyncio.run(phr.refresh_one(card.id, card.name, cutoff_ms, fake_fetch))
    assert inserted == 1

    with _db.connect() as conn:
        rows = conn.execute(
            "SELECT price_usd FROM price_history WHERE card_id = ? ORDER BY recorded_at",
            (card.id,),
        ).fetchall()
    prices = sorted(r["price_usd"] for r in rows)
    assert prices == [99.0, 120.0]


def test_refresh_one_no_chart_data_inserts_nothing(test_db):
    card = _make_card(name="Mewtwo")

    async def fake_fetch():
        return None

    cutoff_ms = time.time() * 1000 - 90 * 86400_000
    assert asyncio.run(phr.refresh_one(card.id, card.name, cutoff_ms, fake_fetch)) == 0

    with _db.connect() as conn:
        rows = conn.execute(
            "SELECT * FROM price_history WHERE card_id = ?", (card.id,),
        ).fetchall()
    assert rows == []


def test_refresh_all_covers_raw_cards_and_sealed_products_and_skips_graded(test_db, monkeypatch):
    monkeypatch.setattr(phr, "REQUEST_DELAY_SECONDS", 0)

    raw_card = _make_card(name="Pikachu", product_type="card")
    sealed = _make_card(name="SV 151 Booster Box", set_name="SV 151",
                         card_number=None, product_type="booster_box")
    _make_card(name="Charizard", card_number="4/102",
               is_graded=True, grade_company="PSA", grade=10)

    async def fake_chart_history(name, set_name, card_number, language="english", variant=None):
        return [(time.time() * 1000, 50.0)], "https://pricecharting.com/raw"

    async def fake_sealed_chart_history(name, set_name, product_type, language="english"):
        return [(time.time() * 1000, 110.0)], "https://pricecharting.com/sealed"

    monkeypatch.setattr(phr.pc, "fetch_chart_history", fake_chart_history)
    monkeypatch.setattr(phr.pc, "fetch_sealed_chart_history", fake_sealed_chart_history)

    summary = asyncio.run(phr.refresh_all(min_days=90))

    assert summary == {"raw_cards": 1, "sealed_products": 1, "inserted": 2}

    with _db.connect() as conn:
        raw_price = conn.execute(
            "SELECT price_usd FROM price_history WHERE card_id = ?", (raw_card.id,),
        ).fetchone()
        sealed_price = conn.execute(
            "SELECT price_usd FROM price_history WHERE card_id = ?", (sealed.id,),
        ).fetchone()
    assert raw_price["price_usd"] == 50.0
    assert sealed_price["price_usd"] == 110.0
