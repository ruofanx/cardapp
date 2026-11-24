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
    pmdb.init_db()  # idempotent -- guards a fresh/empty DB file on first run
    try:
        harvest_summary = await corpus.harvest_all()
        per_set = (harvest_summary or {}).get("per_set") or {}
        # A set that returned 0 cards is a failed fetch, not an empty set.
        # Fitting that corpus can keep a similar R² and still ship bad prices.
        if per_set and any(n == 0 for n in per_set.values()):
            log.warning("pricing_model monthly refit: incomplete harvest %s; keeping previous run",
                        per_set)
            return {"kept_previous": True, "incomplete": True, "harvest": harvest_summary}

        cards = pmdb.get_all_corpus_cards()
        history = pmdb.get_all_corpus_history()
        new_run = pm.fit_model(cards, history)
    except Exception as e:
        log.exception("pricing_model monthly refit failed during harvest/fit")
        return {"kept_previous": True, "error": str(e)}

    previous = pmdb.get_latest_model_run()
    if previous is not None and new_run.r_squared_raw < previous.r_squared_raw + MIN_R_SQUARED_IMPROVEMENT:
        log.warning(
            "pricing_model monthly refit: new fit R^2=%.3f worse than previous %.3f, keeping previous run",
            new_run.r_squared_raw, previous.r_squared_raw,
        )
        _mark_refit_ran()
        return {"kept_previous": True, "harvest": harvest_summary,
                "new_r_squared": new_run.r_squared_raw, "previous_r_squared": previous.r_squared_raw}

    run_id = pmdb.save_model_run(new_run)
    log.info("pricing_model monthly refit: saved new model run %s (R^2=%.3f, n=%d)",
              run_id, new_run.r_squared_raw, new_run.n_cards)
    _mark_refit_ran()
    return {"kept_previous": False, "harvest": harvest_summary,
            "new_r_squared": new_run.r_squared_raw, "run_id": run_id}


def _mark_refit_ran() -> None:
    try:
        from refresh_job import mark_job_ran
        mark_job_ran("monthly_pricing_model_refit")
    except Exception as e:
        log.warning("could not record monthly refit run: %s", e)
