# Recent-Sold & Market-Trend Pricing Improvements — Design Spec

**Date:** 2026-06-12
**Status:** Approved

## Overview

Improve the accuracy of the "recent sold" headline price and the market-trend
chart without introducing new ongoing costs, by leaning harder on
PriceCharting's sold-comp-derived "Ungraded" price (currently only consulted
for JP/old-print raw cards), refreshing `price_history` from PriceCharting's
embedded chart data on a recurring basis, and aligning the daily refresh job
with the same logic as the on-demand refresh endpoint. Separately, add a
small, fully reversible, opt-in ScraperAPI transport for eBay sold-listing
fetches, scoped to on-demand (cached) calls only so it can't silently burn
through the free credit tier.

## Scope

In scope: raw card pricing (EN + JP), the Overview trend chart's
`price_history` data, the daily refresh job.
Out of scope: graded card pricing (already PriceCharting-first, unchanged),
sealed product pricing (has its own flow per
`2026-06-09-sealed-product-pricing-design.md`, unchanged).

---

## Section 1: Shared raw-price resolver

New module `webapp/raw_price_resolver.py`.

```python
@dataclass
class RawPriceResult:
    nm_price: Optional[float]
    baseline_label: str
    extra_note: Optional[str]

async def resolve_raw_price(
    name: str, set_name: str, card_number: str,
    language: str, variant: Optional[str] = None,
) -> RawPriceResult: ...
```

Logic:

1. **Baseline** — exactly today's existing cascade, extracted as-is:
   - JP only: eBay Browse API median of active listings
     (`ebay_browse_api.median_relevant_price`) → label
     `"eBay Browse median (JP, n=... of ..., range $...-$...)"`.
   - If still none: `card_lookup.lookup_card(...)` → TCGplayer (EN) /
     Cardmarket EUR (JP) → label `"TCGplayer (EN{variant})"` or
     `"Cardmarket EUR (JP)"`.
   - JP only, if still none: legacy eBay sold median
     (`ebay_lookup.lookup_raw_price`) → label `"eBay sold (JP-keyword)"`.
2. **PriceCharting cross-check** — for **all** raw cards regardless of
   language/variant (the JP/old-print gate is removed), call
   `pricecharting_lookup.lookup_raw_price(...)`. PriceCharting's "Ungraded"
   price is itself derived from aggregated recent sold comps, so this is a
   real "recent sold" signal, not just another market index.
3. **Blend decision**:
   - Neither present → `nm_price=None`.
   - Only one present → use it, with its existing label (PriceCharting label:
     `"PriceCharting Ungraded (sold-based)"`).
   - Both present → compute
     `divergence = abs(pc_price - baseline_price) / baseline_price`
     (if `baseline_price <= 0`, treat divergence as infinite — always prefer
     PriceCharting in that degenerate case).
     - `divergence <= 0.15` (the `RAW_PRICE_DIVERGENCE_THRESHOLD` constant):
       keep the baseline price and label unchanged — no churn for cards where
       TCGplayer/Cardmarket already agree with sold comps.
     - `divergence > 0.15`: `nm_price = pc_price`,
       `baseline_label = "PriceCharting Ungraded (sold-based)"`, and
       `extra_note = f"{old_baseline_label} was ${baseline_price:.2f} "
               f"(diverged {divergence:.0%}) — using PriceCharting sold-based price."`

This function is the single place both call sites below use, so the blend
rule only needs to be implemented and tested once.

---

## Section 2: `/api/refresh-price` integration (`webapp/app.py`)

- The eBay 5-recent-mean check (lines ~817-858) stays first, unchanged — it
  remains the headline source if it ever returns data (e.g. once the
  ScraperAPI experiment in Section 5 lands).
- Graded path (PriceCharting graded → JP eBay-Browse graded median) stays
  unchanged. Where it currently falls through to "baseline lookup" for the
  grade-multiplier fallback, it now gets that baseline from
  `raw_price_resolver.resolve_raw_price(...)`.
- The current "baseline lookup for raw or graded-fallback" block
  (lines ~920-1004 — JP eBay-Browse median, PriceCharting Ungraded
  [JP/old-variant only], `card_lookup`, legacy eBay sold median) is replaced
  by a single call to `resolve_raw_price(...)`, and `nm_price` /
  `baseline_label` / `extra_note` come directly from the returned
  `RawPriceResult`'s fields of the same names.
- Response construction (multiplier application, `source`/`note` strings) is
  otherwise unchanged — it already consumes `nm_price`, `baseline_label`, and
  `extra_note` generically.

---

## Section 3: Daily refresh job alignment (`webapp/refresh_job.py`)

`_price_for_card`'s raw branch currently calls
`card_lookup.lookup_card(...)` directly (TCGplayer/Cardmarket +
condition multiplier only — no PriceCharting, no blend).

Change: for raw cards (and as the NM baseline for the graded-fallback
multiplier path), call `raw_price_resolver.resolve_raw_price(...)` instead.
Everything downstream (condition/grade multiplier application) is unchanged.
This makes daily `price_history` snapshots reflect the same "best available,
sold-aware" price as the on-demand refresh button.

---

## Section 4: Periodic trend-history refresh

New module `webapp/price_history_refresh.py`, refactored from
`backfill_historical_prices.py`:

```python
async def refresh_one(card_id: int, name: str, cutoff_ms: float, fetch) -> int: ...
async def refresh_all(min_days: int) -> dict:
    """Returns {"raw_cards": N, "sealed_products": N, "inserted": N}."""
```

