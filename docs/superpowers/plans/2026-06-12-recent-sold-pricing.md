# Recent-Sold & Market-Trend Pricing Improvements Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make raw-card prices lean on PriceCharting's sold-comp-derived "Ungraded" price for ALL raw cards (not just JP/old-print), share that logic between `/api/refresh-price` and the daily refresh job, refresh `price_history` from PriceCharting's chart data weekly, and add an opt-in ScraperAPI transport for eBay sold-listing fetches.

**Architecture:** A new shared module `webapp/raw_price_resolver.py` extracts today's catalogue-baseline cascade and blends it with PriceCharting's "Ungraded" price using a 15% divergence rule; both `/api/refresh-price` (`webapp/app.py`) and the daily batch job (`webapp/refresh_job.py`) call it. A second new module `webapp/price_history_refresh.py` (refactored from `backfill_historical_prices.py`) is called both by the existing manual-backfill CLI and a new weekly APScheduler job. Finally, `webapp/ebay_lookup.py` gains an optional ScraperAPI-routed HTML fetch, scoped to on-demand/cached eBay calls only.

**Tech Stack:** Python 3, FastAPI, httpx, APScheduler (AsyncIOScheduler + CronTrigger), SQLite (`sqlite3`), pytest (`asyncio.run()`-style async tests, `monkeypatch`).

---

## File Structure

| File | Responsibility |
|------|-----------------|
| `webapp/raw_price_resolver.py` (new) | `RawPriceResult`, `_baseline_price()`, `resolve_raw_price()` — shared baseline + PriceCharting blend |
| `webapp/tests/test_raw_price_resolver.py` (new) | Blend-logic unit tests |
| `webapp/app.py` | `/api/refresh-price` raw/graded-fallback baseline block replaced with `resolve_raw_price()`; remove dead `_OLD_PRINT_VARIANTS`/`_is_old_variant` |
| `webapp/refresh_job.py` | `_price_for_card` raw branch uses `resolve_raw_price()`; new weekly `price_history_refresh` scheduler job |
| `webapp/price_history_refresh.py` (new) | `refresh_one()` / `refresh_all()` — refactored from `backfill_historical_prices.py` |
| `webapp/tests/test_price_history_refresh.py` (new) | Dedup/cutoff + summary-dict tests |
| `webapp/backfill_historical_prices.py` | Becomes a thin CLI wrapper over `price_history_refresh.refresh_all()` |
| `webapp/tests/test_refresh_job.py` (new) | Confirms the weekly scheduler job is registered with the right trigger/kwargs |
| `webapp/ebay_lookup.py` | New `SCRAPERAPI_KEY` + `_fetch_ebay_html()` helper; wired into `lookup_sold_listings` and `lookup_raw_price` |
| `webapp/tests/test_ebay_lookup.py` (new) | `_fetch_ebay_html` URL-construction tests (direct vs ScraperAPI) |
| `CLAUDE.md` | Document `SCRAPERAPI_KEY`, new modules, new weekly job, updated raw-price cascade |

---

### Task 1: Shared raw-price resolver

**Files:**
- Create: `webapp/raw_price_resolver.py`
- Test: `webapp/tests/test_raw_price_resolver.py`

- [ ] **Step 1: Write the failing tests**

```python
"""Tests for raw_price_resolver.resolve_raw_price's baseline + PriceCharting blend."""
from __future__ import annotations

import asyncio
import os
import sys
from types import SimpleNamespace

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

import raw_price_resolver as rpr


def test_only_baseline_keeps_tcgplayer_price(monkeypatch):
    async def fake_lookup_card(name, set_name, card_number, language="english", variant=None):
        return SimpleNamespace(market_price=50.0, source="tcgplayer")

    async def fake_pc_raw(*args, **kwargs):
        return None

    monkeypatch.setattr(rpr.card_lookup, "lookup_card", fake_lookup_card)
    monkeypatch.setattr(rpr.pricecharting_lookup, "lookup_raw_price", fake_pc_raw)

    result = asyncio.run(rpr.resolve_raw_price(
        "Pikachu", "Base Set", "58/102", language="english",
    ))

    assert result.nm_price == 50.0
    assert result.baseline_label == "TCGplayer (EN)"
    assert result.extra_note is None


def test_only_pricecharting_when_no_baseline(monkeypatch):
    async def fake_lookup_card(name, set_name, card_number, language="english", variant=None):
        return None

    async def fake_browse(*args, **kwargs):
        return None

    async def fake_ebay_raw(*args, **kwargs):
        return None

    async def fake_pc_raw(name, set_name, card_number, language="english", variant=None):
        return SimpleNamespace(price_usd=30.0)

    monkeypatch.setattr(rpr.card_lookup, "lookup_card", fake_lookup_card)
    monkeypatch.setattr(rpr.ebay_browse_api, "median_relevant_price", fake_browse)
    monkeypatch.setattr(rpr.ebay_lookup, "lookup_raw_price", fake_ebay_raw)
    monkeypatch.setattr(rpr.pricecharting_lookup, "lookup_raw_price", fake_pc_raw)

    result = asyncio.run(rpr.resolve_raw_price(
        "Lugia", "Neo Genesis", "9/111", language="japanese",
    ))

    assert result.nm_price == 30.0
    assert result.baseline_label == "PriceCharting Ungraded (sold-based)"
    assert result.extra_note is None


def test_agree_within_threshold_keeps_baseline(monkeypatch):
    async def fake_lookup_card(name, set_name, card_number, language="english", variant=None):
        return SimpleNamespace(market_price=100.0, source="tcgplayer")

    async def fake_pc_raw(name, set_name, card_number, language="english", variant=None):
        return SimpleNamespace(price_usd=108.0)

    monkeypatch.setattr(rpr.card_lookup, "lookup_card", fake_lookup_card)
    monkeypatch.setattr(rpr.pricecharting_lookup, "lookup_raw_price", fake_pc_raw)

    result = asyncio.run(rpr.resolve_raw_price(
        "Charizard", "Base Set", "4/102", language="english",
    ))

    assert result.nm_price == 100.0
    assert result.baseline_label == "TCGplayer (EN)"
    assert result.extra_note is None


def test_divergence_beyond_threshold_switches_to_pricecharting(monkeypatch):
    async def fake_lookup_card(name, set_name, card_number, language="english", variant=None):
        return SimpleNamespace(market_price=100.0, source="tcgplayer")

    async def fake_pc_raw(name, set_name, card_number, language="english", variant=None):
        return SimpleNamespace(price_usd=150.0)

    monkeypatch.setattr(rpr.card_lookup, "lookup_card", fake_lookup_card)
    monkeypatch.setattr(rpr.pricecharting_lookup, "lookup_raw_price", fake_pc_raw)

    result = asyncio.run(rpr.resolve_raw_price(
        "Charizard", "Base Set", "4/102", language="english",
    ))

    assert result.nm_price == 150.0
    assert result.baseline_label == "PriceCharting Ungraded (sold-based)"
    assert "TCGplayer (EN) was $100.00" in result.extra_note
    assert "diverged 50%" in result.extra_note
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd ~/claude/CardApp/webapp && source .venv/bin/activate && pytest tests/test_raw_price_resolver.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'raw_price_resolver'`

