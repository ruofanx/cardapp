# PokeCollect — CardApp

Pokemon card collection tracker + trade tool for Ro, Reid, and Ryan.
FastAPI backend + React/Babel PWA frontend, served together on port 8000.

## Project layout

```
~/claude/CardApp/
├── pricing_engine.py        # eBay HTML scraper, aggregator, query builder
├── ocr_engine.py            # Card photo → identity (Claude / Gemini)
├── webapp/
│   ├── app.py               # FastAPI — all API routes
│   ├── db.py                # SQLite schema + helpers
│   ├── card_lookup.py       # Pokemon TCG API (EN cards + TCGplayer prices)
│   ├── tcgdex_lookup.py     # TCGdex (JP cards + Cardmarket EUR prices)
│   ├── pricecharting_lookup.py  # PriceCharting scraper (graded + raw)
│   ├── raw_price_resolver.py    # Sold-anchor + listing-trend price model (see Pricing logic)
│   ├── ebay_lookup.py       # eBay sold listings endpoint + 24h cache + optional ScraperAPI
│   ├── ebay_browse_api.py   # eBay Browse API (active listings, catalog fallback)
│   ├── refresh_job.py       # APScheduler: daily 7am price refresh + weekly trend-history refresh
│   ├── price_history_refresh.py # PriceCharting chart-data → price_history (weekly job + manual backfill)
│   ├── trade_proposer.py    # Subset-sum trade matcher
│   ├── run.sh               # Start server: uvicorn app:app --reload --port 8000
│   ├── pokemon_trading.sqlite      # Main DB (gitignored)
│   ├── ebay_cache.sqlite           # 24h eBay cache (gitignored)
│   ├── pricecharting_cache.sqlite  # 24h PC cache (gitignored)
│   ├── uploads/             # User card photos (gitignored)
│   └── static/              # React+Babel frontend (served by FastAPI)
│       ├── index.html       # Entry point; Babel-standalone transpiles JSX in-browser
│       ├── api.jsx          # All fetch calls to the backend
│       ├── app.jsx          # Navigation stack, global state, user switching
│       ├── components.jsx   # Shared UI: CardArt, Price, Sparkline, Icon, NavBar…
│       ├── data.jsx         # Mock/seed data, CARDS constant
│       ├── styles.css       # CSS custom properties, layout primitives
│       ├── ios-frame.jsx    # iOS chrome wrapper
│       ├── tweaks-panel.jsx # Dev tweaks overlay
│       └── screens/
│           ├── Detail.jsx   # Card detail — pricing chart, sold listings, variants
│           ├── Browse.jsx   # Collection grid + filters
│           ├── Home.jsx     # Portfolio summary + sparklines
│           ├── Scan.jsx     # Camera / search to add cards
│           ├── Bulk.jsx     # Bulk import
│           ├── Trade.jsx    # Trade proposal screen
│           └── SettingsAndOnboarding.jsx
```

## Running the app

```bash
cd ~/claude/CardApp/webapp
./run.sh                          # starts uvicorn on :8000
# or:
uvicorn app:app --reload --host 0.0.0.0 --port 8000
```

`--host 0.0.0.0` required for iPhone access over LAN.
Frontend is served at `http://localhost:8000/` by FastAPI's `StaticFiles` mount —
**this is the only server to run**; don't spin up a separate dev server (e.g.
Vite on `:5173`) to serve `static/`, since `:8000` already serves both the API
and the frontend from the same `STATIC_DIR`.

In `app.py`, the catch-all `StaticFiles(directory=STATIC_DIR)` mount MUST be
registered at `/` **last**, after `/uploads` and the explicit `@app.get("/")`
route — Starlette matches routes in registration order, so an earlier
`/static`-prefixed mount leaves `index.html`'s relative asset references
(`styles.css`, `app.jsx`, `screens/Detail.jsx`, ...) 404ing at the root and
the page renders blank. (This was a pre-existing bug since commit `9ebb16e`,
fixed 2026-06-07 by reordering the mounts.)

