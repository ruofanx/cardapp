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

def test_sealed_product_urls_unknown_type_returns_empty():
    """Unknown product_type yields no candidate URLs."""
    from pricecharting_lookup import _sealed_product_urls
    assert _sealed_product_urls("Base Set", "Base Set", "mystery_box") == []

def test_sealed_product_urls_strips_print_run_qualifier():
    """Old-set sealed products carry a print-run qualifier ("Unlimited",
    "1st Edition", "First Edition") in `set_name` that PriceCharting doesn't
    use for its sealed-product set slugs — try the raw set name first, then
    a candidate with the qualifier stripped."""
    from pricecharting_lookup import _sealed_product_urls
    assert _sealed_product_urls("Jungle Unlimited", "Jungle Unlimited", "booster_pack") == [
        "https://www.pricecharting.com/game/pokemon-jungle-unlimited/booster-pack",
        "https://www.pricecharting.com/game/pokemon-jungle/booster-pack",
    ]
    assert _sealed_product_urls("Fossil 1st Edition", "Fossil 1st Edition", "booster_box") == [
        "https://www.pricecharting.com/game/pokemon-fossil-1st-edition/booster-box",
        "https://www.pricecharting.com/game/pokemon-fossil/booster-box",
    ]

def test_sealed_product_urls_no_qualifier_yields_single_candidate():
    """Set names without a print-run qualifier produce just one URL."""
    from pricecharting_lookup import _sealed_product_urls
    assert _sealed_product_urls("Chaos Rising", "Chaos Rising", "etb") == [
        "https://www.pricecharting.com/game/pokemon-chaos-rising/elite-trainer-box",
    ]

def test_parse_cover_image_base():
    """_parse_cover_image_base extracts the storage.googleapis.com base URL
    from a PriceCharting product page's <div class="cover"> image tag."""
    from pricecharting_lookup import _parse_cover_image_base
    html = """
    <div class="cover">
      <a href="#"><img src='https://storage.googleapis.com/images.pricecharting.com/4y25v47sayxaguat/240.jpg' alt="Elite Trainer Box Pokemon Chaos Rising Prices"></a>
    </div>
    """
    assert _parse_cover_image_base(html) == "https://storage.googleapis.com/images.pricecharting.com/4y25v47sayxaguat/"

def test_parse_cover_image_base_missing():
    """Pages without a cover image (not in catalogue) return None."""
    from pricecharting_lookup import _parse_cover_image_base
    assert _parse_cover_image_base("<html><body>no cover here</body></html>") is None

def test_cover_image_urls():
    """_cover_image_urls expands a base URL into thumbnail + full-size variants."""
    from pricecharting_lookup import _cover_image_urls
    base = "https://storage.googleapis.com/images.pricecharting.com/4y25v47sayxaguat/"
    assert _cover_image_urls(base) == (base + "240.jpg", base + "1600.jpg")
    assert _cover_image_urls(None) == (None, None)

def test_parse_chart_series():
    """_parse_chart_series extracts a named series from embedded
    VGPC.chart_data as sorted (timestamp_ms, price_usd) pairs, dropping
    zero-value points."""
    from pricecharting_lookup import _parse_chart_series
    html = (
        "<script>VGPC.chart_data = "
        '{"used": [[1700000000000, 31750], [1690000000000, 40400], [1680000000000, 0]], '
        '"new": [[1700000000000, 26600]]}'
        ";</script>"
    )
    assert _parse_chart_series(html, "used") == [(1690000000000, 404.0), (1700000000000, 317.5)]
    assert _parse_chart_series(html, "new") == [(1700000000000, 266.0)]
    assert _parse_chart_series(html, "missing_series") is None

def test_parse_chart_series_no_chart_data():
    from pricecharting_lookup import _parse_chart_series
    assert _parse_chart_series("<html>nothing here</html>") is None

def test_fetch_sealed_chart_history_unknown_type_returns_none():
    """Unknown product_type returns None without fetching."""
    import asyncio
    from pricecharting_lookup import fetch_sealed_chart_history
    result = asyncio.run(fetch_sealed_chart_history("Base Set", "Base Set", "mystery_box"))
    assert result is None