- `refresh_one` is `_backfill_one` unchanged (fetch chart data, dedup by
  `recorded_at[:10]`, insert new rows into `price_history` with
  `source='pricecharting_chart_backfill'`).
- `refresh_all` is today's `backfill()` function, returning a summary dict
  instead of printing.
- `backfill_historical_prices.py` becomes a thin CLI:
  `asyncio.run(price_history_refresh.refresh_all(args.days))`, prints the
  returned summary. No behavior change for manual runs.
- `refresh_job.py` gains a new APScheduler job:
  ```python
  _scheduler.add_job(
      lambda: price_history_refresh.refresh_all(min_days=35),
      CronTrigger(day_of_week="sun", hour=6, minute=0, timezone=DAILY_TIMEZONE),
      id="weekly_price_history_refresh",
      name="Weekly price-history refresh (Sun 6am CT)",
      replace_existing=True,
      misfire_grace_time=6 * 60 * 60,
  )
  ```
  `min_days=35` is enough to pick up the latest monthly chart_data point
  while the existing date-based dedup prevents duplicate rows on overlap.
  Runs before the 7am daily price refresh.

---

## Section 5: ScraperAPI opt-in transport (`webapp/ebay_lookup.py`)

- New optional env var `SCRAPERAPI_KEY`.
- New helper:
  ```python
  async def _fetch_ebay_html(url: str) -> Optional[str]:
      """Fetch an eBay sold-listings page, routed through ScraperAPI if
      SCRAPERAPI_KEY is set, else direct (current behavior). Returns the
      response body, or None on error/non-200 (logs a warning)."""
  ```
  - If `SCRAPERAPI_KEY` set: GET
    `f"http://api.scraperapi.com/?api_key={key}&url={quote(url, safe='')}"`.
  - Else: current direct GET with `USER_AGENT` header (unchanged behavior
    when no key is configured — fully backwards compatible no-op).
- Used by `lookup_sold_listings` and the legacy `lookup_raw_price` — the two
  functions that fetch `build_ebay_sold_url(...)` HTML directly. Both are
  already 24h-cached per-URL.
- `lookup_recent_n_mean` calls `lookup_sold_listings` internally, so wiring
  that one function covers both the Sold Listings tab (`/api/sold-listings`)
  and the eBay-recent-mean headline check in `/api/refresh-price`.
- **Deliberately not wired into**: the daily refresh job (Section 3 never
  calls into `ebay_lookup`'s eBay-HTML fetchers — `raw_price_resolver`'s
  baseline only uses `ebay_browse_api` [Browse API, free 5000/day, separate
  quota] and `card_lookup`/`pricecharting_lookup`), and not wired into
  `lookup_sealed_recent_n_mean` (sealed is out of scope). This keeps
  ScraperAPI usage strictly to user-initiated, 24h-cached requests so 36
  cards can't exceed the free 1,000-credit/month tier through automation.
- If `SCRAPERAPI_KEY` is unset (default), there is zero behavioral change.

---

## Section 6: Testing

New test files under `webapp/tests/` (existing pytest + pytest-asyncio setup):

- `test_raw_price_resolver.py` — mocks `card_lookup`, `pricecharting_lookup`,
  `ebay_browse_api`, `ebay_lookup` to cover: only-baseline, only-PC,
  agree-within-threshold (keeps baseline), diverge-beyond-threshold (switches
  to PC + note), baseline price `<= 0` (always prefers PC).
- `test_price_history_refresh.py` — covers `refresh_one`'s dedup-by-date
  (existing `recorded_at` dates skipped) and `cutoff_ms` filtering, using an
  in-memory/temp SQLite db.
- `test_ebay_lookup.py` — covers `_fetch_ebay_html`'s URL construction with
  and without `SCRAPERAPI_KEY` set (mocked `httpx.AsyncClient`).

Manual verification after implementation: via `:8000` Detail screen, refresh
a few raw EN cards and a couple of JP cards, confirm `source`/`note` reflect
the new blend (including a case where PriceCharting and TCGplayer diverge),
and confirm the weekly trend-refresh job is registered at startup (check
scheduler log line).

---

## Files Changed

| File | Change |
|------|--------|
| `webapp/raw_price_resolver.py` (new) | `RawPriceResult`, `resolve_raw_price()` — baseline + PriceCharting blend, shared by refresh-price and daily refresh job |
| `webapp/app.py` | `/api/refresh-price` raw/graded-fallback baseline section replaced with `resolve_raw_price()` call |
| `webapp/refresh_job.py` | `_price_for_card` raw branch uses `resolve_raw_price()`; new weekly `price_history_refresh` job |
| `webapp/price_history_refresh.py` (new) | `refresh_one()` / `refresh_all()` — refactored from `backfill_historical_prices.py` |
| `backfill_historical_prices.py` | Thin CLI wrapper over `price_history_refresh.refresh_all()` |
| `webapp/ebay_lookup.py` | `_fetch_ebay_html()` helper — routes through ScraperAPI if `SCRAPERAPI_KEY` set; used by `lookup_sold_listings` and `lookup_raw_price` |
| `webapp/tests/test_raw_price_resolver.py` (new) | Blend logic tests |
| `webapp/tests/test_price_history_refresh.py` (new) | Dedup/cutoff tests |
| `webapp/tests/test_ebay_lookup.py` (new) | ScraperAPI transport URL tests |
| `CLAUDE.md` | Document `SCRAPERAPI_KEY`, new modules, new weekly job, updated raw-price cascade |
