"""
One-off backfill: pull each raw card's multi-year price history out of
PriceCharting's embedded chart data (`pricecharting_lookup.fetch_chart_history`,
the "used"/ungraded series) and insert it into `price_history`.

Why: `price_history` only had ~3 weeks of daily snapshots from this app's own
collection. PriceCharting's product pages embed ~33 months of monthly price
points for free, which gives the Overview chart a real trend to show on the
3M/1Y/ALL ranges instead of an empty/flat line.

Graded cards are skipped — PriceCharting's chart_data only exposes a single
generic "graded" series, not grade/grader-specific ones, so backfilling it
into a grade-specific card's history would mix incompatible price scales.

Run from webapp/: `python3 backfill_historical_prices.py [--days N]`
"""
from __future__ import annotations

import argparse
import asyncio
import sys
import time
from datetime import datetime, timezone

import db
import pricecharting_lookup as pc

SOURCE = "pricecharting_chart_backfill"
REQUEST_DELAY_SECONDS = 1.5


async def backfill(min_days: int) -> None:
    cutoff_ms = time.time() * 1000 - min_days * 86400_000

    with db.connect() as conn:
        cards = conn.execute(
            "SELECT id, name, set_name, card_number, language, variant "
            "FROM cards WHERE is_graded = 0 ORDER BY id"
        ).fetchall()

    total_inserted = 0
    for card in cards:
        with db.connect() as conn:
            existing_dates = {
                row["recorded_at"][:10]
                for row in conn.execute(
                    "SELECT recorded_at FROM price_history WHERE card_id = ?",
                    (card["id"],),
                ).fetchall()
            }

        result = await pc.fetch_chart_history(
            card["name"], card["set_name"], card["card_number"],
            language=card["language"], variant=card["variant"],
        )
        if not result:
            print(f"  card {card['id']:>3} {card['name']!r}: no chart data found")
            await asyncio.sleep(REQUEST_DELAY_SECONDS)
            continue

        points, url = result
        rows = []
        for ts_ms, price in points:
            if ts_ms < cutoff_ms:
                continue
            recorded_at = datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc) \
                .strftime("%Y-%m-%d %H:%M:%S")
            if recorded_at[:10] in existing_dates:
                continue
            rows.append((card["id"], recorded_at, price, SOURCE, url))
            existing_dates.add(recorded_at[:10])

        if rows:
            with db.connect() as conn:
                conn.executemany(
                    "INSERT INTO price_history (card_id, recorded_at, price_usd, source, source_url) "
                    "VALUES (?, ?, ?, ?, ?)",
                    rows,
                )
                conn.commit()
        total_inserted += len(rows)
        print(f"  card {card['id']:>3} {card['name']!r}: +{len(rows)} rows from {url}")
        await asyncio.sleep(REQUEST_DELAY_SECONDS)

    print(f"\nDone — inserted {total_inserted} historical price rows across "
          f"{len(cards)} raw cards (source={SOURCE!r}).")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--days", type=int, default=90,
                        help="minimum days of history to backfill (default 90)")
    args = parser.parse_args()
    print(f"Backfilling >= {args.days} days of price history for raw cards...")
    asyncio.run(backfill(args.days))
    sys.exit(0)
