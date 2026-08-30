"""One-off / manual CLI for pricing_model.corpus.harvest_all().

Run from webapp/: `python3 backfill_pricing_corpus.py [--sets sv8,sv7,...]`
Populates pricing_model.sqlite's corpus_cards/corpus_history tables from
Pokemon TCG API + PriceCharting. Safe to re-run (upserts).
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import sys

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s — %(message)s")

from pricing_model import corpus, db as pmdb

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--sets", type=str, default=None,
                        help="comma-separated Pokemon TCG API set ids (default: corpus.TRAINING_SETS)")
    args = parser.parse_args()
    pmdb.init_db()
    set_ids = args.sets.split(",") if args.sets else None
    print("Harvesting training corpus…")
    summary = asyncio.run(corpus.harvest_all(set_ids))
    print(f"\nSummary: {summary}")
    sys.exit(0)
