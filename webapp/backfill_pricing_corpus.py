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

from pricing_model import corpus, db as pmdb, model as pm

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
    per_set = (summary or {}).get("per_set") or {}
    if per_set and any(n == 0 for n in per_set.values()):
        print("Harvest incomplete (a set returned 0 cards); not fitting a model.", file=sys.stderr)
        sys.exit(1)
    cards = pmdb.get_all_corpus_cards()
    history = pmdb.get_all_corpus_history()
    run = pm.fit_model(cards, history)
    run_id = pmdb.save_model_run(run)
    print(f"Fitted model run {run_id} (R^2={run.r_squared_raw:.3f}, n={run.n_cards})")
    sys.exit(0)
