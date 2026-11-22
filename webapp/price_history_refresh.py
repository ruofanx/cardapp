"""Refresh `price_history` from PriceCharting's embedded chart data
(`pricecharting_lookup.fetch_chart_history` / `fetch_sealed_chart_history`,
the "used"/ungraded series — a free ~33-month monthly price series per card).

Originally a one-off backfill (see `backfill_historical_prices.py`), this
module is also called weekly by `refresh_job`'s scheduler so the Overview
trend chart keeps picking up PriceCharting's latest monthly chart_data point.

Graded cards are skipped — PriceCharting's chart_data only exposes a single
generic "graded" series, not grade/grader-specific ones, so blending it into
a grade-specific card's history would mix incompatible price scales. Sealed
products are never graded, so all of them are attempted.
"""
from __future__ import annotations

import asyncio
import time
from datetime import datetime, timezone

import logging
import os

# Same choice as app.py: production sets DATABASE_URL. The weekly job used to
# import the SQLite module unconditionally, so chart history never landed in
# the Postgres rows the Overview chart reads.
if os.environ.get("DATABASE_URL"):
    import db_postgres as db
else:
    import db
import pricecharting_lookup as pc

_POSTGRES = bool(os.environ.get("DATABASE_URL"))

log = logging.getLogger(__name__)
SOURCE = "pricecharting_chart_backfill"
REQUEST_DELAY_SECONDS = 1.5


def _recorded_day(value) -> str:
    if hasattr(value, "strftime"):
        return value.strftime("%Y-%m-%d")
    return str(value)[:10]


def _existing_dates(card_id: int) -> set[str]:
    sql = "SELECT recorded_at FROM price_history WHERE card_id = %s" if _POSTGRES \
        else "SELECT recorded_at FROM price_history WHERE card_id = ?"
    with db.connect() as conn:
        if _POSTGRES:
            cur = conn.cursor()
            cur.execute(sql, (card_id,))
            rows = cur.fetchall()
        else:
            rows = conn.execute(sql, (card_id,)).fetchall()
    return {_recorded_day(row["recorded_at"]) for row in rows}


def _insert_rows(rows: list[tuple]) -> None:
    sql = (
        "INSERT INTO price_history (card_id, recorded_at, price_usd, source, source_url) "
        "VALUES (%s, %s, %s, %s, %s)"
        if _POSTGRES else
        "INSERT INTO price_history (card_id, recorded_at, price_usd, source, source_url) "
        "VALUES (?, ?, ?, ?, ?)"
    )
    with db.connect() as conn:
        if _POSTGRES:
            cur = conn.cursor()
            cur.executemany(sql, rows)
        else:
            conn.executemany(sql, rows)
            conn.commit()


def _fetch_cards(sql_pg: str, sql_sqlite: str):
    with db.connect() as conn:
        if _POSTGRES:
            cur = conn.cursor()
            cur.execute(sql_pg)
            return cur.fetchall()
        return conn.execute(sql_sqlite).fetchall()


async def refresh_one(card_id: int, name: str, cutoff_ms: float, fetch) -> int:
    """Fetch chart history for one card/product and insert new rows.
    `fetch` is a zero-arg async callable returning `(points, url)` or None."""
    existing_dates = _existing_dates(card_id)

    result = await fetch()
    if not result:
        log.debug("price_history backfill: card %s %r — no chart data found", card_id, name)
        return 0

    points, url = result
    rows = []
    for ts_ms, price in points:
        if ts_ms < cutoff_ms:
            continue
        recorded_at = datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc) \
            .strftime("%Y-%m-%d %H:%M:%S")
        if recorded_at[:10] in existing_dates:
            continue
        rows.append((card_id, recorded_at, price, SOURCE, url))
        existing_dates.add(recorded_at[:10])

    if rows:
        _insert_rows(rows)
    log.info("price_history backfill: card %s %r → +%d rows from %s", card_id, name, len(rows), url)
    return len(rows)


async def refresh_all(min_days: int = 90) -> dict:
    """Refresh price_history for every raw card and sealed product.

    Returns {"raw_cards": N, "sealed_products": N, "inserted": N}.
    """
    cutoff_ms = time.time() * 1000 - min_days * 86400_000

    raw_cards = _fetch_cards(
        "SELECT id, name, set_name, card_number, language, variant "
        "FROM cards WHERE is_graded = FALSE AND product_type = 'card' ORDER BY id",
        "SELECT id, name, set_name, card_number, language, variant "
        "FROM cards WHERE is_graded = 0 AND product_type = 'card' ORDER BY id",
    )
    sealed_products = _fetch_cards(
        "SELECT id, name, set_name, language, product_type "
        "FROM cards WHERE product_type <> 'card' ORDER BY id",
        "SELECT id, name, set_name, language, product_type "
        "FROM cards WHERE product_type != 'card' ORDER BY id",
    )

    total_inserted = 0

    log.info("price_history refresh: %d raw cards", len(raw_cards))
    for card in raw_cards:
        try:
            total_inserted += await refresh_one(
                card["id"], card["name"], cutoff_ms,
                lambda c=card: pc.fetch_chart_history(
                    c["name"], c["set_name"], c["card_number"],
                    language=c["language"], variant=c["variant"],
                ),
            )
        except Exception as e:
            log.warning("price_history refresh failed for card %s (%s): %s",
                        card["id"], card["name"], e)
        await asyncio.sleep(REQUEST_DELAY_SECONDS)

    log.info("price_history refresh: %d sealed products", len(sealed_products))
    for card in sealed_products:
        try:
            total_inserted += await refresh_one(
                card["id"], card["name"], cutoff_ms,
                lambda c=card: pc.fetch_sealed_chart_history(
                    c["name"], c["set_name"], c["product_type"], language=c["language"],
                ),
            )
        except Exception as e:
            log.warning("price_history refresh failed for sealed %s (%s): %s",
                        card["id"], card["name"], e)
        await asyncio.sleep(REQUEST_DELAY_SECONDS)

    log.info("price_history refresh done — inserted %d rows across %d cards",
             total_inserted, len(raw_cards) + len(sealed_products))
    try:
        from refresh_job import mark_job_ran
        mark_job_ran("weekly_price_history_refresh")
    except Exception as e:
        log.warning("could not record weekly history run: %s", e)

    return {
        "raw_cards": len(raw_cards),
        "sealed_products": len(sealed_products),
        "inserted": total_inserted,
    }