- [ ] **Step 3: Write the implementation**

```python
"""
Shared raw-card "best available, sold-aware" price resolver.

Used by both /api/refresh-price (on-demand) and the daily refresh job, so the
blend between catalogue baselines (TCGplayer/Cardmarket/eBay Browse) and
PriceCharting's sold-comp-derived "Ungraded" price only needs to be
implemented and tested once.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional

import card_lookup
import ebay_browse_api
import ebay_lookup
import pricecharting_lookup

log = logging.getLogger(__name__)

# If PriceCharting's sold-comp-derived "Ungraded" price diverges from the
# catalogue baseline by more than this fraction, prefer PriceCharting — it's
# a closer "what is this actually selling for" signal than a catalogue index.
RAW_PRICE_DIVERGENCE_THRESHOLD = 0.15


@dataclass
class RawPriceResult:
    nm_price: Optional[float]
    baseline_label: str
    extra_note: Optional[str]


async def _baseline_price(
    name: str, set_name: str, card_number: str,
    language: str, variant: Optional[str],
) -> RawPriceResult:
    """Today's catalogue-based cascade, extracted as-is from /api/refresh-price."""
    nm_price: Optional[float] = None
    baseline_label = "TCGplayer (EN)"
    extra_note: Optional[str] = None

    # PRIMARY for JP cards: eBay Browse API median of relevant active
    # listings. Cardmarket EUR data is stale for newer JP sets.
    if name and language.lower() == "japanese":
        try:
            br = await ebay_browse_api.median_relevant_price(
                name, set_name, card_number, language="japanese",
            )
        except Exception as e:
            log.warning("eBay Browse median lookup failed: %s", e)
            br = None
        if br and br["median_usd"]:
            nm_price = float(br["median_usd"])
            baseline_label = (
                f"eBay Browse median (JP, n={br['sample_size']} of "
                f"{br['raw_sample_size']}, range "
                f"${br['low_usd']:.2f}-${br['high_usd']:.2f})"
            )
            extra_note = (
                f"Trimmed-median of {br['sample_size']} relevant active "
                f"listings from eBay (query: {br['query']!r}). Cardmarket "
                f"EUR is often stale for newer JP sets."
            )

    if nm_price is None:
        base = await card_lookup.lookup_card(
            name, set_name, card_number, language=language, variant=variant,
        )
        if base and base.market_price:
            nm_price = float(base.market_price)
            variant_tag = f" / {variant}" if variant else ""
            baseline_label = (
                "Cardmarket EUR (JP)" if base.source == "cardmarket-jp"
                else f"TCGplayer (EN{variant_tag})"
            )

    # Last-ditch second opinion for JP — eBay sold listings.
    if nm_price is None and language.lower() == "japanese":
        try:
            ebay = await ebay_lookup.lookup_raw_price(
                name, set_name, card_number, language="japanese", condition="NM",
            )
        except Exception as e:
            log.warning("eBay lookup failed for JP card: %s", e)
            ebay = None
        if ebay and ebay.median_usd:
            nm_price = float(ebay.median_usd)
            baseline_label = "eBay sold (JP-keyword)"
            extra_note = (f"eBay sold-median n={ebay.sample_size}/{ebay.raw_sample_size}, "
                          f"{ebay.period_days}d window")

    return RawPriceResult(nm_price=nm_price, baseline_label=baseline_label, extra_note=extra_note)


async def resolve_raw_price(
    name: str, set_name: str, card_number: str,
    language: str = "english", variant: Optional[str] = None,
) -> RawPriceResult:
    """Best-available NM price for a raw card, blending the catalogue
    baseline with PriceCharting's sold-comp-derived "Ungraded" price.

    PriceCharting's Ungraded price is itself derived from aggregated recent
    sold comps, so when it diverges meaningfully from the catalogue baseline
    it's treated as the more accurate "recent sold" signal.
    """
    baseline = await _baseline_price(name, set_name, card_number, language, variant)

    try:
        pc_raw = await pricecharting_lookup.lookup_raw_price(
            name, set_name, card_number, language=language, variant=variant,
        )
    except Exception as e:
        log.warning("PriceCharting raw lookup failed: %s", e)
        pc_raw = None
    pc_price = float(pc_raw.price_usd) if pc_raw and pc_raw.price_usd else None

    if pc_price is None:
        return baseline

    if baseline.nm_price is None:
        return RawPriceResult(
            nm_price=pc_price,
            baseline_label="PriceCharting Ungraded (sold-based)",
            extra_note=None,
        )

    divergence = abs(pc_price - baseline.nm_price) / baseline.nm_price
    if divergence <= RAW_PRICE_DIVERGENCE_THRESHOLD:
        return baseline

    return RawPriceResult(
        nm_price=pc_price,
        baseline_label="PriceCharting Ungraded (sold-based)",
        extra_note=(
            f"{baseline.baseline_label} was ${baseline.nm_price:.2f} "
            f"(diverged {divergence:.0%}) — using PriceCharting sold-based price."
        ),
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd ~/claude/CardApp/webapp && source .venv/bin/activate && pytest tests/test_raw_price_resolver.py -v`
Expected: PASS — 4 passed

