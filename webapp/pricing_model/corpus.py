"""Bulk training-corpus harvest for the pricing model.

EN-only in this version: Pokemon TCG API's per-set card list is reliable
and gives rarity + release date directly; TCGdex connectivity could not be
verified during planning, so JP/CN corpus harvesting is deferred (see plan
Global Constraints). JP/CN cards still get predictions via the fitted
language-factor coefficient and a wider confidence band.

Reuses pricecharting_lookup's existing per-card page fetch/cache machinery
(no new scraping code) for both the raw+PSA10+Grade9 snapshot and the
~33-month raw price history.
"""
from __future__ import annotations

import asyncio
import logging

import httpx

import pricecharting_lookup as pc
from pricing_model import db as pmdb
from pricing_model.features import era_bucket

log = logging.getLogger(__name__)

POKEMONTCG_BASE = "https://api.pokemontcg.io/v2"
PER_CARD_SLEEP_SEC = 1.5

# EN sets spanning eras, used to build the training corpus. Extend this list
# to widen/refresh corpus coverage.
TRAINING_SETS: list[str] = [
    "sv8", "sv7", "sv6", "sv4", "sv3pt5", "sv3", "sv1",
    "swsh12pt5", "swsh12", "swsh9", "swsh7", "swsh4",
    "sm12", "sm9", "sm5",
    "xy12", "xy7",
]


async def _fetch_set_cards(set_id: str) -> list[dict]:
    async with httpx.AsyncClient(timeout=20.0) as client:
        resp = await client.get(
            f"{POKEMONTCG_BASE}/cards",
            params={"q": f"set.id:{set_id}", "pageSize": 250},
        )
        resp.raise_for_status()
        return resp.json().get("data", [])


def _iso_month(ts_ms: int) -> str:
    from datetime import datetime, timezone
    dt = datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc)
    return f"{dt.year:04d}-{dt.month:02d}"


async def harvest_set(set_id: str) -> int:
    cards = await _fetch_set_cards(set_id)
    harvested = 0
    for card in cards:
        name = card.get("name")
        number = card.get("number")
        set_info = card.get("set") or {}
        set_name = set_info.get("name", "")
        release_raw = set_info.get("releaseDate", "")  # "YYYY/MM/DD"
        if not (name and number and release_raw):
            continue
        release_date = release_raw.replace("/", "-")
        card_key = f"{set_id}-{number}"

        pc_result = await pc.lookup_raw_price(name, set_name, number, "english")
        history = await pc.fetch_chart_history(name, set_name, number, "english")
        await asyncio.sleep(PER_CARD_SLEEP_SEC)

        if pc_result is None or history is None:
            continue
        points, _url = history

        pmdb.upsert_corpus_card(pmdb.CorpusCardRow(
            card_key=card_key, name=name, set_name=set_name, card_number=number,
            rarity_raw=card.get("rarity"), era=era_bucket(release_date),
            language="english", release_date=release_date,
            psa10_price_usd=pc_result.all_prices.get("PSA 10"),
            grade9_price_usd=pc_result.all_prices.get("Grade 9"),
        ))
        pmdb.insert_corpus_history(card_key, [(_iso_month(ts), price) for ts, price in points])
        harvested += 1
    return harvested


async def harvest_all(set_ids: list[str] | None = None) -> dict:
    set_ids = set_ids or TRAINING_SETS
    total = 0
    per_set: dict[str, int] = {}
    for set_id in set_ids:
        try:
            n = await harvest_set(set_id)
        except Exception as e:
            log.warning("corpus harvest failed for set %s: %s", set_id, e)
            n = 0
        per_set[set_id] = n
        total += n
    return {"total_cards": total, "per_set": per_set}