def test_fetch_sealed_chart_history_exists():
    """fetch_sealed_chart_history is importable with correct signature."""
    from pricecharting_lookup import fetch_sealed_chart_history
    sig = inspect.signature(fetch_sealed_chart_history)
    assert "name" in sig.parameters
    assert "product_type" in sig.parameters


# ---- Print-run-qualifier fallback (e.g. "Jungle Unlimited" -> "Jungle") ----

import asyncio
import httpx

_BOOSTER_PACK_PAGE = """
<html><body>
<div class="cover">
  <a href="#"><img src='https://storage.googleapis.com/images.pricecharting.com/h7j65jus7pvqacmy/240.jpg' alt="Jungle Booster Pack Prices"></a>
</div>
<table>
<tr><td>Ungraded</td><td class="price js-price">$305.00</td></tr>
</table>
<script>VGPC.chart_data = {"used": [[1780293600000, 30500]]};</script>
</body></html>
"""


def _mock_transport_redirect_then_hit(redirect_path, hit_path, hit_html):
    """MockTransport: requests to `redirect_path` 302 to /search-products
    (PriceCharting's "not in catalogue" behaviour); requests to `hit_path`
    return `hit_html`."""
    def handler(request):
        path = request.url.path
        if path == redirect_path:
            return httpx.Response(302, headers={"Location": "/search-products?q=jungle+unlimited"})
        if path == hit_path:
            return httpx.Response(200, text=hit_html)
        if path == "/search-products":
            return httpx.Response(200, text="<html>no results</html>")
        return httpx.Response(404)
    return httpx.MockTransport(handler)


def test_lookup_sealed_price_falls_back_to_stripped_set_name(monkeypatch):
    """"Jungle Unlimited" 404s into /search-products; "Jungle" (qualifier
    stripped) hits the real PriceCharting booster-pack page."""
    transport = _mock_transport_redirect_then_hit(
        "/game/pokemon-jungle-unlimited/booster-pack",
        "/game/pokemon-jungle/booster-pack",
        _BOOSTER_PACK_PAGE,
    )

    class FakeClient(httpx.AsyncClient):
        def __init__(self, *a, **kw):
            kw['transport'] = transport
            super().__init__(*a, **kw)

    monkeypatch.setattr("pricecharting_lookup.httpx.AsyncClient", FakeClient)

    from pricecharting_lookup import lookup_sealed_price
    result = asyncio.run(lookup_sealed_price("Jungle Unlimited", "Jungle Unlimited", "booster_pack"))
    assert result is not None
    assert result.url == "https://www.pricecharting.com/game/pokemon-jungle/booster-pack"
    assert result.price_usd == 305.0
    assert result.image_url == "https://storage.googleapis.com/images.pricecharting.com/h7j65jus7pvqacmy/240.jpg"


def test_fetch_sealed_chart_history_falls_back_to_stripped_set_name(monkeypatch):
    """Same fallback for chart-history backfill."""
    transport = _mock_transport_redirect_then_hit(
        "/game/pokemon-jungle-unlimited/booster-pack",
        "/game/pokemon-jungle/booster-pack",
        _BOOSTER_PACK_PAGE,
    )

    class FakeClient(httpx.AsyncClient):
        def __init__(self, *a, **kw):
            kw['transport'] = transport
            super().__init__(*a, **kw)

    monkeypatch.setattr("pricecharting_lookup.httpx.AsyncClient", FakeClient)

    from pricecharting_lookup import fetch_sealed_chart_history
    result = asyncio.run(fetch_sealed_chart_history("Jungle Unlimited", "Jungle Unlimited", "booster_pack"))
    assert result is not None
    points, url = result
    assert url == "https://www.pricecharting.com/game/pokemon-jungle/booster-pack"
    assert points == [(1780293600000, 305.0)]


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