- [ ] **Step 5: Commit**

```bash
cd ~/claude/CardApp
git add webapp/raw_price_resolver.py webapp/tests/test_raw_price_resolver.py
git commit -m "feat: add shared raw-price resolver blending catalogue baseline with PriceCharting sold-comps"
```

---

### Task 2: Wire `resolve_raw_price()` into `/api/refresh-price`

**Files:**
- Modify: `webapp/app.py`

- [ ] **Step 1: Add the import**

In `webapp/app.py`, add `raw_price_resolver` to the module imports:

```python
import db
from trade_proposer import propose_trades
import card_lookup
import pricecharting_lookup
import ebay_lookup
import raw_price_resolver
```

(Replace the existing 5-line import block at the top of the file — `import db` through `import ebay_lookup` — with this 6-line block.)

- [ ] **Step 2: Remove the now-dead `_OLD_PRINT_VARIANTS` / `_is_old_variant`**

These were only used to gate the old "PriceCharting Ungraded for JP/old-print only" check, which Step 4 below removes (PriceCharting is now consulted for ALL raw cards inside `resolve_raw_price`). Delete this block (including its explanatory comment):

```python


# Variants where TCGplayer's Pokemon TCG API doesn't reliably distinguish the
# print, so we benefit from PriceCharting's separate-page coverage.
#
# Deliberately NOT in this list:
#   - "1st edition" / "first edition" — Pokemon TCG API has the dedicated
#     `1stEditionHolofoil` / `1stEdition` price keys per card. _extract_market_price
#     selects the right key based on variant. PC is less accurate here.
#   - "unlimited" — same reason: TCGplayer carries `unlimitedHolofoil` and
#     `holofoil` directly. Routing through PC drops us onto a generic
#     "Ungraded" row that conflates conditions and prints.
#   - Modern rarity descriptors (Holo, Rainbow, Alt Art, Illustration Rare) —
#     these are different card numbers, not variants, on TCGplayer.
_OLD_PRINT_VARIANTS = {"shadowless"}


def _is_old_variant(variant: Optional[str]) -> bool:
    return bool(variant) and variant.strip().lower() in _OLD_PRINT_VARIANTS
```

This sits between `RAW_CONDITION_MULTIPLIERS = {...}` and the `GRADED_MULTIPLIERS` comment block — delete the whole block above, leaving the two surrounding sections with a normal blank-line gap.

- [ ] **Step 3: Update the `/api/refresh-price` docstring**

Replace:

```python
    Fallback order if eBay returns nothing (sandbox 403, anti-bot block, or
    genuinely thin comps):
      - Graded → PriceCharting per-grade, then NM × grader multiplier
      - Raw    → PriceCharting Ungraded (for JP and old prints) → TCGplayer
        market price → eBay 30/60-day trimmed median (legacy)
    """
```

with:

```python
    Fallback order if eBay returns nothing (sandbox 403, anti-bot block, or
    genuinely thin comps):
      - Graded → PriceCharting per-grade, then NM × grader multiplier
      - Raw    → raw_price_resolver.resolve_raw_price() — catalogue baseline
        (TCGplayer / Cardmarket / eBay Browse) blended with PriceCharting's
        sold-comp-derived "Ungraded" price
    """
```

- [ ] **Step 4: Replace the baseline-computation block with `resolve_raw_price()`**

Replace this entire block (everything from the `# ----- baseline lookup for raw or graded-fallback -----` comment through the `raise HTTPException(404, ...)` line — 85 lines):

