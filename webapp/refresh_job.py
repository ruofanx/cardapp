"""
Daily price refresh job.

At 7:00 AM Central Time, walks every card in every collection and refreshes
its current_market_price by re-running the same logic /api/refresh-price uses
(PriceCharting for graded; raw_price_resolver's catalogue baseline +
PriceCharting sold-comp blend for raw). Updates last_priced_at on success.

Also registers a weekly job (Sunday 6am CT) that refreshes price_history from
PriceCharting's chart data via price_history_refresh.refresh_all().

The scheduler runs in-process via APScheduler — fine for a personal/family
deployment where uvicorn stays up. If the server is down at 7am, it'll just
miss that day; next start triggers `_run_now_if_overdue` so a long-down server
catches up on its first boot of the day.
"""
from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timezone

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

import db
import pricecharting_lookup
import raw_price_resolver
import price_history_refresh

log = logging.getLogger(__name__)

# 7am Central. APScheduler accepts pytz/zoneinfo identifiers.
DAILY_HOUR_LOCAL = 7
DAILY_TIMEZONE = "America/Chicago"

# Per-card delay so we don't hammer Pokemon TCG API / PriceCharting.
PER_CARD_SLEEP_SEC = 1.5


async def refresh_all_cards() -> dict:
    """Iterate every card across every user, update current_market_price.

    Returns a small summary dict that callers can log / surface in /api/health.
    """
    started = time.perf_counter()
    updated = 0
    skipped = 0
    failures = 0

    users = db.list_users()
    log.info("Daily price refresh: %d user(s)", len(users))

    for user in users:
        cards = db.list_cards(user.id)
        for card in cards:
            try:
                price = await _price_for_card(card)
                if price is not None:
                    db.update_market_price(card.id, price)
                    updated += 1
                else:
                    skipped += 1
            except Exception as e:
                log.warning("refresh failed for card %s (%s): %s",
                            card.id, card.name, e)
                failures += 1
            await asyncio.sleep(PER_CARD_SLEEP_SEC)

    elapsed = time.perf_counter() - started
    summary = {
        "updated": updated, "skipped": skipped, "failures": failures,
        "elapsed_sec": round(elapsed, 1),
        "ran_at": datetime.now(timezone.utc).isoformat(),
    }
    log.info("Daily price refresh done: %s", summary)
    return summary


async def _price_for_card(card: db.Card) -> float | None:
    """Same routing as /api/refresh-price, simplified for batch use."""
    # Graded → PriceCharting first
    if card.is_graded and card.grade_company and card.grade is not None:
        try:
            pc = await pricecharting_lookup.lookup_graded_price(
                card.name, card.set_name or "", card.card_number or "",
                card.language, card.grade_company, float(card.grade),
            )
            if pc and pc.price_usd is not None:
                return float(pc.price_usd)
        except Exception as e:
            log.warning("PriceCharting failed for card %s: %s", card.id, e)
        # Fall through to multiplier estimate

    # Baseline + PriceCharting sold-comp blend — same as /api/refresh-price.
    result = await raw_price_resolver.resolve_raw_price(
        card.name, card.set_name or "", card.card_number or "",
        language=card.language, variant=card.variant,
    )
    if result.nm_price is None:
        return None
    nm_price = result.nm_price

    if card.is_graded and card.grade_company and card.grade is not None:
        from app import _pick_multiplier
        mult = _pick_multiplier(
            card.grade_company, float(card.grade),
            card.set_name, card.variant,
        ) or 1.0
        return round(nm_price * mult, 2)

    # Raw — apply condition factor
    from app import RAW_CONDITION_MULTIPLIERS
    mult = RAW_CONDITION_MULTIPLIERS.get((card.condition or "NM").upper(), 1.0)
    return round(nm_price * mult, 2)


# ---------------------------------------------------------------------------
# Scheduler boot
# ---------------------------------------------------------------------------

_scheduler: AsyncIOScheduler | None = None


def start_scheduler():
    """Boot the AsyncIOScheduler. Idempotent — safe to call multiple times."""
    global _scheduler
    if _scheduler is not None:
        return _scheduler
    _scheduler = AsyncIOScheduler()
    _scheduler.add_job(
        refresh_all_cards,
        CronTrigger(hour=DAILY_HOUR_LOCAL, minute=0, timezone=DAILY_TIMEZONE),
        id="daily_price_refresh",
        name="Daily price refresh (7am CT)",
        replace_existing=True,
        misfire_grace_time=60 * 60,   # 1 hour grace after server downtime
    )
    _scheduler.add_job(
        price_history_refresh.refresh_all,
        CronTrigger(day_of_week="sun", hour=6, minute=0, timezone=DAILY_TIMEZONE),
        kwargs={"min_days": 35},
        id="weekly_price_history_refresh",
        name="Weekly price-history refresh (Sun 6am CT)",
        replace_existing=True,
        misfire_grace_time=6 * 60 * 60,   # 6 hour grace
    )
    _scheduler.start()
    log.info("Scheduler started: daily price refresh @ %02d:00 %s, "
             "weekly price-history refresh Sun 06:00 %s",
             DAILY_HOUR_LOCAL, DAILY_TIMEZONE, DAILY_TIMEZONE)
    return _scheduler


def shutdown_scheduler():
    global _scheduler
    if _scheduler is not None:
        _scheduler.shutdown(wait=False)
        _scheduler = None