def test_identify_photo_number_mismatch_skips_broad_image(monkeypatch):
    """A broad name-only search hit whose printed number doesn't match what
    OCR read off the photo is a DIFFERENT printing (different art) — its
    image_url must not become the response's top-level image_url, so the
    frontend falls back to the user's own captured photo instead of showing
    the wrong card's artwork."""
    from card_lookup import CardLookupResult

    monkeypatch.setenv("GOOGLE_API_KEY", "test-key")
    fake_result = _fake_identify_result("card", name="N's Zekrom")
    fake_result.identity.card_number = "031"
    monkeypatch.setattr(
        ocr_engine_module, "identify_card",
        lambda path, product_type_hint=None: fake_result,
    )

    async def fake_lookup_card(**kw):
        return None  # structured lookup rejects (score below threshold)
    monkeypatch.setattr(app_module.card_lookup, "lookup_card", fake_lookup_card)

    async def fake_search_cards(*a, **k):
        return [CardLookupResult(
            name="N's Zekrom", set_name="Ascended Heroes", card_number="155",
            image_url="https://images.scrydex.com/pokemon/me2pt5-155/small",
            image_url_large="https://images.scrydex.com/pokemon/me2pt5-155/large",
            rarity="Rare", market_price=None, tcg_id="me2pt5-155",
        )]
    monkeypatch.setattr(app_module.card_lookup, "search_cards", fake_search_cards)

    import ebay_browse_api
    async def fake_search_items(*a, **k):
        return []
    monkeypatch.setattr(ebay_browse_api, "search_items", fake_search_items)

    client = TestClient(app_module.app)
    res = client.post("/api/identify", files={"photo": ("card.jpg", b"fake", "image/jpeg")})
    assert res.status_code == 200
    data = res.json()
    assert data["image_url"] is None
    # The broad result is still surfaced as a candidate the user can pick,
    # with its OWN (correct, for that printing) image.
    assert data["candidates"][0]["image_url"] == "https://images.scrydex.com/pokemon/me2pt5-155/small"


# ---- sealed product images via eBay active listings ----

import ebay_browse_api


def _ebay_item(title, image_url="https://i.ebayimg.com/images/g/abc/s-l225.jpg", price=59.99):
    return ebay_browse_api.EbayItem(
        item_id="1", title=title, image_url=image_url,
        image_url_large=image_url.replace("s-l225.", "s-l1600.") if image_url else None,
        price_usd=price, currency="USD", condition="New",
        seller_country="US", item_url="https://ebay.com/itm/1", sold=False,
    )


def test_lookup_sealed_image_exists_with_signature():
    assert hasattr(ebay_browse_api, "lookup_sealed_image")
    sig = inspect.signature(ebay_browse_api.lookup_sealed_image)
    assert "name" in sig.parameters
    assert "set_name" in sig.parameters
    assert "product_type" in sig.parameters


def test_lookup_sealed_image_skips_irrelevant_listings(monkeypatch):
    """Sold/opened/unrelated listings shouldn't be used as the product image
    — only a listing whose title actually matches the sealed product."""
    async def fake_search_items(query, **kw):
        assert "Mega Evolution" in query
        assert "Elite Trainer Box" in query
        return [
            _ebay_item("Pokemon Mega Evolution ETB OPENED no packs"),
            _ebay_item("Random unrelated graded slab PSA 10"),
            _ebay_item("Pokemon Mega Evolution Elite Trainer Box Sealed New"),
        ]
    monkeypatch.setattr(ebay_browse_api, "search_items", fake_search_items)

    import asyncio
    item = asyncio.run(ebay_browse_api.lookup_sealed_image(
        name="Mega Evolution Elite Trainer Box",
        set_name="Mega Evolution",
        product_type="etb",
    ))
    assert item is not None
    assert item.title == "Pokemon Mega Evolution Elite Trainer Box Sealed New"
    assert item.image_url.endswith("s-l225.jpg")
    assert item.image_url_large.endswith("s-l1600.jpg")


