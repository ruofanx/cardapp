# webapp/tests/test_pricing_corpus.py
from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from pricing_model import corpus, db as pmdb
import os

os.environ["PRICING_MODEL_DB"] = "/tmp/test_pricing_corpus.sqlite"


def _fresh_db():
    if pmdb.DB_PATH.exists():
        pmdb.DB_PATH.unlink()
    pmdb.init_db()


_FAKE_TCG_API_RESPONSE = {
    "data": [
        {
            "name": "Charizard ex", "number": "199",
            "rarity": "Special Illustration Rare",
            "set": {"id": "sv8", "name": "Surging Sparks", "releaseDate": "2024/11/08"},
        },
        {
            "name": "Pikachu", "number": "5",
            "rarity": "Common",
            "set": {"id": "sv8", "name": "Surging Sparks", "releaseDate": "2024/11/08"},
        },
    ]
}


def test_harvest_set_writes_corpus_cards_and_history():
    _fresh_db()

    fake_response = SimpleNamespace(
        status_code=200,
        json=lambda: _FAKE_TCG_API_RESPONSE,
        raise_for_status=lambda: None,
    )

    fake_pc_result = SimpleNamespace(
        price_usd=100.0,
        all_prices={"Ungraded": 100.0, "PSA 10": 450.0, "Grade 9": 180.0},
    )

    async def fake_get(*args, **kwargs):
        return fake_response

    with patch("httpx.AsyncClient.get", new=AsyncMock(side_effect=fake_get)), \
         patch("pricecharting_lookup.lookup_raw_price", new=AsyncMock(return_value=fake_pc_result)), \
         patch("pricecharting_lookup.fetch_chart_history",
               new=AsyncMock(return_value=([(1700000000000, 90.0), (1702592000000, 100.0)], "https://example.com"))):
        n = asyncio.run(corpus.harvest_set("sv8"))

    assert n == 2
    cards = pmdb.get_all_corpus_cards()
    assert len(cards) == 2
    names = {c.name for c in cards}
    assert names == {"Charizard ex", "Pikachu"}
    charizard = next(c for c in cards if c.name == "Charizard ex")
    assert charizard.psa10_price_usd == 450.0
    assert charizard.grade9_price_usd == 180.0
    assert charizard.era == "sv"

    history = pmdb.get_corpus_history(charizard.card_key)
    assert len(history) == 2
