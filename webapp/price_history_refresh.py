"""
Refresh `price_history` from PriceCharting's embedded chart data
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

import db
import pricecharting_lookup as pc

SOURCE = "pricecharting_chart_backfill"
REQUEST_DELAY_SECONDS = 1.5


async def refresh_one(card_id: int, name: str, cutoff_ms: float, fetch) -> int:
    """Fetch chart history for one card/product and insert new rows.
    `fetch` is a zero-arg async callable returning `(points, url)` or None."""
    with db.connect() as conn:
        existing_dates = {
            row["recorded_at"][:10]
            for row in conn.execute(
                "SELECT recorded_at FROM price_history WHERE card_id = ?",
                (card_id,),
            ).fetchall()
        }

    result = await fetch()
    if not result:
        print(f"  card {card_id:>3} {name!r}: no chart data found")
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
        with db.connect() as conn:
            conn.executemany(
                "INSERT INTO price_history (card_id, recorded_at, price_usd, source, source_url) "
                "VALUES (?, ?, ?, ?, ?)",
                rows,
            )
            conn.commit()
    print(f"  card {card_id:>3} {name!r}: +{len(rows)} rows from {url}")
    return len(rows)


async def refresh_all(min_days: int = 90) -> dict:
    """Refresh price_history for every raw card and sealed product.

    Returns {"raw_cards": N, "sealed_products": N, "inserted": N}.
    """
    cutoff_ms = time.time() * 1000 - min_days * 86400_000

    with db.connect() as conn:
        raw_cards = conn.execute(
            "SELECT id, name, set_name, card_number, language, variant "
            "FROM cards WHERE is_graded = 0 AND product_type = 'card' ORDER BY id"
        ).fetchall()
        sealed_products = conn.execute(
            "SELECT id, name, set_name, language, product_type "
            "FROM cards WHERE product_type != 'card' ORDER BY id"
        ).fetchall()

    total_inserted = 0

    print(f"Raw cards ({len(raw_cards)}):")
    for card in raw_cards:
        total_inserted += await refresh_one(
            card["id"], card["name"], cutoff_ms,
            lambda c=card: pc.fetch_chart_history(
                c["name"], c["set_name"], c["card_number"],
                language=c["language"], variant=c["variant"],
            ),
        )
        await asyncio.sleep(REQUEST_DELAY_SECONDS)

    print(f"\nSealed products ({len(sealed_products)}):")
    for card in sealed_products:
        total_inserted += await refresh_one(
            card["id"], card["name"], cutoff_ms,
            lambda c=card: pc.fetch_sealed_chart_history(
                c["name"], c["set_name"], c["product_type"], language=c["language"],
            ),
        )
        await asyncio.sleep(REQUEST_DELAY_SECONDS)

    print(f"\nDone — inserted {total_inserted} historical price rows across "
          f"{len(raw_cards) + len(sealed_products)} cards (source={SOURCE!r}).")

    return {
        "raw_cards": len(raw_cards),
        "sealed_products": len(sealed_products),
        "inserted": total_inserted,
    }