```python
    # ----- baseline lookup for raw or graded-fallback ----------------------
    nm_price: Optional[float] = None
    baseline_label = "TCGplayer (EN)"
    extra_note = None

    # PRIMARY for JP cards: eBay Browse API median of relevant active
    # listings. Cardmarket EUR data is stale for newer JP sets (Crimson
    # Haze, Battle Partners, Glory of Team Rocket, etc.) — often off by
    # 5-10×. Trim-medianing 5-10 live eBay listings gives a real number.
    if (req.name and req.language.lower() == "japanese"
            and not req.is_graded):
        try:
            import ebay_browse_api
            br = await ebay_browse_api.median_relevant_price(
                req.name, req.set_name, req.card_number,
                language="japanese",
            )
        except Exception as e:
            log.warning("eBay Browse median lookup failed: %s", e)
            br = None
        if br and br["median_usd"]:
            nm_price = float(br["median_usd"])
            baseline_label = (
                f"eBay Browse median (JP, n={br['sample_size']} of "
                f"{br['raw_sample_size']}, range "
                f"${br['low_usd']:.2f}-${br['high_usd']:.2f})"
            )
            extra_note = (
                f"Trimmed-median of {br['sample_size']} relevant active "
                f"listings from eBay (query: {br['query']!r}). Cardmarket "
                f"EUR is often stale for newer JP sets."
            )

    # PriceCharting "Ungraded" — best for OLD cards where 1st Edition is its
    # own product (different page slug), and for JP cards where Cardmarket
    # is consistently stale. Also try for any card with variant context.
    if nm_price is None and req.name and (
            req.language.lower() == "japanese" or _is_old_variant(req.variant)):
        try:
            pc_raw = await pricecharting_lookup.lookup_raw_price(
                req.name, req.set_name or "", req.card_number or "",
                language=req.language, variant=req.variant,
            )
        except Exception as e:
            log.warning("PriceCharting raw lookup failed: %s", e)
            pc_raw = None
        if pc_raw and pc_raw.price_usd:
            nm_price = float(pc_raw.price_usd)
            baseline_label = (
                f"PriceCharting Ungraded "
                f"({'JP' if req.language.lower() == 'japanese' else req.variant or 'EN'})"
            )

    if nm_price is None:
        base = await card_lookup.lookup_card(
            req.name, req.set_name, req.card_number, language=req.language,
            variant=req.variant,
        )
        if base and base.market_price:
            nm_price = float(base.market_price)
            variant_tag = f" / {req.variant}" if req.variant else ""
            baseline_label = (
                "Cardmarket EUR (JP)" if base.source == "cardmarket-jp"
                else f"TCGplayer (EN{variant_tag})"
            )

    # Last-ditch second opinion for JP — eBay sold listings. May 403 from
    # certain network egress points; that's why it's last and best-effort.
    if nm_price is None and req.language.lower() == "japanese":
        try:
            ebay = await ebay_lookup.lookup_raw_price(
                req.name, req.set_name or "", req.card_number or "",
                language="japanese", condition="NM",
            )
        except Exception as e:
            log.warning("eBay lookup failed for JP card: %s", e)
            ebay = None
        if ebay and ebay.median_usd:
            nm_price = float(ebay.median_usd)
            baseline_label = "eBay sold (JP-keyword)"
            extra_note = (f"eBay sold-median n={ebay.sample_size}/{ebay.raw_sample_size}, "
                          f"{ebay.period_days}d window")

    if nm_price is None:
        raise HTTPException(404, "no baseline market price found in any catalogue")
```

with:

```python
    # ----- baseline + PriceCharting sold-comp blend (raw or graded-fallback) -
    result = await raw_price_resolver.resolve_raw_price(
        req.name, req.set_name or "", req.card_number or "",
        language=req.language, variant=req.variant,
    )
    nm_price = result.nm_price
    baseline_label = result.baseline_label
    extra_note = result.extra_note

    if nm_price is None:
        raise HTTPException(404, "no baseline market price found in any catalogue")
```

- [ ] **Step 5: Run the full test suite to confirm no regressions**

