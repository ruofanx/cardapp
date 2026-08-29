# Pricing Prediction Model — Design

**Date:** 2026-08-29
**Status:** Approved design, pre-implementation

## Overview

A fundamentals-based fair-value model for Pokemon cards. Today the app only
*observes* market prices (eBay Browse, PriceCharting, TCGplayer, Cardmarket
via `raw_price_resolver.py`). This subsystem *predicts* what a card should be
worth from structural factors — pull-rate scarcity, grade scarcity, rarity,
character attraction, and where the card sits in its market lifecycle — and
compares that prediction against the observed price.

One core model serves four use cases:

1. **Gap-fill** — a price estimate when comps are thin or missing (new
   releases, obscure cards, CN exclusives).
2. **Forecast** — where a card's price is likely headed as its lifecycle and
   population inputs evolve.
3. **Grade-worthiness** — expected value of grading a raw card.
4. **Sanity check** — flag cards whose market price diverges from
   fundamentals (over/undervalued).

**Language scope:** EN, JP, and Chinese-exclusive cards from day one. CN
cards have almost no comps, so the fundamentals prediction is often their
only price signal; they carry an explicitly wider uncertainty band because
the CN market factor is fitted from thin data.

**Data policy:** free/derivable inputs only. No paid APIs. PSA and GemRate
population pages were both evaluated for scraping and are Cloudflare-blocked
(confirmed 403/managed-challenge on both — the same class of block already
documented for eBay sold-listings in this repo). No free source of real
grading-population data exists, so grade scarcity is a derived heuristic
instead (see feature 3 below), not scraped.

## Data layer

### Feature inputs (per card)

1. **Canonical rarity.** Source: Pokemon TCG API `rarity` (EN), TCGdex
   `rarity` (JP/CN). A versioned mapping table in code normalizes the ~40
   era/language-specific rarity strings onto one canonical scarcity ladder:
   Common → Uncommon → Rare → Holo → Ultra Rare → IR/AR → SIR/SAR →
   UR/Gold → promo-special. Unmapped strings are logged and the card gets
   no prediction (never a garbage one).

2. **Pull-rate scarcity.** No official pull rates exist, so this is derived
   structure: a config table of `expected copies per booster box` keyed by
   (era, canonical rarity tier), seeded from well-known community ratios
   (e.g. SV-era SIR ≈ 1 per 2–3 boxes, IR ≈ 3 per box), overridable per set
   for outliers (god-pack sets like Terastal Festival, special structures).
   Effective scarcity = pulls-per-box ÷ number of cards competing in that
   rarity slot within the set.

3. **Grade-scarcity estimate (`gem_rate`).** Not scraped — PSA and GemRate
   are both Cloudflare-blocked (see Data policy above). Instead, the same
   treatment as pull-rate: a config table keyed by (era, canonical rarity
   tier, surface type — textured/full-art foils gem noticeably worse than
   standard holo) seeded from well-known community gem-rate ranges,
   overridable per set. This is explicitly an estimate, not measured
   population data, and the confidence band accounts for that. The
   `card_features.gem_rate` column is named generically so a future paid
   source (PSA API, GemRate API) can populate it with real data with zero
   schema rework.

4. **Character attraction.** A curated static tier file (S/A/B/C/D) of
   species and trainer characters, seeded from official popularity polls and
   known market tiers (Charizard, Umbreon, Rayquaza, Eeveelutions, Pikachu =
   S; etc.), plus an `is_trainer_art` flag because full-art trainer cards
   price independently of species. The file is plain data, editable by hand.
   Unknown characters default to tier C.

5. **Context.** Set release date (→ months since release), era, and a
   per-language market factor (EN/JP/CN).

### New tables (SQLite)

- `card_features` — per card: canonical rarity, pull-rate scarcity,
  `gem_rate` (heuristic estimate), character tier, `is_trainer_art`,
  language, set release date, `features_updated_at`.
- `market_corpus` — training rows harvested from PriceCharting:
  (card key, month, raw price, psa10 price).
- `model_runs` — versioned fit artifacts: coefficients JSON,
  lifecycle-curve points, market-index series, residual stats, fit date.
  Predictions always compute on demand from the latest run — nothing is
  precomputed per card, so a refit instantly updates every card.

## Model core

Log-linear regression (ridge-regularized), two heads:

```
log(raw_price)   = β0 + β_era + β_lang + β_pull·log(pull_scarcity)
                   + β_char[tier] + lifecycle(months_since_release) + ε

log(psa10_price) = same features + β_gem·log(1/gem_rate)
```

Fitting in log space makes every coefficient a multiplier, so each
prediction decomposes exactly: `$142 = $8 base × 5.2 rarity × 3.1 character
× 1.1 scarcity × 1.6 lifecycle`. Ridge regularization keeps small-sample
coefficients (CN language factor, rare tiers) sane.

### Lifecycle curve (timing pillar)

The age term is not a simple drift — it is a fitted **lifecycle curve**
capturing the canonical modern-card trajectory: release hype spike → slide
during print waves → trough near peak supply (typically 3–9 months
post-release) → recovery after the set leaves print. Fitted as a flexible
curve (e.g. spline or binned means over months-since-release) across the
whole corpus, optionally per era.

Output per card: lifecycle phase (hype / supply slide / trough window /
post-print recovery), and the card's deviation from its fitted curve
position. "At the trough window" is the model's buy-timing signal; "well
past out-of-print and above curve" is the sell-side mirror.

