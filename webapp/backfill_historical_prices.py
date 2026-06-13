"""
One-off / manual CLI for `price_history_refresh.refresh_all()`.

Run from webapp/: `python3 backfill_historical_prices.py [--days N]`
See `price_history_refresh.py` for what this does — the same function is
also called weekly by `refresh_job`'s scheduler.
"""
from __future__ import annotations

import argparse
import asyncio
import sys

import price_history_refresh


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--days", type=int, default=90,
                        help="minimum days of history to backfill (default 90)")
    args = parser.parse_args()
    print(f"Backfilling >= {args.days} days of price history for raw cards and sealed products...")
    summary = asyncio.run(price_history_refresh.refresh_all(args.days))
    print(f"\nSummary: {summary}")
    sys.exit(0)