Run: `cd ~/claude/CardApp/webapp && source .venv/bin/activate && pytest -v`
Expected: PASS — all tests pass (same count as before this task, plus Task 1's 4 new tests)

- [ ] **Step 6: Commit**

```bash
cd ~/claude/CardApp
git add webapp/app.py
git commit -m "refactor: wire resolve_raw_price into /api/refresh-price, drop dead JP/old-print gate"
```

---

### Task 3: Align the daily refresh job with `resolve_raw_price()`

**Files:**
- Modify: `webapp/refresh_job.py`

- [ ] **Step 1: Update imports**

Replace:

```python
import db
import card_lookup
import pricecharting_lookup
```

with:

```python
import db
import pricecharting_lookup
import raw_price_resolver
```

(`card_lookup` becomes a dead import once Step 2 removes its only call site.)

- [ ] **Step 2: Rewrite `_price_for_card`'s baseline lookup**

Replace:

```python
    # Baseline lookup — variant matters for old cards (Unlimited vs 1st Ed)
    base = await card_lookup.lookup_card(
        card.name, card.set_name, card.card_number, language=card.language,
        variant=card.variant,
    )
    if not base or not base.market_price:
        return None
    nm_price = float(base.market_price)
```

with:

```python
    # Baseline + PriceCharting sold-comp blend — same as /api/refresh-price.
    result = await raw_price_resolver.resolve_raw_price(
        card.name, card.set_name or "", card.card_number or "",
        language=card.language, variant=card.variant,
    )
    if result.nm_price is None:
        return None
    nm_price = result.nm_price
```

The rest of `_price_for_card` (grade/condition multiplier application) is unchanged.

- [ ] **Step 3: Run the full test suite to confirm no regressions**

Run: `cd ~/claude/CardApp/webapp && source .venv/bin/activate && pytest -v`
Expected: PASS — all tests pass

- [ ] **Step 4: Commit**

```bash
cd ~/claude/CardApp
git add webapp/refresh_job.py
git commit -m "refactor: align daily refresh job's raw-price lookup with resolve_raw_price"
```

---

### Task 4: Refactor `backfill_historical_prices.py` into `price_history_refresh.py`

**Files:**
- Create: `webapp/price_history_refresh.py`
- Test: `webapp/tests/test_price_history_refresh.py`
- Modify: `webapp/backfill_historical_prices.py`

- [ ] **Step 1: Write the failing tests**

```python
"""Tests for price_history_refresh: dedup-by-date, cutoff filtering, and the
summary dict returned by refresh_all()."""
from __future__ import annotations

import asyncio
import os
import sys
import time

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

import db as _db
import price_history_refresh as phr


@pytest.fixture
def test_db(tmp_path, monkeypatch):
    db_path = tmp_path / "test.sqlite"
    monkeypatch.setattr(_db, 'DB_PATH', db_path)
    _db.init_db()
    return db_path


def _ms(days_ago: float) -> float:
    return (time.time() - days_ago * 86400) * 1000


def _make_card(**overrides):
    users = _db.list_users()
    fields = dict(
        id=None, user_id=users[0].id, name="Pikachu", set_name="Base Set",
        card_number="58/102", language="english",
    )
    fields.update(overrides)
    return _db.create_card(_db.Card(**fields))


def test_refresh_one_dedups_by_date_and_applies_cutoff(test_db):
    card = _make_card()

    existing_date = time.strftime("%Y-%m-%d", time.gmtime(_ms(30) / 1000))
    with _db.connect() as conn:
        conn.execute(
            "INSERT INTO price_history (card_id, recorded_at, price_usd, source) "
            "VALUES (?, ?, ?, ?)",
            (card.id, f"{existing_date} 00:00:00", 99.0, "manual"),
        )
        conn.commit()

    points = [
        (_ms(30), 100.0),   # same date as existing row -> skipped
        (_ms(15), 120.0),   # new -> inserted
        (_ms(200), 80.0),   # older than cutoff -> skipped
    ]

    async def fake_fetch():
        return points, "https://www.pricecharting.com/game/pokemon-base-set/pikachu-58"

    cutoff_ms = time.time() * 1000 - 90 * 86400_000
    inserted = asyncio.run(phr.refresh_one(card.id, card.name, cutoff_ms, fake_fetch))
    assert inserted == 1

    with _db.connect() as conn:
        rows = conn.execute(
            "SELECT price_usd FROM price_history WHERE card_id = ? ORDER BY recorded_at",
            (card.id,),
        ).fetchall()
    prices = sorted(r["price_usd"] for r in rows)
    assert prices == [99.0, 120.0]


def test_refresh_one_no_chart_data_inserts_nothing(test_db):
    card = _make_card(name="Mewtwo")

    async def fake_fetch():
        return None

    cutoff_ms = time.time() * 1000 - 90 * 86400_000
    assert asyncio.run(phr.refresh_one(card.id, card.name, cutoff_ms, fake_fetch)) == 0

    with _db.connect() as conn:
        rows = conn.execute(
            "SELECT * FROM price_history WHERE card_id = ?", (card.id,),
        ).fetchall()
    assert rows == []


def test_refresh_all_covers_raw_cards_and_sealed_products_and_skips_graded(test_db, monkeypatch):
    monkeypatch.setattr(phr, "REQUEST_DELAY_SECONDS", 0)

    raw_card = _make_card(name="Pikachu", product_type="card")
    sealed = _make_card(name="SV 151 Booster Box", set_name="SV 151",
                         card_number=None, product_type="booster_box")
    _make_card(name="Charizard", card_number="4/102",
               is_graded=True, grade_company="PSA", grade=10)

    async def fake_chart_history(name, set_name, card_number, language="english", variant=None):
        return [(time.time() * 1000, 50.0)], "https://pricecharting.com/raw"

    async def fake_sealed_chart_history(name, set_name, product_type, language="english"):
        return [(time.time() * 1000, 110.0)], "https://pricecharting.com/sealed"

    monkeypatch.setattr(phr.pc, "fetch_chart_history", fake_chart_history)
    monkeypatch.setattr(phr.pc, "fetch_sealed_chart_history", fake_sealed_chart_history)

    summary = asyncio.run(phr.refresh_all(min_days=90))

    assert summary == {"raw_cards": 1, "sealed_products": 1, "inserted": 2}

    with _db.connect() as conn:
        raw_price = conn.execute(
            "SELECT price_usd FROM price_history WHERE card_id = ?", (raw_card.id,),
        ).fetchone()
        sealed_price = conn.execute(
            "SELECT price_usd FROM price_history WHERE card_id = ?", (sealed.id,),
        ).fetchone()
    assert raw_price["price_usd"] == 50.0
    assert sealed_price["price_usd"] == 110.0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd ~/claude/CardApp/webapp && source .venv/bin/activate && pytest tests/test_price_history_refresh.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'price_history_refresh'`

- [ ] **Step 3: Write `price_history_refresh.py`**

This is `backfill_historical_prices.py`'s `_backfill_one`/`backfill` renamed to `refresh_one`/`refresh_all`, with `refresh_all` returning a summary dict instead of `None`. The `print()` progress lines are kept as-is so manual CLI runs are unchanged.

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd ~/claude/CardApp/webapp && source .venv/bin/activate && pytest tests/test_price_history_refresh.py -v`
Expected: PASS — 3 passed

- [ ] **Step 5: Turn `backfill_historical_prices.py` into a thin CLI wrapper**

Replace the entire file contents with:

```python
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
```

- [ ] **Step 6: Run the full test suite to confirm no regressions**

Run: `cd ~/claude/CardApp/webapp && source .venv/bin/activate && pytest -v`
Expected: PASS — all tests pass

- [ ] **Step 7: Commit**

```bash
cd ~/claude/CardApp
git add webapp/price_history_refresh.py webapp/tests/test_price_history_refresh.py webapp/backfill_historical_prices.py
git commit -m "refactor: extract price_history_refresh module from backfill_historical_prices CLI"
```

---

### Task 5: Add weekly trend-history refresh to the scheduler

**Files:**
- Modify: `webapp/refresh_job.py`
- Test: `webapp/tests/test_refresh_job.py` (new)

- [ ] **Step 1: Write the failing test**

```python
"""Tests for refresh_job's scheduler setup — confirms both the daily price
refresh and the new weekly price-history refresh jobs are registered."""
from __future__ import annotations

import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd ~/claude/CardApp/webapp && source .venv/bin/activate && pytest tests/test_refresh_job.py -v`
Expected: FAIL — `test_weekly_price_history_job_registered` fails with `AssertionError: assert None is not None` (job not registered yet)

- [ ] **Step 3: Register the weekly job**

Add the import (alongside the existing imports near the top of `webapp/refresh_job.py`):

```python
import db
import pricecharting_lookup
import raw_price_resolver
import price_history_refresh
```

In `start_scheduler()`, add a second `add_job` call before `_scheduler.start()`:

```python
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
```

(`min_days=35` is enough to pick up the latest monthly chart_data point while `refresh_one`'s date-based dedup prevents duplicate rows on overlap. Runs before the 7am daily price refresh.)

- [ ] **Step 4: Run test to verify it passes**

Run: `cd ~/claude/CardApp/webapp && source .venv/bin/activate && pytest tests/test_refresh_job.py -v`
Expected: PASS — 2 passed

- [ ] **Step 5: Run the full test suite to confirm no regressions**

Run: `cd ~/claude/CardApp/webapp && source .venv/bin/activate && pytest -v`
Expected: PASS — all tests pass

- [ ] **Step 6: Commit**

```bash
cd ~/claude/CardApp
git add webapp/refresh_job.py webapp/tests/test_refresh_job.py
git commit -m "feat: add weekly price-history refresh job to the scheduler"
```

---

### Task 6: Opt-in ScraperAPI transport for eBay sold-listing fetches

**Files:**
- Modify: `webapp/ebay_lookup.py`
- Test: `webapp/tests/test_ebay_lookup.py` (new)

- [ ] **Step 1: Write the failing tests**

```python
"""Tests for ebay_lookup._fetch_ebay_html — direct vs ScraperAPI-routed fetch."""
from __future__ import annotations

import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

import ebay_lookup


class FakeResponse:
    def __init__(self, status_code=200, text="<html>ok</html>"):
        self.status_code = status_code
        self.text = text


class FakeAsyncClient:
    """Captures the constructor headers and the URL passed to .get()."""
    last_headers = None
    last_url = None
    response = FakeResponse()

    def __init__(self, *, timeout=None, headers=None):
        FakeAsyncClient.last_headers = headers

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def get(self, url, follow_redirects=True):
        FakeAsyncClient.last_url = url
        return FakeAsyncClient.response


def test_fetch_direct_when_no_scraperapi_key(monkeypatch):
    monkeypatch.setattr(ebay_lookup, "SCRAPERAPI_KEY", None)
    monkeypatch.setattr(ebay_lookup.httpx, "AsyncClient", FakeAsyncClient)
    FakeAsyncClient.response = FakeResponse(text="<html>direct</html>")

    url = "https://www.ebay.com/sch/i.html?_nkw=Pikachu"
    html = asyncio.run(ebay_lookup._fetch_ebay_html(url))

    assert html == "<html>direct</html>"
    assert FakeAsyncClient.last_url == url
    assert FakeAsyncClient.last_headers == {"User-Agent": ebay_lookup.USER_AGENT}


def test_fetch_routes_through_scraperapi_when_key_set(monkeypatch):
    monkeypatch.setattr(ebay_lookup, "SCRAPERAPI_KEY", "TESTKEY123")
    monkeypatch.setattr(ebay_lookup.httpx, "AsyncClient", FakeAsyncClient)
    FakeAsyncClient.response = FakeResponse(text="<html>via-scraperapi</html>")

    url = "https://www.ebay.com/sch/i.html?_nkw=Pikachu&LH_Sold=1"
    html = asyncio.run(ebay_lookup._fetch_ebay_html(url))

    assert html == "<html>via-scraperapi</html>"
    assert FakeAsyncClient.last_url == (
        "http://api.scraperapi.com/?api_key=TESTKEY123&"
        "url=https%3A%2F%2Fwww.ebay.com%2Fsch%2Fi.html%3F_nkw%3DPikachu%26LH_Sold%3D1"
    )
    assert FakeAsyncClient.last_headers == {}


def test_fetch_returns_none_on_non_200(monkeypatch):
    monkeypatch.setattr(ebay_lookup, "SCRAPERAPI_KEY", None)
    monkeypatch.setattr(ebay_lookup.httpx, "AsyncClient", FakeAsyncClient)
    FakeAsyncClient.response = FakeResponse(status_code=503, text="blocked")

    html = asyncio.run(ebay_lookup._fetch_ebay_html("https://www.ebay.com/sch/i.html"))

    assert html is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd ~/claude/CardApp/webapp && source .venv/bin/activate && pytest tests/test_ebay_lookup.py -v`
Expected: FAIL — `AttributeError: module 'ebay_lookup' has no attribute 'SCRAPERAPI_KEY'`

- [ ] **Step 3: Add `SCRAPERAPI_KEY` + `_fetch_ebay_html()`**

Update the imports at the top of `webapp/ebay_lookup.py`. Replace:

```python
import json
import logging
import sqlite3
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional
```

with:

```python
import json
import logging
import os
import sqlite3
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional
from urllib.parse import quote
```

Then, right after the `USER_AGENT` constant definition, add the new constant and helper:

```python
USER_AGENT = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
              "AppleWebKit/537.36 (KHTML, like Gecko) "
              "Chrome/126.0.0.0 Safari/537.36")

