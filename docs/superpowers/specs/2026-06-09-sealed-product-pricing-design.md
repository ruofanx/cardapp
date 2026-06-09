# Sealed Product Pricing — Design Spec

**Date:** 2026-06-09  
**Status:** Approved

## Overview

Add sealed Pokemon products (booster packs, booster boxes, ETBs, tins, bundles) to the pricing engine and collection tracker. Sealed products live alongside cards in the existing infrastructure — same `cards` table, same price history, same tags — distinguished by a new `product_type` column. Pricing routes eBay sold listings (primary) then PriceCharting (fallback). Portfolio splits card vs. sealed totals on the Home screen.

## Scope

Product types supported: `booster_pack`, `booster_box`, `etb`, `tin`, `bundle`.  
Out of scope: opened/loose product tracking, sealed product quantity management, graded sealed slabs.

---

## Section 1: Data Layer

### Schema

```sql
ALTER TABLE cards ADD COLUMN product_type TEXT NOT NULL DEFAULT 'card'
```

- Added as a lightweight migration in `db.init_db()` alongside the existing `image_url` / `source_url` migrations.
- Valid values: `card | booster_pack | booster_box | etb | tin | bundle`
- Existing rows default to `'card'` — no data migration needed.
- Semantically irrelevant columns (`card_number`, `is_graded`, `grade`, `grade_company`, `condition`) will be null for sealed products. This is acceptable: SQLite doesn't enforce it and the UI already handles null fields gracefully.

### `Card` dataclass

Add `product_type: str = "card"` field.

### `PortfolioSummary` dataclass

Add three new fields:

```python
sealed_count: int
total_sealed_value: float
total_cards_value: float
```

`total_market_value` is kept as `total_cards_value + total_sealed_value` for backwards compatibility.

### `portfolio_summary()` function

Split the market value sum into two sub-queries:
- `WHERE product_type = 'card'` → `total_cards_value`
- `WHERE product_type != 'card'` → `total_sealed_value`

---

## Section 2: Pricing Engine

### `pricing_engine.py`

Add `SealedProductQuery` dataclass:

```python
@dataclass
class SealedProductQuery:
    name: str          # e.g. "Scarlet & Violet 151"
    set_name: str
    product_type: str  # "booster_box", "etb", etc.
    language: str = "english"
```

Add `build_ebay_sealed_url(query: SealedProductQuery) -> str`:
- Builds a search string like `"Scarlet Violet 151 Booster Box sealed -opened -loose"`
- Maps `product_type` to human-readable search terms (e.g. `etb` → `"Elite Trainer Box"`)
- No grading exclusions; excludes opened/loose/damaged/lot listings
- For non-pack types (`booster_box`, `etb`, `tin`, `bundle`), also excludes `-pack` to avoid individual pack results; `booster_pack` searches do not apply this exclusion

Add `is_relevant_sealed_title(title: str, query: SealedProductQuery) -> bool`:
- Filters out opened, loose, damaged, lot, bundle (when searching for singles)
- Checks name tokens appear in title

### `ebay_lookup.py`

Add `lookup_sealed_recent_n_mean()` mirroring `lookup_recent_n_mean`:
- Accepts `SealedProductQuery`
- Uses `is_relevant_sealed_title` for filtering
- Same 24h SQLite cache, same `n=5` recent-mean logic
- Returns `EbayRecentMean` (existing dataclass — reusable)

### `pricecharting_lookup.py`

Add `lookup_sealed_price(name, set_name, product_type, language)`:
- URL pattern: `https://www.pricecharting.com/game/pokemon-<set-slug>/<product-slug>`
- Product slug map: `booster_box → booster-box`, `etb → elite-trainer-box`, `booster_pack → booster-pack`, `tin → tin`, `bundle → booster-bundle`
- Scrapes the `"Sealed"` price row (vs `"Loose"` for opened)
- Reuses existing `_slug()`, `_parse_price_table()`, and 24h cache
- Returns `PriceChartingResult` (existing dataclass)

### `app.py` — `refresh_price` endpoint

Early branch on `product_type`:

```python
if req.product_type and req.product_type != "card":
    return await _refresh_sealed_price(req)
```

`_refresh_sealed_price` priority order:
1. eBay recent-N-mean (`lookup_sealed_recent_n_mean`, n=5, 90-day window)
2. PriceCharting sealed price (`lookup_sealed_price`)
3. Return `null` with informative message if both fail

---

## Section 3: OCR Engine + API

### `ocr_engine.py`

Update the LLM prompt to:
- Recognize sealed product packaging (box art, ETB, tin, bundle) in addition to cards
- Return `product_type` in the response JSON alongside existing fields (`name`, `set_name`, `card_number`, `language`, `variant`)
- For sealed products: `card_number` and `variant` return `null`
- Valid `product_type` values are explicitly listed in the prompt

### `app.py` — model changes

```python
class RefreshPriceRequest(BaseModel):
    ...
    product_type: str = "card"

class IdentifyResponse(BaseModel):
    ...
    product_type: str = "card"
```

### Portfolio API response

`GET /api/users/{uid}/portfolio` gains new keys:

```json
{
  "total_cards_value": 1240.50,
  "total_sealed_value": 380.00,
  "total_market_value": 1620.50,
  "sealed_count": 4,
  ...
}
```

`total_market_value` is kept as the sum of both for backwards compatibility.

---

## Section 4: Frontend

### `api.jsx`

- `normalizeCard` maps `product_type` from backend row (default `'card'`)
- Add helper: `isSealedProduct(card)` → `card.product_type !== 'card'`

### `Home.jsx`

Portfolio summary gains a split line beneath the total:
```
Total: $1,620
  Cards $1,240 · Sealed $380
```
Uses new `total_cards_value` / `total_sealed_value` from portfolio API.

### `Browse.jsx`

- Filter bar adds type toggle: `All | Cards | Sealed`
- Sealed items show product-type badge (e.g. "Booster Box") in place of set number chip

### `Scan.jsx`

- After OCR returns, if `product_type !== 'card'`:
  - Hide condition, grade, and card number fields
  - Show read-only product type badge
- No other changes to the scan/add flow

### `Detail.jsx`

- If `isSealedProduct(card)`:
  - Hide Condition/Grade section
  - Hide card number
  - Show "Sealed Product" type badge next to name
- Price chart and price history work unchanged

### `components.jsx`

New component: `ProductTypeBadge({ type })` — renders a colored chip for each product type. Used in Browse and Detail.

---

## Files Changed

| File | Change |
|------|--------|
| `webapp/db.py` | Schema migration, `Card` + `PortfolioSummary` dataclasses, `portfolio_summary()` |
| `pricing_engine.py` | `SealedProductQuery`, `build_ebay_sealed_url`, `is_relevant_sealed_title` |
| `webapp/ebay_lookup.py` | `lookup_sealed_recent_n_mean` |
| `webapp/pricecharting_lookup.py` | `lookup_sealed_price` |
| `webapp/app.py` | `RefreshPriceRequest`, `IdentifyResponse`, `refresh_price` branch, `_refresh_sealed_price` |
| `ocr_engine.py` | Updated LLM prompt for sealed product recognition |
| `webapp/static/api.jsx` | `normalizeCard`, `isSealedProduct` helper |
| `webapp/static/components.jsx` | `ProductTypeBadge` component |
| `webapp/static/screens/Home.jsx` | Split portfolio total display |
| `webapp/static/screens/Browse.jsx` | Type filter toggle, sealed badge |
| `webapp/static/screens/Scan.jsx` | Conditional field hiding post-OCR |
| `webapp/static/screens/Detail.jsx` | Sealed product display adaptations |
