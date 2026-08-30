# webapp/pricing_model/jobs.py
"""Monthly corpus refresh + model refit, wired into refresh_job.py's
scheduler. A refit that degrades fit quality vs. the previous run keeps the
previous model_runs row active instead of shipping worse predictions.
"""
from __future__ import annotations

import logging

from pricing_model import corpus, db as pmdb, model as pm

log = logging.getLogger(__name__)

# A refit must beat the previous run's R^2 by at least this much to replace
# it — guards against a bad/partial harvest silently degrading predictions.
MIN_R_SQUARED_IMPROVEMENT = -0.05  # allow small noise-driven dips, reject real regressions


async def monthly_refit() -> dict:
    harvest_summary = await corpus.harvest_all()

    cards = pmdb.get_all_corpus_cards()
    history = pmdb.get_all_corpus_history()
    new_run = pm.fit_model(cards, history)

    previous = pmdb.get_latest_model_run()
    if previous is not None and new_run.r_squared_raw < previous.r_squared_raw + MIN_R_SQUARED_IMPROVEMENT:
        log.warning(
            "pricing_model monthly refit: new fit R^2=%.3f worse than previous %.3f, keeping previous run",
            new_run.r_squared_raw, previous.r_squared_raw,
        )
        return {"kept_previous": True, "harvest": harvest_summary,
                "new_r_squared": new_run.r_squared_raw, "previous_r_squared": previous.r_squared_raw}

    run_id = pmdb.save_model_run(new_run)
    log.info("pricing_model monthly refit: saved new model run %s (R^2=%.3f, n=%d)",
              run_id, new_run.r_squared_raw, new_run.n_cards)
    return {"kept_previous": False, "harvest": harvest_summary,
            "new_r_squared": new_run.r_squared_raw, "run_id": run_id}