# Optional: route eBay HTML fetches through ScraperAPI to dodge anti-bot
# blocking. Free tier = 1,000 credits/mo. Unset = current direct-fetch
# behavior, unchanged.
SCRAPERAPI_KEY = os.environ.get("SCRAPERAPI_KEY")


async def _fetch_ebay_html(url: str) -> Optional[str]:
    """Fetch an eBay sold-listings page.

    Routed through ScraperAPI if SCRAPERAPI_KEY is set; otherwise a direct
    GET with USER_AGENT (current behavior). Returns the response body, or
    None on a transport error or non-200 status (logs a warning).
    """
    if SCRAPERAPI_KEY:
        fetch_url = f"http://api.scraperapi.com/?api_key={SCRAPERAPI_KEY}&url={quote(url, safe='')}"
        headers = {}
    else:
        fetch_url = url
        headers = {"User-Agent": USER_AGENT}

    try:
        async with httpx.AsyncClient(timeout=15.0, headers=headers) as client:
            r = await client.get(fetch_url, follow_redirects=True)
    except httpx.HTTPError as e:
        log.warning("eBay fetch failed: %s", e)
        return None

    if r.status_code != 200:
        log.warning("eBay returned %s for %s", r.status_code, url)
        return None

    return r.text
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd ~/claude/CardApp/webapp && source .venv/bin/activate && pytest tests/test_ebay_lookup.py -v`
Expected: PASS — 3 passed

- [ ] **Step 5: Wire `_fetch_ebay_html` into `lookup_sold_listings`**

Replace:

```python
    try:
        async with httpx.AsyncClient(timeout=15.0,
                                       headers={"User-Agent": USER_AGENT}) as client:
            r = await client.get(url, follow_redirects=True)
    except httpx.HTTPError as e:
        log.warning("eBay fetch failed: %s", e)
        return None

    if r.status_code != 200:
        log.warning("eBay returned %s for %s", r.status_code, url)
        return None

    from pricing_engine import is_relevant_title
    parsed = parse_ebay_sold_html(r.text, period_days=period_days)