**Reprint caveat:** heavily reprinted sets (151-style restocks,
Celebrations) break the lifecycle assumption. The fit flags sets whose
price path diverges persistently below the curve rather than confidently
calling a trough that keeps deepening; flagged sets show a "reprint
pressure" note instead of a timing verdict.

### Market index (detrending)

A market-wide index — median monthly price movement across the entire
corpus — is computed per fit. The card model is trained on market-detrended
prices, separating card-specific movement from everything-moved-together
movement. The index position ("market is 12% off its high") is reported as
context alongside predictions. With ~33 months of history (roughly one
boom-recovery arc) the index is context only — the model makes no claim to
time macro turns. Per-card technical analysis (support/resistance on
monthly data) is deliberately excluded as noise; deviation from the fitted
lifecycle curve carries the same intuition with statistical footing.

### Grade-worthiness

```
EV(grading) = gem_rate × predicted_PSA10
            + (1 − gem_rate) × predicted_PSA9
            − grading_fee − raw_price
```

PSA9 is approximated as a fitted fraction of PSA10 (single corpus-wide
ratio in V1). `gem_rate` here is the heuristic estimate, not a measured
rate — the EV verdict is labeled "estimated" in the UI. Grading fee is a
config constant. Positive EV with margin → "worth grading" verdict; the
full arithmetic is shown to the user.

### Confidence

Every prediction carries an interval derived from regression residuals,
widened per-language by comp coverage (CN widest). Missing inputs widen the
band further (see error handling).

### Training corpus

The user's own collection (~36 cards) is far too small to fit on. Corpus:
bulk-harvest PriceCharting full-set price pages for ~15–20 sets spanning
eras and languages (few thousand cards, raw + PSA10 columns), through the
existing `pricecharting_lookup` cache machinery. One-time backfill script
seeds it; a monthly job refreshes it before refit.

## Jobs (extends `refresh_job.py`)

- **Monthly** — corpus refresh + model refit. A refit that degrades fit
  quality beyond a threshold (vs. the previous run's residual stats) keeps
  the previous `model_runs` row active and logs a warning instead of
  silently shipping worse predictions.
- **One-time** — corpus backfill CLI (pattern of
  `backfill_historical_prices.py`) to harvest the training sets.

`gem_rate` is a static heuristic lookup (no network call, nothing to go
stale) — feature staleness in practice means an unmapped rarity/era/surface
combination, handled by the fallback in Error handling below.

## API

- `GET /api/cards/{id}/prediction` →
  fair value + interval, factor breakdown (multiplier per factor),
  lifecycle position (months in, phase, deviation from curve), market index
  context, grade EV verdict with its arithmetic, confidence level, and
  which inputs were missing/stale.
- `GET /api/users/{uid}/rankings?sort=undervalued|upside|grade_ev` →
  collection ranked by valuation gap, combined valuation+timing upside, or
  grading EV.

## UI

- **Detail screen — "Fair Value" panel:** predicted price with band, the
  multiplier breakdown, over/undervalued badge vs. current market price, a
  small lifecycle-curve sparkline with a "you are here" dot, and (raw cards
  only) the grade-worthiness verdict with its EV math.
- **Rankings:** entry point from Browse/Home listing the collection sorted
  by the rankings API — "which of my cards are the best holds / best
  grading candidates."

The combined buy/sell signal is two explainable numbers: **valuation gap**
(market vs. fair value) and **timing position** (lifecycle phase + market
index context). "Undervalued AND in the trough window" is the strongest buy
flag.

## Error handling

The model degrades gracefully per missing input, and says so in the UI:

- Unmapped (era, rarity, surface type) combo for `gem_rate` → falls back to
  a rarity-tier-only default estimate, band widens, panel notes "estimated
  gem rate".
- Unknown character → tier C default.
- Unmapped rarity string → logged; **no prediction** rather than a wrong one.
- CN cards → always the wider band.
- Stale features (rarity/character lookups) → visible timestamp;
  predictions still compute.
- PriceCharting scrape/HTTP failures during corpus refresh → cached values
  used; job logs and continues.

## Testing

- Unit tests: rarity normalization mapping (all known strings), pull-rate
  config lookup + per-set overrides, `gem_rate` heuristic lookup + fallback
  behavior, grade-EV arithmetic.
- Fit-quality regression test: fit on a frozen corpus fixture, assert R²
  and coefficient-sign sanity, so refactors can't silently break the model.
- API tests: prediction endpoint happy path plus each degraded-input path.

## Out of scope (V1)

- Real PSA/GemRate/BGS population data — both evaluated and confirmed
  Cloudflare-blocked; `gem_rate` is a heuristic estimate instead. Column is
  schema-ready for a future paid source (PSA API, GemRate API).
- Dynamic character popularity (search/sales volume); the tier file is
  static and hand-curated.
- Macro-cycle forecasting; the market index is context only.
- Per-card technical analysis (support/resistance).
- Sealed products — this model is singles-only.

## Risks

- **`gem_rate` is a heuristic estimate, not measured data** — PSA and
  GemRate are both confirmed Cloudflare-blocked (403/managed-challenge),
  so this factor leans on community-known gem-rate ranges rather than real
  population counts, and may drift from reality for newer sets with little
  grading history yet. Mitigated by being one multiplier among several and
  by the confidence band accounting for estimate-based inputs; schema is
  ready to swap in real data from a paid source later.
- **Pull-rate config is approximate** — community ratios, not official
  data; mitigated by per-set overrides and the factor being one multiplier
  among several.
- **CN training data is thin** — the CN language factor leans on EN/JP
  structure; mitigated by the wider band and honest missing-input notes.
- **Reprints** — break lifecycle assumptions; mitigated by the reprint-
  pressure flag.
