"""Tests for refresh_job's scheduler setup — confirms both the daily price
refresh and the new weekly price-history refresh jobs are registered."""
from __future__ import annotations

import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

from datetime import datetime
from zoneinfo import ZoneInfo

import price_history_refresh
import refresh_job


def test_weekly_price_history_job_registered():
    async def _check():
        scheduler = refresh_job.start_scheduler()
        try:
            job = scheduler.get_job("weekly_price_history_refresh")
            assert job is not None
            assert job.kwargs == {"min_days": 70}
            assert job.func is price_history_refresh.refresh_all
        finally:
            refresh_job.shutdown_scheduler()

    asyncio.run(_check())


def test_daily_price_refresh_job_still_registered():
    async def _check():
        scheduler = refresh_job.start_scheduler()
        try:
            job = scheduler.get_job("daily_price_refresh")
            assert job is not None
            assert job.func is refresh_job.refresh_all_cards
        finally:
            refresh_job.shutdown_scheduler()

    asyncio.run(_check())


def test_daily_slot_is_today_after_the_hour_and_yesterday_before_it():
    tz = ZoneInfo("America/Chicago")
    after = datetime(2026, 11, 22, 8, 0, tzinfo=tz)
    before = datetime(2026, 11, 22, 6, 30, tzinfo=tz)
    assert refresh_job._last_daily_slot(after, 7).day == 22
    assert refresh_job._last_daily_slot(before, 7).day == 21
