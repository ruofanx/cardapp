"""Tests for raw_price_resolver.resolve_raw_price's baseline + PriceCharting blend."""
from __future__ import annotations

import asyncio
import os
import sys
from types import SimpleNamespace

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

import raw_price_resolver as rpr


def test_only_baseline_keeps_tcgplayer_price(monkeypatch):
    async def fake_lookup_card(name, set_name, card_number, language="english", variant=None):
        return SimpleNamespace(market_price=50.0, source="tcgplayer")

    async def fake_pc_raw(*args, **kwargs):
        return None

    monkeypatch.setattr(rpr.card_lookup, "lookup_card", fake_lookup_card)
    monkeypatch.setattr(rpr.pricecharting_lookup, "lookup_raw_price", fake_pc_raw)

    result = asyncio.run(rpr.resolve_raw_price(
        "Pikachu", "Base Set", "58/102", language="english",
    ))

    assert result.nm_price == 50.0
    assert result.baseline_label == "TCGplayer (EN)"
    assert result.extra_note is None


def test_only_pricecharting_when_no_baseline(monkeypatch):
    async def fake_lookup_card(name, set_name, card_number, language="english", variant=None):
        return None

    async def fake_browse(*args, **kwargs):
        return None

    async def fake_ebay_raw(*args, **kwargs):
        return None

    async def fake_pc_raw(name, set_name, card_number, language="english", variant=None):
        return SimpleNamespace(price_usd=30.0)

    monkeypatch.setattr(rpr.card_lookup, "lookup_card", fake_lookup_card)
    monkeypatch.setattr(rpr.ebay_browse_api, "median_relevant_price", fake_browse)
    monkeypatch.setattr(rpr.ebay_lookup, "lookup_raw_price", fake_ebay_raw)
    monkeypatch.setattr(rpr.pricecharting_lookup, "lookup_raw_price", fake_pc_raw)

    result = asyncio.run(rpr.resolve_raw_price(
        "Lugia", "Neo Genesis", "9/111", language="japanese",
    ))

    assert result.nm_price == 30.0
    assert result.baseline_label == "PriceCharting Ungraded (sold-based)"
    assert result.extra_note is None


def test_agree_within_threshold_keeps_baseline(monkeypatch):
    async def fake_lookup_card(name, set_name, card_number, language="english", variant=None):
        return SimpleNamespace(market_price=100.0, source="tcgplayer")

    async def fake_pc_raw(name, set_name, card_number, language="english", variant=None):
        return SimpleNamespace(price_usd=108.0)

    monkeypatch.setattr(rpr.card_lookup, "lookup_card", fake_lookup_card)
    monkeypatch.setattr(rpr.pricecharting_lookup, "lookup_raw_price", fake_pc_raw)

    result = asyncio.run(rpr.resolve_raw_price(
        "Charizard", "Base Set", "4/102", language="english",
    ))

    assert result.nm_price == 100.0
    assert result.baseline_label == "TCGplayer (EN)"
    assert result.extra_note is None


def test_divergence_beyond_threshold_switches_to_pricecharting(monkeypatch):
    async def fake_lookup_card(name, set_name, card_number, language="english", variant=None):
        return SimpleNamespace(market_price=100.0, source="tcgplayer")

    async def fake_pc_raw(name, set_name, card_number, language="english", variant=None):
        return SimpleNamespace(price_usd=150.0)

    monkeypatch.setattr(rpr.card_lookup, "lookup_card", fake_lookup_card)
    monkeypatch.setattr(rpr.pricecharting_lookup, "lookup_raw_price", fake_pc_raw)

    result = asyncio.run(rpr.resolve_raw_price(
        "Charizard", "Base Set", "4/102", language="english",
    ))

    assert result.nm_price == 150.0
    assert result.baseline_label == "PriceCharting Ungraded (sold-based)"
    assert "TCGplayer (EN) was $100.00" in result.extra_note
    assert "diverged 50%" in result.extra_note