## API routes

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/api/users` | List users |
| GET | `/api/users/{uid}/cards` | User's collection |
| GET | `/api/users/{uid}/portfolio` | Portfolio value + history |
| POST | `/api/users/{uid}/cards` | Add card |
| PATCH | `/api/cards/{id}` | Update card fields |
| DELETE | `/api/cards/{id}` | Remove card |
| POST | `/api/cards/{id}/photo` | Upload photo (multipart, field: `photo`) |
| GET | `/api/cards/{id}/price-history` | `{points:[{at,price}], current, currency}` |
| GET | `/api/cards/search?q=` | Text search (used by Scan bar) |
| POST | `/api/identify` | Photo → card identity (multipart, field: `photo`) |
| POST | `/api/refresh-price` | Get price quote (does NOT write to card row) |
| POST | `/api/sold-listings` | eBay sold listings for a card |
| POST | `/api/trade/propose` | Subset-sum trade proposal |
| POST | `/api/refresh-prices/run-now` | Trigger full refresh |

**refresh-price quirk:** returns `{estimated_price, source, image_url}` but does NOT update the DB. `api.jsx` POSTs here then PATCHes `/api/cards/{id}` separately.

## Pricing logic

### Source routing
- **Graded** → PriceCharting first; falls back to `GRADED_MULTIPLIERS` table in
  `app.py`, using the raw NM baseline from `raw_price_resolver.resolve_raw_price()`.
- **Raw (EN + JP)** → `webapp/raw_price_resolver.py` (`resolve_raw_price()`),
  shared by `/api/refresh-price` and the daily refresh job. All three signals
  are fetched in parallel (`asyncio.gather`) then combined:
  1. **Sold anchor** (primary) — PriceCharting "Ungraded" for any language
     (aggregated eBay sold comps, most accurate). EN fallback: TCGplayer market
     price (also a rolling 30-day sale median, sold-based). JP-only: Cardmarket
     EUR is listing-based, treated as a trend signal (not an anchor).
  2. **Trend signal** — eBay Browse API active-listing median for all cards.
     For JP: retries with a wider name-only query if primary result looks wrong
     (SIR-range card numbers, thin sample < 3). This is direction, not price.
  3. **Blend** — `trend = (ask_median / sold_base) - 1`, clamped to ±`TREND_CAP`
     (15%). `price = sold_base × (1 + trend_adj)`. Rising ask prices push the
     estimate up; softening asks pull it down. Avoids chasing thin Browse samples.
  4. **Fallbacks** — Browse-only (no sold data): apply `BROWSE_HAIRCUT` (12%)
     discount. Cardmarket-only (JP, no Browse): also apply haircut. Last resort:
     eBay sold HTML via ScraperAPI (almost always 403 without it).

### eBay parser (`pricing_engine.py → parse_ebay_sold_html`)
Splits HTML on `<li class="s-item">` boundaries, extracts date/price/title/url
independently with multiple fallback CSS class patterns. Do NOT revert to a
single cross-item `re.DOTALL` regex — that approach breaks silently when eBay
changes class names (returned 0 results after their last HTML overhaul).

eBay URL: `LH_Sold=1&LH_Complete=1&_ipg=240&_sop=13` (240 results, newest first).
No date param in the URL — Python filters by `period_days` after parsing (default 60).

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
cached requests within the free 1,000-credit/month tier. Also not wired into
`lookup_sealed_recent_n_mean` (sealed product sold-listings) — sealed pricing
is out of scope for this change. Unset (default): zero behavioral change.

### Grading scales
PSA: whole grades only (10, 9, 8…).
CGC/BGS/SGC: half-grades + a 10.5 sentinel for top grades:
- CGC 10.5 = CGC 10 Pristine
- BGS 10.5 = BGS 10 Black Label
- SGC 10.5 = SGC 10 Pristine

## Frontend architecture

React 18 + Babel-standalone (transpiled in-browser, no build step).
Custom navigation stack in `app.jsx` — no React Router.
`index.html` rewrites every `<script type=text/babel>` to `?v=${Date.now()}`
to bust the browser cache on reload. Do not remove this.

### Card data shape (frontend)
`api.normalizeCard` maps the backend row to:
```
{ id, name, set, code, lang, usd, change, image_url, condition,
  is_graded, grader, grade, purchase_price, tags[], ... }
```
- `lang`: `"EN"` / `"JP"` (frontend) ↔ `"english"` / `"japanese"` (backend)
- `grader`: maps from `grade_company`
- `usd`: maps from `current_market_price`
- `change`: maps from `gain_loss_pct`

### Overview tab pricing chart (`screens/Detail.jsx`)
`PricePointChart` SVG component: dots = individual data points,
line = rolling average (window adapts to sample count).
Plots eBay sold prices when available; falls back to daily `price_history`
snapshots (`historyPoints`/`points`) in the same dots+trendline style when
sold-listing data is unavailable — labeled "price snapshots" on the chart.

**eBay sold-listing data is effectively unobtainable** (verified 2026-06-07):
HTML scraping gets a hard 403 from eBay's anti-bot, the Finding API
(`findCompletedItems`) is deprecated for new developer accounts, and the
Marketplace Insights API requires special "Limited Release" approval that
this app's eBay developer account does not have (`invalid_scope` on
`buy.marketplace.insights`). The Browse API (`ebay_browse_api.py`) only
covers active listings. So `soldListings` will essentially always be `[]`
and the chart runs on snapshot data — treat that as the primary path, not
a fallback edge case.

Range picker (1W/1M/3M/1Y/ALL) filters client-side; server window is 60 days.

### Wishlist
Wishlist = `wishlist` tag on a card. Browse hides wishlist cards from default
view. Toggle entry points: Scan result screen + Detail screen star button.

### Demo mode
When backend is unreachable, `app.jsx` sets `backend.online = false`,
fills collection with `window.CARDS` mock data, shows an orange offline banner.

### Backend URL resolution (api.jsx)
1. `window.POKECOLLECT_API` (set before scripts load)
2. `window.location.hostname:8000`
3. `localhost:8000`

## Database (SQLite)

Key tables: `users`, `cards`, `tags`, `card_tags`, `price_history`.

`price_history`: `(card_id, recorded_at, price_usd, source)`.
`db.log_price()` is idempotent within 60s + ±$0.005.
`db.backfill_price_history()` runs at startup to seed legacy cards.

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

## LLM / OCR

`ocr_engine.py` tries Claude (`ANTHROPIC_API_KEY`) first, falls back to Gemini
(`GOOGLE_API_KEY`). Force Gemini with `LLM_PROVIDER=gemini` env var.
Models: `claude-sonnet-4-5` / `gemini-2.5-flash`.

## Users

Ro + Reid + Ryan (pre-seeded). PWA installed on iPhone via Add-to-Home-Screen.
