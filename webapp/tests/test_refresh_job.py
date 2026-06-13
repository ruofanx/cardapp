"""Tests for refresh_job's scheduler setup — confirms both the daily price
refresh and the new weekly price-history refresh jobs are registered."""
from __future__ import annotations

import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

import price_history_refresh
import refresh_job


def test_weekly_price_history_job_registered():
    async def _check():
        scheduler = refresh_job.start_scheduler()
        try:
            job = scheduler.get_job("weekly_price_history_refresh")
            assert job is not None
            assert job.kwargs == {"min_days": 35}
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