def test_lookup_sealed_image_returns_none_when_nothing_relevant(monkeypatch):
    async def fake_search_items(query, **kw):
        return [_ebay_item("Pokemon Mega Evolution ETB OPENED no packs")]
    monkeypatch.setattr(ebay_browse_api, "search_items", fake_search_items)

    import asyncio
    item = asyncio.run(ebay_browse_api.lookup_sealed_image(
        name="Mega Evolution Elite Trainer Box",
        set_name="Mega Evolution",
        product_type="etb",
    ))
    assert item is None


def test_lookup_sealed_image_prefers_generic_over_sub_expansion(monkeypatch):
    """"Mega Evolution" is a series with multiple sub-expansion ETBs (e.g.
    "Mega Evolution: Chaos Rising", "Mega Evolution—Phantasmal Flames")
    that all mention "Mega Evolution Elite Trainer Box" and so pass the
    relevance filter. When the requested product has no sub-expansion
    subtitle, the listing whose title has no extra subtitle words should
    be picked over ones that do — even if it's not first in eBay's
    relevance ranking."""
    async def fake_search_items(query, **kw):
        return [
            _ebay_item("Pokemon Mega Evolutions ME4 Chaos Rising Elite Trainer Box Sealed",
                       image_url="https://i.ebayimg.com/images/g/chaos/s-l225.jpg"),
            _ebay_item("Pokemon TCG: Mega Evolution—Phantasmal Flames Elite Trainer Box SEALED NEW",
                       image_url="https://i.ebayimg.com/images/g/flames/s-l225.jpg"),
            _ebay_item("Pokemon TCG Mega Evolution Elite Trainer Box BRAND NEW & SEALED",
                       image_url="https://i.ebayimg.com/images/g/generic/s-l225.jpg"),
        ]
    monkeypatch.setattr(ebay_browse_api, "search_items", fake_search_items)

    import asyncio
    item = asyncio.run(ebay_browse_api.lookup_sealed_image(
        name="Mega Evolution Elite Trainer Box",
        set_name="Mega Evolution",
        product_type="etb",
    ))
    assert item is not None
    assert item.image_url == "https://i.ebayimg.com/images/g/generic/s-l225.jpg"


def test_refresh_sealed_price_includes_ebay_image(monkeypatch):
    """_refresh_sealed_price falls back to an eBay-listing image when
    PriceCharting has no cover image for this product."""
    async def fake_lookup_sealed_image(**kw):
        return _ebay_item("Pokemon Mega Evolution Elite Trainer Box Sealed New")
    monkeypatch.setattr(ebay_browse_api, "lookup_sealed_image", fake_lookup_sealed_image)

    async def fake_lookup_sealed_price(**kw):
        return None
    monkeypatch.setattr("pricecharting_lookup.lookup_sealed_price", fake_lookup_sealed_price)

    async def fake_recent_n_mean(**kw):
        from ebay_lookup import EbayRecentMean
        return EbayRecentMean(
            mean_usd=54.99, median_usd=54.99, sample_size=5, requested_n=5,
            raw_sample_size=5, period_days=90, low_usd=50.0, high_usd=60.0,
            sold_url="https://ebay.com/sch", cached=False, is_graded=False, sales=[],
        )
    monkeypatch.setattr("ebay_lookup.lookup_sealed_recent_n_mean", fake_recent_n_mean)

    import asyncio
    from app import RefreshPriceRequest
    result = asyncio.run(app_module._refresh_sealed_price(RefreshPriceRequest(
        name="Mega Evolution Elite Trainer Box", set_name="Mega Evolution", product_type="etb",
    )))
    assert result["image_url"] == "https://i.ebayimg.com/images/g/abc/s-l225.jpg"
    assert result["image_url_large"] == "https://i.ebayimg.com/images/g/abc/s-l1600.jpg"
    assert result["estimated_price"] == 54.99