```

with:

```python
    html = await _fetch_ebay_html(url)
    if html is None:
        return None

    from pricing_engine import is_relevant_title
    parsed = parse_ebay_sold_html(html, period_days=period_days)
```

- [ ] **Step 6: Wire `_fetch_ebay_html` into `lookup_raw_price`**

Replace:

```python
    try:
        async with httpx.AsyncClient(timeout=15.0,
                                       headers={"User-Agent": USER_AGENT}) as client:
            r = await client.get(url, follow_redirects=True)
    except httpx.HTTPError as e:
        log.warning("eBay fetch failed: %s", e)
        return None

    if r.status_code != 200:
        log.warning("eBay returned %s for %s", r.status_code, url)
        return None

    sales = parse_ebay_sold_html(r.text, period_days=period_days)
```

with:

```python
    html = await _fetch_ebay_html(url)
    if html is None:
        return None

    sales = parse_ebay_sold_html(html, period_days=period_days)
```

- [ ] **Step 7: Run the full test suite to confirm no regressions**

Run: `cd ~/claude/CardApp/webapp && source .venv/bin/activate && pytest -v`
Expected: PASS — all tests pass

- [ ] **Step 8: Commit**

```bash
cd ~/claude/CardApp
git add webapp/ebay_lookup.py webapp/tests/test_ebay_lookup.py
git commit -m "feat: add opt-in ScraperAPI transport for eBay sold-listing fetches"
```

---

### Task 7: Update `CLAUDE.md`

**Files:**
- Modify: `CLAUDE.md`

- [ ] **Step 1: Add the new modules to the project layout tree**

In the `## Project layout` file tree, replace:

```
│   ├── pricecharting_lookup.py  # PriceCharting scraper (graded + raw)
│   ├── ebay_lookup.py       # eBay sold listings endpoint + 24h cache
│   ├── ebay_browse_api.py   # eBay Browse API (active listings, catalog fallback)
│   ├── refresh_job.py       # APScheduler daily 7am price refresh
```

with:

```
│   ├── pricecharting_lookup.py  # PriceCharting scraper (graded + raw)
│   ├── raw_price_resolver.py    # Shared raw-card baseline + PriceCharting sold-comp blend
│   ├── ebay_lookup.py       # eBay sold listings endpoint + 24h cache + optional ScraperAPI
│   ├── ebay_browse_api.py   # eBay Browse API (active listings, catalog fallback)
│   ├── refresh_job.py       # APScheduler: daily 7am price refresh + weekly trend-history refresh
│   ├── price_history_refresh.py # PriceCharting chart-data → price_history (weekly job + manual backfill)
```

- [ ] **Step 2: Replace the "Source routing" section**

Replace:

```markdown
### Source routing
- **Graded** → PriceCharting first; falls back to `GRADED_MULTIPLIERS` table in `app.py`
- **Raw EN** → TCGplayer via Pokemon TCG API, variant-aware key selection
  - Default key when variant absent: `holofoil` (Unlimited), NOT `max()`
  - `_OLD_PRINT_VARIANTS = {"shadowless"}` — 1st Edition uses TCGplayer keys, not PriceCharting
- **Raw JP** → PriceCharting Ungraded JP → TCGdex Cardmarket EUR → eBay last
```

with:

```markdown
### Source routing
- **Graded** → PriceCharting first; falls back to `GRADED_MULTIPLIERS` table in
  `app.py`, using the raw NM baseline from `raw_price_resolver.resolve_raw_price()`.
- **Raw (EN + JP)** → `webapp/raw_price_resolver.py` (`resolve_raw_price()`),
  shared by `/api/refresh-price` and the daily refresh job:
  1. **Baseline** — JP: eBay Browse API median of active listings; otherwise
     TCGplayer via Pokemon TCG API (variant-aware key selection — default key
     `holofoil` (Unlimited) when variant absent, NOT `max()`) or Cardmarket
     EUR (JP, via TCGdex); JP last-ditch: eBay sold median.
  2. **PriceCharting "Ungraded" cross-check** — sold-comp-derived, fetched for
     ALL raw cards (1st Edition / Shadowless included via PriceCharting's
     separate product page). The previous JP/old-print-only gate is gone.
  3. **Blend** — if both are present and diverge by more than
     `RAW_PRICE_DIVERGENCE_THRESHOLD` (15%), use PriceCharting's price (it's
     closer to "what this is actually selling for"); otherwise keep the
     catalogue baseline unchanged.
```

- [ ] **Step 3: Add a ScraperAPI subsection after the eBay parser section**

After the `### eBay parser (...)` section (the one describing `parse_ebay_sold_html` and the eBay URL format), add:

```markdown
### ScraperAPI (optional, `SCRAPERAPI_KEY`)
If `SCRAPERAPI_KEY` is set, `ebay_lookup._fetch_ebay_html()` routes eBay
sold-listings fetches through
`http://api.scraperapi.com/?api_key=...&url=...` instead of a direct GET.
Used by `lookup_sold_listings` (`/api/sold-listings` + the eBay-recent-mean
check in `/api/refresh-price`) and the legacy `lookup_raw_price`. Both call
sites are 24h-cached per-URL.

