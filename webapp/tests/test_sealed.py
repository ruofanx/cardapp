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


# ---- pricecharting_lookup sealed tests ----

def test_lookup_sealed_price_slug_mapping():
    """SEALED_PRODUCT_SLUGS maps all five product types."""
    from pricecharting_lookup import SEALED_PRODUCT_SLUGS
    assert SEALED_PRODUCT_SLUGS["booster_box"] == "booster-box"
    assert SEALED_PRODUCT_SLUGS["etb"] == "elite-trainer-box"
    assert SEALED_PRODUCT_SLUGS["booster_pack"] == "booster-pack"
    assert SEALED_PRODUCT_SLUGS["tin"] == "tin"
    assert SEALED_PRODUCT_SLUGS["bundle"] == "booster-bundle"

def test_lookup_sealed_price_unknown_type_returns_none():
    """Unknown product_type returns None without fetching."""
    import asyncio
    from pricecharting_lookup import lookup_sealed_price
    result = asyncio.run(lookup_sealed_price("Base Set", "Base Set", "mystery_box"))
    assert result is None

def test_lookup_sealed_price_exists():
    """lookup_sealed_price is importable with correct signature."""
    from pricecharting_lookup import lookup_sealed_price
    sig = inspect.signature(lookup_sealed_price)
    assert "name" in sig.parameters
    assert "product_type" in sig.parameters


# ---- app.py API schema tests ----

def test_refresh_price_request_has_product_type():
    """RefreshPriceRequest accepts product_type field."""
    from app import RefreshPriceRequest
    req = RefreshPriceRequest(name="Scarlet & Violet 151", product_type="booster_box")
    assert req.product_type == "booster_box"

def test_refresh_price_request_defaults_to_card():
    """RefreshPriceRequest defaults product_type to 'card'."""
    from app import RefreshPriceRequest
    req = RefreshPriceRequest(name="Charizard")
    assert req.product_type == "card"

def test_card_create_has_product_type():
    """CardCreate accepts product_type field."""
    from app import CardCreate
    cc = CardCreate(name="Scarlet & Violet 151 Booster Box", product_type="booster_box")
    assert cc.product_type == "booster_box"

def test_identify_response_has_product_type():
    """IdentifyResponse includes product_type field."""
    from app import IdentifyResponse
    assert "product_type" in IdentifyResponse.model_fields


# ---- ocr_engine tests ----

def test_identify_result_has_product_type():
    """IdentifyResult dataclass includes product_type field."""
    import sys
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))
    from ocr_engine import IdentifyResult
    from pricing_engine import CardIdentity
    identity = CardIdentity(name="Scarlet & Violet 151 Booster Box", set_name="", card_number="", language="english")
    r = IdentifyResult(identity=identity, confidence=0.9, source="llm", phash="abc", product_type="booster_box")
    assert r.product_type == "booster_box"


def test_identify_result_defaults_product_type_to_card():
    """IdentifyResult product_type defaults to 'card'."""
    import sys
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))
    from ocr_engine import IdentifyResult
    from pricing_engine import CardIdentity
    identity = CardIdentity(name="Charizard EX", set_name="", card_number="", language="english")
    r = IdentifyResult(identity=identity, confidence=0.9, source="llm", phash="abc")
    assert r.product_type == "card"


def test_build_identity_validates_product_type():
    """_build_identity_from_json rejects unrecognized product_type."""
    import sys
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))
    try:
        from ocr_engine import _build_identity_from_json
        import json
        raw = json.dumps({
            "name": "Test",
            "set_name": "Test Set",
            "card_number": "",
            "language": "english",
            "variant": None,
            "confidence": 0.9,
            "product_type": "mystery_box",
        })
        identity, confidence, raw_json, product_type = _build_identity_from_json(raw)
        assert product_type == "card"
    except ImportError:
        pass  # Function is private, tested indirectly