def test_refresh_sealed_price_pricecharting_fallback(monkeypatch):
    """When eBay sold-listing data is unavailable (the common case — see
    EbayHtmlFetcher 403s), _refresh_sealed_price falls back to PriceCharting.
    Regression test for a bug where this branch read `pc.ungraded_usd`,
    an attribute PriceChartingResult doesn't have (it's `price_usd`),
    raising AttributeError and silently dropping the eBay image too."""
    async def fake_lookup_sealed_image(**kw):
        return None
    monkeypatch.setattr(ebay_browse_api, "lookup_sealed_image", fake_lookup_sealed_image)

    async def fake_recent_n_mean(**kw):
        return None
    monkeypatch.setattr("ebay_lookup.lookup_sealed_recent_n_mean", fake_recent_n_mean)

    from pricecharting_lookup import PriceChartingResult
    async def fake_lookup_sealed_price(**kw):
        return PriceChartingResult(
            url="https://www.pricecharting.com/game/pokemon-chaos-rising/elite-trainer-box",
            grade_label="Ungraded", price_usd=84.31, all_prices={"Ungraded": 84.31}, cached=False,
        )
    monkeypatch.setattr("pricecharting_lookup.lookup_sealed_price", fake_lookup_sealed_price)

    import asyncio
    from app import RefreshPriceRequest
    result = asyncio.run(app_module._refresh_sealed_price(RefreshPriceRequest(
        name="Chaos Rising", set_name="Chaos Rising", product_type="etb",
    )))
    assert result["estimated_price"] == 84.31
    assert result["source"] == "pricecharting_sealed"


def test_refresh_sealed_price_prefers_pricecharting_image(monkeypatch):
    """PriceCharting's cover art (official Pokemon TCG box art) is
    preferred over eBay's active-listing seller photo when both are
    available."""
    from pricecharting_lookup import PriceChartingResult
    pc_image = "https://storage.googleapis.com/images.pricecharting.com/4y25v47sayxaguat/240.jpg"
    pc_image_large = "https://storage.googleapis.com/images.pricecharting.com/4y25v47sayxaguat/1600.jpg"

    async def fake_lookup_sealed_price(**kw):
        return PriceChartingResult(
            url="https://www.pricecharting.com/game/pokemon-chaos-rising/elite-trainer-box",
            grade_label="Ungraded", price_usd=84.31, all_prices={"Ungraded": 84.31},
            cached=False, image_url=pc_image, image_url_large=pc_image_large,
        )
    monkeypatch.setattr("pricecharting_lookup.lookup_sealed_price", fake_lookup_sealed_price)

    async def fake_lookup_sealed_image(**kw):
        return _ebay_item("Pokemon Chaos Rising Elite Trainer Box Sealed New")
    monkeypatch.setattr(ebay_browse_api, "lookup_sealed_image", fake_lookup_sealed_image)

    async def fake_recent_n_mean(**kw):
        return None
    monkeypatch.setattr("ebay_lookup.lookup_sealed_recent_n_mean", fake_recent_n_mean)

    import asyncio
    from app import RefreshPriceRequest
    result = asyncio.run(app_module._refresh_sealed_price(RefreshPriceRequest(
        name="Chaos Rising", set_name="Chaos Rising", product_type="etb",
    )))
    assert result["image_url"] == pc_image
    assert result["image_url_large"] == pc_image_large
    assert result["estimated_price"] == 84.31
    assert result["source"] == "pricecharting_sealed"


def test_identify_photo_sealed_product_includes_image(monkeypatch):
    """End-to-end: /api/identify for a sealed-product scan returns a real
    product photo (eBay active-listing image) as image_url, not null."""
    monkeypatch.setenv("GOOGLE_API_KEY", "test-key")
    monkeypatch.setattr(ocr_engine_module, "identify_card",
                         lambda path, product_type_hint=None: _fake_identify_result("etb"))
    monkeypatch.setattr(app_module.card_lookup, "lookup_card",
                         lambda *a, **k: (_ for _ in ()).throw(AssertionError("skip")))
    monkeypatch.setattr(app_module.card_lookup, "search_cards",
                         lambda *a, **k: (_ for _ in ()).throw(AssertionError("skip")))

    async def fake_sealed_price(req):
        return {
            "estimated_price": 54.99,
            "source": "ebay_sealed",
            "image_url": "https://i.ebayimg.com/images/g/abc/s-l225.jpg",
            "image_url_large": "https://i.ebayimg.com/images/g/abc/s-l1600.jpg",
        }
    monkeypatch.setattr(app_module, "_refresh_sealed_price", fake_sealed_price)

    client = TestClient(app_module.app)
    res = client.post("/api/identify", files={"photo": ("box.jpg", b"fake", "image/jpeg")})
    assert res.status_code == 200
    data = res.json()
    assert data["image_url"] == "https://i.ebayimg.com/images/g/abc/s-l225.jpg"
    assert data["candidates"][0]["image_url_large"] == "https://i.ebayimg.com/images/g/abc/s-l1600.jpg"


