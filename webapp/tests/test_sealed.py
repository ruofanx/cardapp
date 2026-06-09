"""Tests for sealed product schema and portfolio split."""
import os, sys, sqlite3, tempfile, pytest
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

import db as _db

@pytest.fixture
def test_db(tmp_path, monkeypatch):
    db_path = tmp_path / "test.sqlite"
    monkeypatch.setattr(_db, 'DB_PATH', db_path)
    _db.init_db()
    return db_path

def test_card_has_product_type_column(test_db):
    with _db.connect() as conn:
        cols = [r[1] for r in conn.execute("PRAGMA table_info(cards)").fetchall()]
    assert 'product_type' in cols

def test_card_defaults_product_type_to_card(test_db):
    users = _db.list_users()
    card = _db.create_card(_db.Card(
        id=None, user_id=users[0].id, name='Charizard', set_name='Base Set',
        card_number='4/102', language='english',
    ))
    assert card.product_type == 'card'

def test_create_sealed_product(test_db):
    users = _db.list_users()
    box = _db.create_card(_db.Card(
        id=None, user_id=users[0].id, name='Scarlet & Violet 151',
        set_name='Scarlet & Violet 151', language='english',
        product_type='booster_box',
    ))
    assert box.product_type == 'booster_box'
    fetched = _db.get_card(box.id)
    assert fetched.product_type == 'booster_box'

def test_portfolio_summary_splits_cards_and_sealed(test_db):
    users = _db.list_users()
    uid = users[0].id
    _db.create_card(_db.Card(
        id=None, user_id=uid, name='Pikachu', set_name='Base',
        card_number='58/102', language='english', product_type='card',
        current_market_price=25.0,
    ))
    _db.create_card(_db.Card(
        id=None, user_id=uid, name='Scarlet & Violet 151', set_name='SV 151',
        card_number=None, language='english', product_type='booster_box',
        current_market_price=120.0,
    ))
    summary = _db.portfolio_summary(uid)
    assert summary.total_cards_value == 25.0
    assert summary.total_sealed_value == 120.0
    assert summary.total_market_value == 145.0
    assert summary.card_count == 1
    assert summary.sealed_count == 1
