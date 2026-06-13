"""Tests for pricecharting_lookup.search_products / _parse_search_results —
a last-resort card-identity fallback.

Bug: "Maushold ex" #158 from PriceCharting's "Pokemon Chinese CSV4C" set
can't be found anywhere in the app. TCGdex's zh-cn database HAS registered
the CSV4C set (嘉奖回合, 129 official cards) but its card list is empty and
every direct card-ID lookup 404s — TCGdex knows the set exists but never
populated the cards. Pokemon TCG API is EN-only and doesn't have Chinese
sets at all.

PriceCharting itself DOES have this card (it's the source the user found it
on) — confirmed live: searching pricecharting.com/search-products?q=Maushold+ex
returns a row for "Maushold Ex #158" / "Pokemon Chinese CSV4C" with a price
and a cover-image thumbnail. So PriceCharting's search becomes the final
fallback tier when both Pokemon TCG API and TCGdex come up empty.
"""
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

import asyncio

import httpx

from pricecharting_lookup import _parse_search_results, search_products


# Trimmed real row from https://www.pricecharting.com/search-products?type=prices&q=Maushold+ex
MAUSHOLD_ROW = """
<tr id="product-10514166" data-product="10514166">
    <td class="image">
        <div>
            <a href="https://www.pricecharting.com/game/pokemon-chinese-csv4c/maushold-ex-158" title="10514166">
                <img class="photo" loading="lazy" src="https://storage.googleapis.com/images.pricecharting.com/jbargpapxhi5nnhv/60.jpg" />
            </a>
        </div>
    </td>
    <td class="title">
        <a href="https://www.pricecharting.com/game/pokemon-chinese-csv4c/maushold-ex-158" title="10514166">
            Maushold Ex #158</a>
        <div class="console-in-title">
            <a href="/console/pokemon-chinese-csv4c">
                Pokemon Chinese CSV4C
            </a>
        <div>
    </td>
    <td class="console phone-landscape-hidden">
        <a href="/console/pokemon-chinese-csv4c">
            Pokemon Chinese CSV4C
        </a>
    </td>
    <td class="price numeric used_price">
        <span class="js-price">$322.31</span>
    </td>
</tr>
"""

# A non-Pokemon product that could match by accident (e.g. video game) —
# must be filtered out.
NON_POKEMON_ROW = """
<tr id="product-99">
    <td class="image"><div><a href="#"><img class="photo" src="https://storage.googleapis.com/images.pricecharting.com/zzzzzzzzzzzzzzzz/60.jpg" /></a></div></td>
    <td class="title"><a href="https://www.pricecharting.com/game/super-mario-bros/mario-1">Mario #1</a></td>
    <td class="console phone-landscape-hidden"><a href="/console/super-mario-bros">Super Mario Bros</a></td>
    <td class="price numeric used_price"><span class="js-price">$50.00</span></td>
</tr>
"""

JAPANESE_ROW = """
<tr id="product-555">
    <td class="image"><div><a href="#"><img class="photo" src="https://storage.googleapis.com/images.pricecharting.com/abcdefghijklmnop/60.jpg" /></a></div></td>
    <td class="title"><a href="https://www.pricecharting.com/game/pokemon-japanese-future-flash/tamatama-1">Tamatama #1</a></td>
    <td class="console phone-landscape-hidden"><a href="/console/pokemon-japanese-future-flash">Pokemon Japanese Future Flash</a></td>
    <td class="price numeric used_price"><span class="js-price">$1.50</span></td>
</tr>
"""


def test_parse_search_results_extracts_chinese_card():
    results = _parse_search_results(MAUSHOLD_ROW)
    assert len(results) == 1
    r = results[0]
    assert r.product_id == "10514166"
    assert r.name == "Maushold Ex"
    assert r.card_number == "158"
    assert r.set_name == "Pokemon Chinese CSV4C"
    assert r.url == "https://www.pricecharting.com/game/pokemon-chinese-csv4c/maushold-ex-158"
    assert r.price_usd == 322.31
    assert r.language == "chinese"
    assert r.image_url == "https://storage.googleapis.com/images.pricecharting.com/jbargpapxhi5nnhv/240.jpg"
    assert r.image_url_large == "https://storage.googleapis.com/images.pricecharting.com/jbargpapxhi5nnhv/1600.jpg"


def test_parse_search_results_filters_non_pokemon():
    assert _parse_search_results(NON_POKEMON_ROW) == []


def test_parse_search_results_detects_japanese():
    results = _parse_search_results(JAPANESE_ROW)
    assert len(results) == 1
    assert results[0].language == "japanese"
    assert results[0].name == "Tamatama"
    assert results[0].card_number == "1"


def test_parse_search_results_respects_limit():
    html_blob = MAUSHOLD_ROW + JAPANESE_ROW
    assert len(_parse_search_results(html_blob, limit=1)) == 1
    assert len(_parse_search_results(html_blob, limit=10)) == 2


def test_to_dict_shape():
    d = _parse_search_results(MAUSHOLD_ROW)[0].to_dict()
    assert d["name"] == "Maushold Ex"
    assert d["card_number"] == "158"
    assert d["set_name"] == "Pokemon Chinese CSV4C"
    assert d["market_price"] == 322.31
    assert d["language"] == "chinese"
    assert d["image_url"].endswith("1600.jpg")


def test_search_products_hits_search_endpoint(monkeypatch):
    captured = {}

    def handler(request):
        captured['url'] = str(request.url)
        return httpx.Response(200, text=MAUSHOLD_ROW)

    transport = httpx.MockTransport(handler)

    class FakeClient(httpx.AsyncClient):
        def __init__(self, *a, **kw):
            kw['transport'] = transport
            super().__init__(*a, **kw)

    monkeypatch.setattr("pricecharting_lookup.httpx.AsyncClient", FakeClient)

    results = asyncio.run(search_products("Maushold ex"))
    assert len(results) == 1
    assert results[0].card_number == "158"
    assert "search-products" in captured['url']
    assert "Maushold" in captured['url']


def test_search_products_empty_query_returns_empty():
    assert asyncio.run(search_products("")) == []
    assert asyncio.run(search_products("   ")) == []