# ---- text search for sealed products (Scan search bar SEALED toggle) ----

@pytest.mark.parametrize("query, expected_name, expected_type", [
    ("chaos rising etb", "Chaos Rising", "etb"),
    ("Chaos Rising Elite Trainer Box", "Chaos Rising", "etb"),
    ("151 booster box", "151", "booster_box"),
    ("evolving skies booster pack", "Evolving Skies", "booster_pack"),
    ("paldea tin", "Paldea", "tin"),
    ("scarlet violet bundle", "Scarlet Violet", "bundle"),
    ("phantasmal flames", "Phantasmal Flames", "etb"),  # no type phrase -> defaults to etb
])
def test_parse_sealed_text_query(query, expected_name, expected_type):
    name, set_name, product_type = app_module._parse_sealed_text_query(query)
    assert name == expected_name
    assert set_name == expected_name
    assert product_type == expected_type


def test_identify_text_sealed_returns_candidate_with_image(monkeypatch):
    """The Scan search bar's SEALED toggle posts a typed name as JSON
    {query, product_type_hint: 'sealed'} — this can't go through
    card_lookup.search_cards (single-card catalogues only), so it should
    route to the eBay-backed sealed lookup and return a candidate with the
    parsed product_type, name, and an image."""
    monkeypatch.setattr(app_module.card_lookup, "search_cards",
                         lambda *a, **k: (_ for _ in ()).throw(AssertionError("skip")))

    async def fake_sealed_price(req):
        assert req.name == "Chaos Rising"
        assert req.set_name == "Chaos Rising"
        assert req.product_type == "etb"
        return {
            "estimated_price": 79.88,
            "source": "ebay_sealed",
            "image_url": "https://i.ebayimg.com/images/g/chaos/s-l225.jpg",
            "image_url_large": "https://i.ebayimg.com/images/g/chaos/s-l1600.jpg",
        }
    monkeypatch.setattr(app_module, "_refresh_sealed_price", fake_sealed_price)

    client = TestClient(app_module.app)
    res = client.post("/api/identify", json={"query": "chaos rising etb", "product_type_hint": "sealed"})
    assert res.status_code == 200
    data = res.json()
    assert data["product_type"] == "etb"
    assert data["identity"]["name"] == "Chaos Rising"
    assert data["market_price"] == 79.88
    assert data["image_url"] == "https://i.ebayimg.com/images/g/chaos/s-l225.jpg"
    assert data["candidates"][0]["product_type"] == "etb"
    assert data["candidates"][0]["image_url_large"] == "https://i.ebayimg.com/images/g/chaos/s-l1600.jpg"


def test_identify_text_card_hint_still_uses_card_search(monkeypatch):
    """product_type_hint: 'card' (or absent) on a JSON text query keeps
    using the existing card_lookup.search_cards path — only 'sealed'
    routes to the eBay sealed lookup."""
    called = {}
    async def fake_search_cards(query, **kw):
        called["query"] = query
        return []
    monkeypatch.setattr(app_module.card_lookup, "search_cards", fake_search_cards)

    client = TestClient(app_module.app)
    res = client.post("/api/identify", json={"query": "Charizard", "product_type_hint": "card"})
    assert res.status_code == 200
    assert called["query"] == "Charizard"
    assert res.json()["mode"] == "text_search"

