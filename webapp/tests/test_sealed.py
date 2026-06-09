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


# ---- pricing_engine tests ----
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))
from pricing_engine import SealedProductQuery, build_ebay_sealed_url, is_relevant_sealed_title

def test_build_ebay_sealed_url_booster_box():
    q = SealedProductQuery(name='Scarlet & Violet 151', set_name='Scarlet & Violet 151', product_type='booster_box')
    url = build_ebay_sealed_url(q)
    assert 'LH_Sold=1' in url
    assert 'Booster+Box' in url or 'Booster Box' in url.replace('%20', ' ').replace('+', ' ')
    assert 'sealed' in url.lower()
    assert '-pack' in url.lower() or '-pack' in url

def test_build_ebay_sealed_url_booster_pack_no_pack_exclusion():
    q = SealedProductQuery(name='Scarlet & Violet 151', set_name='Scarlet & Violet 151', product_type='booster_pack')
    url = build_ebay_sealed_url(q)
    # Should NOT exclude -pack when searching for packs
    decoded = url.replace('%20', ' ').replace('+', ' ')
    assert '-pack' not in decoded

def test_build_ebay_sealed_url_etb():
    q = SealedProductQuery(name='Prismatic Evolutions', set_name='Prismatic Evolutions', product_type='etb')
    url = build_ebay_sealed_url(q)
    decoded = url.replace('%20', ' ').replace('+', ' ')
    assert 'Elite Trainer Box' in decoded

def test_is_relevant_sealed_title_filters_opened():
    q = SealedProductQuery(name='Scarlet & Violet 151', set_name='Scarlet & Violet 151', product_type='booster_box')
    assert not is_relevant_sealed_title('Pokemon 151 Booster Box OPENED empty', q)

def test_is_relevant_sealed_title_filters_lot():
    q = SealedProductQuery(name='Scarlet & Violet 151', set_name='Scarlet & Violet 151', product_type='booster_box')
    assert not is_relevant_sealed_title('Pokemon 151 Booster Box x3 lot', q)

def test_is_relevant_sealed_title_filters_bare_lot():
    q = SealedProductQuery(name='Scarlet & Violet 151', set_name='Scarlet & Violet 151', product_type='booster_box')
    assert not is_relevant_sealed_title('Pokemon Scarlet & Violet 151 Booster Box sealed lot', q)

def test_is_relevant_sealed_title_accepts_good_listing():
    q = SealedProductQuery(name='Scarlet & Violet 151', set_name='Scarlet & Violet 151', product_type='booster_box')
    assert is_relevant_sealed_title('Pokemon Scarlet & Violet 151 Booster Box Factory Sealed', q)


# ---- ebay_lookup sealed test (import-level smoke test) ----
import inspect
import ebay_lookup

def test_lookup_sealed_recent_n_mean_exists():
    assert hasattr(ebay_lookup, 'lookup_sealed_recent_n_mean')
    sig = inspect.signature(ebay_lookup.lookup_sealed_recent_n_mean)
    params = list(sig.parameters)
    assert 'name' in params
    assert 'product_type' in params