Deliberately NOT wired into the daily/weekly refresh jobs (`refresh_job.py`,
`price_history_refresh.py`) — those never call into `ebay_lookup`'s
eBay-HTML fetchers, so ScraperAPI usage stays limited to user-initiated,
cached requests within the free 1,000-credit/month tier. Unset (default):
zero behavioral change.
```

- [ ] **Step 4: Replace the "Historical price backfill" section**

Replace the entire `### Historical price backfill (\`backfill_historical_prices.py\`)` section (from its heading through the "Ran 2026-06-07: ..." paragraph) with:

```markdown
### Price-history refresh (`webapp/price_history_refresh.py`)
PriceCharting product pages embed `VGPC.chart_data = {"used": [[ts_ms, cents], ...], ...}`
inline — a free ~33-month monthly price series per card (`_CHART_DATA_RE` /
`pricecharting_lookup.fetch_chart_history`). The `"used"` series matches the
"Ungraded" row in the price table (verified against live cached prices).

`refresh_one()` / `refresh_all()` read this for every **raw** card
(`is_graded = 0`) and every sealed product, inserting rows into
`price_history` with `source='pricecharting_chart_backfill'`, deduped by
date so re-runs are safe. Graded cards are skipped — chart_data only exposes
one generic "graded" series, not grade/grader-specific ones, so blending it
into a graded card's history would mix incompatible price scales.

`refresh_job.py` runs `refresh_all(min_days=35)` weekly (Sunday 6am CT,
before the 7am daily price refresh) so the Overview trend chart keeps
picking up PriceCharting's latest monthly chart_data point.

`backfill_historical_prices.py` is a thin CLI wrapper over the same function
for one-off / manual runs (run from `webapp/`):

```bash
cd webapp && source .venv/bin/activate
python3 backfill_historical_prices.py --days 365   # default 90
```

Ran 2026-06-07: backfilled 233 rows across 22/36 raw cards spanning
2025-07 → 2026-06 (the other 14 raw cards aren't in PriceCharting's catalogue
— `fetch_chart_history` returns `None` for those, which is expected, not a bug).
```

- [ ] **Step 5: Commit**

```bash
cd ~/claude/CardApp
git add CLAUDE.md
git commit -m "docs: document raw_price_resolver, price_history_refresh, weekly job, and SCRAPERAPI_KEY"
```

---

## Self-Review

**1. Spec coverage** (against `docs/superpowers/specs/2026-06-12-recent-sold-pricing-design.md`):

- Section 1 (shared resolver) → Task 1. ✅
- Section 2 (`/api/refresh-price` integration) → Task 2. ✅
- Section 3 (daily refresh job alignment) → Task 3. ✅
- Section 4 (periodic trend-history refresh, incl. the weekly scheduler job) → Tasks 4 & 5. ✅
- Section 5 (ScraperAPI opt-in transport) → Task 6. ✅
- Section 6 (testing) → `test_raw_price_resolver.py` (Task 1), `test_price_history_refresh.py` (Task 4), `test_ebay_lookup.py` (Task 6) — all three spec-listed files included. `test_refresh_job.py` (Task 5) is an extra addition beyond the spec's file list, added to give the new scheduler job TDD coverage; it doesn't replace anything in the spec.
- Files Changed table → every row has a corresponding task (CLAUDE.md = Task 7).

One deliberate simplification vs. the spec's prose: Section 1 step 3 describes a `baseline_price <= 0` → "treat divergence as infinite, always prefer PriceCharting" branch. `_baseline_price` (Task 1) can only return `nm_price=None` or `nm_price > 0` — every assignment is behind a truthy check (`if br and br["median_usd"]`, `if base and base.market_price`, `if ebay and ebay.median_usd`), all of which are `False` for `0`/`0.0`. So `baseline.nm_price <= 0` is unreachable; that degenerate case collapses into the existing `baseline.nm_price is None` branch ("only PC present"), which Task 1's `test_only_pricecharting_when_no_baseline` already covers. No separate guard or test is needed, and the division `abs(pc_price - baseline.nm_price) / baseline.nm_price` is always safe.

**2. Placeholder scan:** No "TBD"/"TODO"/"similar to Task N" — every step has complete, runnable code or an exact command with expected output.

**3. Type consistency:**
- `RawPriceResult(nm_price: Optional[float], baseline_label: str, extra_note: Optional[str])` defined in Task 1; Task 2 and Task 3 both consume `result.nm_price` / `result.baseline_label` / `result.extra_note` with matching names.
- `resolve_raw_price(name, set_name, card_number, language="english", variant=None)` signature defined in Task 1; called identically (positional name/set_name/card_number, `language=`/`variant=` kwargs) in Task 2 (`app.py`) and Task 3 (`refresh_job.py`).
- `price_history_refresh.refresh_all(min_days: int = 90) -> dict` defined in Task 4, returning `{"raw_cards", "sealed_products", "inserted"}`; Task 5's scheduler job calls it with `kwargs={"min_days": 35}` and Task 4's CLI wrapper calls it positionally with `args.days` — both match the `min_days` parameter name/position.
- `price_history_refresh.refresh_one(card_id, name, cutoff_ms, fetch) -> int` defined and used consistently within Task 4 only (not called elsewhere).
- `ebay_lookup._fetch_ebay_html(url: str) -> Optional[str]` defined in Task 6, then called identically (`html = await _fetch_ebay_html(url); if html is None: return None`) in both `lookup_sold_listings` and `lookup_raw_price`.
- `SCRAPERAPI_KEY` referenced as a plain module global in Task 6's implementation and tests (`monkeypatch.setattr(ebay_lookup, "SCRAPERAPI_KEY", ...)`), consistent with `os.environ.get("SCRAPERAPI_KEY")` at module load time.

No gaps found.

---

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-06-12-recent-sold-pricing.md`. Two execution options:

**1. Subagent-Driven (recommended)** - I dispatch a fresh subagent per task, review between tasks, fast iteration

**2. Inline Execution** - Execute tasks in this session using executing-plans, batch execution with checkpoints

Which approach?