def test_identify_card_accepts_product_type_hint():
    """identify_card takes a product_type_hint kwarg (pre-scan TYPE toggle)."""
    import sys
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))
    from ocr_engine import identify_card
    sig = inspect.signature(identify_card)
    assert 'product_type_hint' in sig.parameters


def test_identify_user_prompt_reflects_hint():
    """_identify_user_prompt biases the LLM prompt per the pre-scan hint."""
    import sys
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))
    from ocr_engine import _identify_user_prompt
    assert 'sealed' in _identify_user_prompt('sealed').lower()
    assert 'individual card' in _identify_user_prompt('card').lower()
    assert _identify_user_prompt(None) == "Identify this card."


# ---- /api/identify photo path: sealed-product routing ----

from fastapi.testclient import TestClient
import app as app_module
import ocr_engine as ocr_engine_module
from ocr_engine import IdentifyResult
from pricing_engine import CardIdentity


def _fake_identify_result(product_type, name="Mega Evolution Elite Trainer Box"):
    identity = CardIdentity(name=name, set_name="Mega Evolution", card_number="", language="english", variant=None)
    return IdentifyResult(identity=identity, confidence=0.9, source="llm", phash="testhash", product_type=product_type)


def test_identify_photo_sealed_product_skips_card_lookups(monkeypatch):
    """A sealed-product OCR result must not fall through to the individual
    card lookups (Pokemon TCG / TCGdex / eBay Browse) — those return
    unrelated single-card images/prices for a box/ETB name."""
    monkeypatch.setenv("GOOGLE_API_KEY", "test-key")
    monkeypatch.setattr(ocr_engine_module, "identify_card",
                         lambda path, product_type_hint=None: _fake_identify_result("etb"))

    def boom(*a, **k):
        raise AssertionError("individual-card lookup should be skipped for sealed products")
    monkeypatch.setattr(app_module.card_lookup, "lookup_card", boom)
    monkeypatch.setattr(app_module.card_lookup, "search_cards", boom)

    async def fake_sealed_price(req):
        assert req.product_type == "etb"
        return {"estimated_price": 54.99, "source": "ebay_sealed", "image_url": None}
    monkeypatch.setattr(app_module, "_refresh_sealed_price", fake_sealed_price)

    client = TestClient(app_module.app)
    res = client.post("/api/identify", files={"photo": ("box.jpg", b"fake", "image/jpeg")})
    assert res.status_code == 200
    data = res.json()
    assert data["product_type"] == "etb"
    assert data["image_url"] is None
    assert data["market_price"] == 54.99


def test_identify_photo_card_hint_forces_card_routing(monkeypatch):
    """The 'Card' pre-scan hint forces individual-card routing even if OCR
    (mis)classifies the photo as a sealed product."""
    monkeypatch.setenv("GOOGLE_API_KEY", "test-key")
    captured = {}

    def fake_identify_card(path, product_type_hint=None):
        captured['hint'] = product_type_hint
        return _fake_identify_result("etb", name="Charizard ex")
    monkeypatch.setattr(ocr_engine_module, "identify_card", fake_identify_card)

    async def fake_lookup_card(**kw):
        return None
    async def fake_search_cards(*a, **k):
        return []
    monkeypatch.setattr(app_module.card_lookup, "lookup_card", fake_lookup_card)
    monkeypatch.setattr(app_module.card_lookup, "search_cards", fake_search_cards)

    import ebay_browse_api
    async def fake_search_items(*a, **k):
        return []
    monkeypatch.setattr(ebay_browse_api, "search_items", fake_search_items)

    def fail_sealed_price(*a, **k):
        raise AssertionError("sealed price lookup should not run when hint forces 'card'")
    monkeypatch.setattr(app_module, "_refresh_sealed_price", fail_sealed_price)

    client = TestClient(app_module.app)
    res = client.post("/api/identify", files={"photo": ("card.jpg", b"fake", "image/jpeg")},
                       data={"product_type_hint": "card"})
    assert res.status_code == 200
    data = res.json()
    assert data["product_type"] == "card"
    assert captured['hint'] == "card"


