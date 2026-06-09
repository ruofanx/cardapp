# Sealed Product Pricing Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add sealed Pokemon products (booster packs, boxes, ETBs, tins, bundles) to the collection tracker with eBay-primary / PriceCharting-fallback pricing, OCR recognition, and a split portfolio display.

**Architecture:** A single `product_type TEXT DEFAULT 'card'` column on the existing `cards` table gates all new behaviour. The pricing engine gets a parallel `SealedProductQuery` path; the OCR prompt is extended to emit `product_type`; the portfolio summary splits card vs. sealed totals; the frontend adds a type filter in Browse and a split row in Home.

**Tech Stack:** Python 3.14, FastAPI, SQLite, httpx, React 18 + Babel-standalone (no build step), pytest (add to dev deps).

**Spec:** `docs/superpowers/specs/2026-06-09-sealed-product-pricing-design.md`

---

## File Map

| File | Change |
|------|--------|
| `webapp/db.py` | Schema migration, `Card.product_type`, `PortfolioSummary` split fields, `portfolio_summary()` |
| `pricing_engine.py` | `SealedProductQuery`, `PRODUCT_TYPE_SEARCH_TERMS`, `build_ebay_sealed_url`, `is_relevant_sealed_title` |
| `webapp/ebay_lookup.py` | `lookup_sealed_recent_n_mean` |
| `webapp/pricecharting_lookup.py` | `SEALED_PRODUCT_SLUGS`, `lookup_sealed_price` |
| `webapp/app.py` | `RefreshPriceRequest.product_type`, `_refresh_sealed_price`, branch in `refresh_price`, `product_type` in identify response, `refreshPrice`/`quotePrice` pass `product_type` |
| `ocr_engine.py` | Updated `LLM_SYSTEM_PROMPT`, `IdentifyResult.product_type`, `IdentifyCache` migration, `_build_identity_from_json` 4-tuple, `identify_card` |
| `webapp/static/api.jsx` | `normalizeCard` adds `product_type`, add `isSealedProduct`, `refreshPrice`/`quotePrice` pass `product_type` |
| `webapp/static/components.jsx` | `ProductTypeBadge` component |
| `webapp/static/screens/Home.jsx` | Sealed split display below total |
| `webapp/static/screens/Browse.jsx` | `sealed` filter chip + filter logic |
| `webapp/static/screens/Scan.jsx` | Hide condition/grade for sealed in `CardPreview` |
| `webapp/static/screens/Detail.jsx` | Hide condition/grade section + show type badge for sealed |
| `webapp/tests/test_sealed.py` | All backend unit tests |

---

## Task 1: Schema + data layer

**Files:**
- Modify: `webapp/db.py`
- Create: `webapp/tests/__init__.py`
- Create: `webapp/tests/test_sealed.py`

- [ ] **Step 1: Install pytest**

```bash
cd ~/claude/CardApp/webapp
source .venv/bin/activate
pip install pytest pytest-asyncio
```

Expected: `Successfully installed pytest-...`

- [ ] **Step 2: Write failing tests for schema + portfolio split**

Create `webapp/tests/__init__.py` (empty file) and `webapp/tests/test_sealed.py`:

```python
"""Tests for sealed product schema and portfolio split."""
import os, sys, sqlite3, tempfile, pytest
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

import db as _db

@pytest.fixture
def test_db(tmp_path, monkeypatch):
    db_path = tmp_path / "test.sqlite"
    monkeypatch.setattr(_db, 'DB_PATH', db_path)
    _db.init_db()
    return db_path

def test_card_has_product_type_column(test_db):
    with _db.connect() as conn:
        cols = [r[1] for r in conn.execute("PRAGMA table_info(cards)").fetchall()]
    assert 'product_type' in cols

def test_card_defaults_product_type_to_card(test_db):
    users = _db.list_users()
    card = _db.create_card(_db.Card(
        id=None, user_id=users[0].id, name='Charizard', set_name='Base Set',
        card_number='4/102', language='english',
    ))
    assert card.product_type == 'card'

def test_create_sealed_product(test_db):
    users = _db.list_users()
    box = _db.create_card(_db.Card(
        id=None, user_id=users[0].id, name='Scarlet & Violet 151',
        set_name='Scarlet & Violet 151', language='english',
        product_type='booster_box',
    ))
    assert box.product_type == 'booster_box'
    fetched = _db.get_card(box.id)
    assert fetched.product_type == 'booster_box'

def test_portfolio_summary_splits_cards_and_sealed(test_db):
    users = _db.list_users()
    uid = users[0].id
    _db.create_card(_db.Card(
        id=None, user_id=uid, name='Pikachu', set_name='Base',
        card_number='58/102', language='english', product_type='card',
        current_market_price=25.0,
    ))
    _db.create_card(_db.Card(
        id=None, user_id=uid, name='Scarlet & Violet 151', set_name='SV 151',
        card_number=None, language='english', product_type='booster_box',
        current_market_price=120.0,
    ))
    summary = _db.portfolio_summary(uid)
    assert summary.total_cards_value == 25.0
    assert summary.total_sealed_value == 120.0
    assert summary.total_market_value == 145.0
    assert summary.card_count == 1
    assert summary.sealed_count == 1
```

- [ ] **Step 3: Run tests to verify they fail**

```bash
cd ~/claude/CardApp/webapp
source .venv/bin/activate
python -m pytest tests/test_sealed.py -v 2>&1 | head -40
```

Expected: 4 failures — `product_type` column and dataclass fields don't exist yet.

- [ ] **Step 4: Add migration + update `Card` dataclass**

In `webapp/db.py`, add `product_type: str = "card"` to the `Card` dataclass (after `created_at`):

```python
    created_at: Optional[str] = None
    tags: list["Tag"] = field(default_factory=list)
    product_type: str = "card"
```

In `init_db()`, add the migration alongside the existing ones (after the `source_url` migration):

```python
        try:
            conn.execute("ALTER TABLE cards ADD COLUMN product_type TEXT NOT NULL DEFAULT 'card'")
        except sqlite3.OperationalError:
            pass
```

In `_row_to_card()`, set `product_type` from the row:

```python
def _row_to_card(r) -> Card:
    d = dict(r)
    d["is_graded"] = bool(d.get("is_graded", 0))
    d.setdefault("product_type", "card")
    return Card(**d, tags=[])
```

- [ ] **Step 5: Update `PortfolioSummary` + `portfolio_summary()`**

Replace the `PortfolioSummary` dataclass in `webapp/db.py`:

```python
@dataclass
class PortfolioSummary:
    user_id: int
    user_name: str
    card_count: int
    total_purchase_price: float
    total_market_value: float
    unrealized_gain_loss: float
    gain_loss_pct: float
    bulk_count: int
    untracked_count: int
    sealed_count: int = 0
    total_sealed_value: float = 0.0
    total_cards_value: float = 0.0
```

Replace the `portfolio_summary()` function body:

```python
def portfolio_summary(user_id: int) -> PortfolioSummary:
    user = get_user(user_id)
    if not user:
        raise ValueError(f"unknown user_id {user_id}")
    cards = list_cards(user_id)
    card_items  = [c for c in cards if c.product_type == "card"]
    sealed_items = [c for c in cards if c.product_type != "card"]
    total_purchase    = sum(c.purchase_price or 0.0 for c in cards)
    total_market      = sum(c.current_market_price or 0.0 for c in cards)
    total_cards_value  = sum(c.current_market_price or 0.0 for c in card_items)
    total_sealed_value = sum(c.current_market_price or 0.0 for c in sealed_items)
    bulk     = sum(1 for c in card_items if (c.current_market_price or 0) < 5)
    untracked = sum(1 for c in cards if c.current_market_price is None)
    gain = total_market - total_purchase
    pct  = (gain / total_purchase * 100) if total_purchase > 0 else 0.0
    return PortfolioSummary(
        user_id=user_id, user_name=user.name,
        card_count=len(card_items),
        sealed_count=len(sealed_items),
        total_purchase_price=round(total_purchase, 2),
        total_market_value=round(total_market, 2),
        total_cards_value=round(total_cards_value, 2),
        total_sealed_value=round(total_sealed_value, 2),
        unrealized_gain_loss=round(gain, 2),
        gain_loss_pct=round(pct, 2),
        bulk_count=bulk,
        untracked_count=untracked,
    )
```

- [ ] **Step 6: Run tests — all 4 should pass**

```bash
cd ~/claude/CardApp/webapp
python -m pytest tests/test_sealed.py -v
```

Expected: `4 passed`

- [ ] **Step 7: Commit**

```bash
cd ~/claude/CardApp
git add webapp/db.py webapp/tests/__init__.py webapp/tests/test_sealed.py
git commit -m "feat: add product_type column and portfolio split for sealed products"
```

---

## Task 2: Sealed query types + eBay URL builder

**Files:**
- Modify: `pricing_engine.py`
- Modify: `webapp/tests/test_sealed.py`

- [ ] **Step 1: Write failing tests**

Append to `webapp/tests/test_sealed.py`:

```python
# ---- pricing_engine tests ----
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))
from pricing_engine import SealedProductQuery, build_ebay_sealed_url, is_relevant_sealed_title

def test_build_ebay_sealed_url_booster_box():
    q = SealedProductQuery(name='Scarlet & Violet 151', set_name='Scarlet & Violet 151', product_type='booster_box')
    url = build_ebay_sealed_url(q)
    assert 'LH_Sold=1' in url
    assert 'Booster+Box' in url or 'Booster Box' in url.replace('%20', ' ').replace('+', ' ')
    assert 'sealed' in url.lower()
    assert '-pack' in url.lower() or '-pack' in url

def test_build_ebay_sealed_url_booster_pack_no_pack_exclusion():
    q = SealedProductQuery(name='Scarlet & Violet 151', set_name='Scarlet & Violet 151', product_type='booster_pack')
    url = build_ebay_sealed_url(q)
    # Should NOT exclude -pack when searching for packs
    decoded = url.replace('%20', ' ').replace('+', ' ')
    assert '-pack' not in decoded

def test_build_ebay_sealed_url_etb():
    q = SealedProductQuery(name='Prismatic Evolutions', set_name='Prismatic Evolutions', product_type='etb')
    url = build_ebay_sealed_url(q)
    decoded = url.replace('%20', ' ').replace('+', ' ')
    assert 'Elite Trainer Box' in decoded

def test_is_relevant_sealed_title_filters_opened():
    q = SealedProductQuery(name='Scarlet & Violet 151', set_name='Scarlet & Violet 151', product_type='booster_box')
    assert not is_relevant_sealed_title('Pokemon 151 Booster Box OPENED empty', q)

def test_is_relevant_sealed_title_filters_lot():
    q = SealedProductQuery(name='Scarlet & Violet 151', set_name='Scarlet & Violet 151', product_type='booster_box')
    assert not is_relevant_sealed_title('Pokemon 151 Booster Box x3 lot', q)

def test_is_relevant_sealed_title_accepts_good_listing():
    q = SealedProductQuery(name='Scarlet & Violet 151', set_name='Scarlet & Violet 151', product_type='booster_box')
    assert is_relevant_sealed_title('Pokemon Scarlet & Violet 151 Booster Box Factory Sealed', q)
```

- [ ] **Step 2: Run to confirm failures**

```bash
cd ~/claude/CardApp/webapp
python -m pytest tests/test_sealed.py -k "sealed_url or relevant_sealed" -v 2>&1 | head -30
```

Expected: ImportError / 6 failures — `SealedProductQuery` doesn't exist yet.

- [ ] **Step 3: Add to `pricing_engine.py`**

After the existing `CardQuery` dataclass (around line 57), add:

```python
PRODUCT_TYPE_SEARCH_TERMS: dict[str, str] = {
    "booster_pack": "Booster Pack",
    "booster_box":  "Booster Box",
    "etb":          "Elite Trainer Box",
    "tin":          "Tin",
    "bundle":       "Bundle",
}


@dataclass
class SealedProductQuery:
    name: str
    set_name: str
    product_type: str   # one of PRODUCT_TYPE_SEARCH_TERMS keys
    language: str = "english"
```

After `build_ebay_sold_url` (around line 123), add:

```python
def build_ebay_sealed_url(query: SealedProductQuery) -> str:
    """Build eBay sold-listings URL for a sealed Pokemon product.

    Uses set_name as the primary search term. Excludes opened/resealed
    listings. For non-pack types also excludes loose individual packs.
    """
    product_term = PRODUCT_TYPE_SEARCH_TERMS.get(query.product_type, "")
    parts: list[str] = []
    if query.set_name:
        parts.append(query.set_name)
    elif query.name:
        parts.append(query.name)
    if product_term:
        parts.append(product_term)
    parts.append("sealed")
    parts += ["-opened", "-resealed", "-empty"]
    if query.product_type != "booster_pack":
        parts.append("-pack")   # avoid individual packs when searching for boxes/ETBs/tins
    if query.language.lower() == "japanese":
        parts.append("Japanese")
    keywords = " ".join(parts)
    params = {
        "_nkw": keywords,
        "LH_Sold": "1",
        "LH_Complete": "1",
        "_ipg": "240",
        "_sop": "13",
    }
    return f"https://www.ebay.com/sch/i.html?{urlencode(params)}"


def is_relevant_sealed_title(title: str, query: SealedProductQuery) -> bool:
    """Return True if this eBay listing title looks like a genuine sealed-product sale."""
    t = title.lower()
    if any(j in t for j in ("opened", "resealed", "empty box", "factory seconds")):
        return False
    if re.search(r"\b(lot of|x[2-9]|[2-9]x|\d+ packs)\b", t):
        return False
    search_text = query.set_name or query.name
    tokens = [w.lower() for w in re.findall(r"\w+", search_text) if len(w) > 2]
    if tokens and not any(tok in t for tok in tokens):
        return False
    return True
```

- [ ] **Step 4: Run tests — 6 should pass**

```bash
cd ~/claude/CardApp/webapp
python -m pytest tests/test_sealed.py -k "sealed_url or relevant_sealed" -v
```

Expected: `6 passed`

- [ ] **Step 5: Commit**

```bash
cd ~/claude/CardApp
git add pricing_engine.py webapp/tests/test_sealed.py
git commit -m "feat: add SealedProductQuery, build_ebay_sealed_url, is_relevant_sealed_title"
```

---

## Task 3: eBay sealed lookup

**Files:**
- Modify: `webapp/ebay_lookup.py`
- Modify: `webapp/tests/test_sealed.py`

- [ ] **Step 1: Write failing test**

Append to `webapp/tests/test_sealed.py`:

```python
# ---- ebay_lookup sealed test (import-level smoke test) ----
import inspect
import ebay_lookup

def test_lookup_sealed_recent_n_mean_exists():
    assert hasattr(ebay_lookup, 'lookup_sealed_recent_n_mean')
    sig = inspect.signature(ebay_lookup.lookup_sealed_recent_n_mean)
    params = list(sig.parameters)
    assert 'name' in params
    assert 'product_type' in params
```

- [ ] **Step 2: Run to confirm failure**

```bash
cd ~/claude/CardApp/webapp
python -m pytest tests/test_sealed.py::test_lookup_sealed_recent_n_mean_exists -v
```

Expected: `FAILED` — AttributeError.

- [ ] **Step 3: Add `lookup_sealed_recent_n_mean` to `webapp/ebay_lookup.py`**

At the top of `webapp/ebay_lookup.py`, update the import from `pricing_engine` to include the new symbols:

```python
from pricing_engine import (
    CardIdentity, CardQuery, build_ebay_sold_url,
    parse_ebay_sold_html, aggregate_sales,
    SealedProductQuery, build_ebay_sealed_url, is_relevant_sealed_title,
)
```

Then append this function at the end of the file:

```python
async def lookup_sealed_recent_n_mean(
    name: str,
    set_name: str = "",
    product_type: str = "booster_box",
    language: str = "english",
    n: int = 5,
    period_days: int = 90,
) -> Optional[EbayRecentMean]:
    """Fetch eBay sold listings for a sealed product; return mean of N most-recent.

    Mirrors lookup_recent_n_mean but uses SealedProductQuery + is_relevant_sealed_title
    instead of the card-specific query builder and title filter.
    """
    query = SealedProductQuery(
        name=name,
        set_name=set_name or name,
        product_type=product_type,
        language=language,
    )
    url = build_ebay_sealed_url(query)

    cached = _cache_get(url)
    if cached and "sales" in cached:
        sales = cached.get("sales", [])[:max(n * 2, 25)]
        sales_sorted = sorted(sales, key=lambda s: s.get("sold_date") or "", reverse=True)
        picked = sales_sorted[:n]
        prices = [float(s["price_usd"]) for s in picked if s.get("price_usd") is not None]
        if prices:
            mean = sum(prices) / len(prices)
            mid = sorted(prices)
            med = mid[len(mid) // 2] if len(mid) % 2 else (mid[len(mid) // 2 - 1] + mid[len(mid) // 2]) / 2
            return EbayRecentMean(
                mean_usd=round(mean, 2), median_usd=round(med, 2),
                sample_size=len(prices), requested_n=n,
                raw_sample_size=cached.get("raw_sample_size", len(prices)),
                period_days=period_days,
                low_usd=min(prices), high_usd=max(prices),
                sold_url=url, cached=True, is_graded=False,
                sales=picked,
            )

    try:
        async with httpx.AsyncClient(timeout=15.0,
                                     headers={"User-Agent": USER_AGENT}) as client:
            r = await client.get(url, follow_redirects=True)
    except httpx.HTTPError as e:
        log.warning("eBay sealed fetch failed: %s", e)
        return None

    if r.status_code != 200:
        log.warning("eBay sealed returned %s for %s", r.status_code, url)
        return None

    parsed = parse_ebay_sold_html(r.text, period_days=period_days)
    relevant = [s for s in parsed if is_relevant_sealed_title(s.title, query)]
    relevant.sort(key=lambda s: s.sold_date, reverse=True)
    sale_dicts = [_sale_to_dict(s) for s in relevant]

    payload = {
        "sales": sale_dicts,
        "raw_sample_size": len(parsed),
    }
    _cache_set(url, payload)

    picked = sale_dicts[:n]
    prices = [float(s["price_usd"]) for s in picked if s.get("price_usd") is not None]
    if not prices:
        return None

    mean = sum(prices) / len(prices)
    mid = sorted(prices)
    med = mid[len(mid) // 2] if len(mid) % 2 else (mid[len(mid) // 2 - 1] + mid[len(mid) // 2]) / 2

    return EbayRecentMean(
        mean_usd=round(mean, 2), median_usd=round(med, 2),
        sample_size=len(prices), requested_n=n,
        raw_sample_size=len(parsed),
        period_days=period_days,
        low_usd=min(prices), high_usd=max(prices),
        sold_url=url, cached=False, is_graded=False,
        sales=picked,
    )
```

- [ ] **Step 4: Run test — should pass**

```bash
cd ~/claude/CardApp/webapp
python -m pytest tests/test_sealed.py::test_lookup_sealed_recent_n_mean_exists -v
```

Expected: `1 passed`

- [ ] **Step 5: Commit**

```bash
cd ~/claude/CardApp
git add webapp/ebay_lookup.py webapp/tests/test_sealed.py
git commit -m "feat: add lookup_sealed_recent_n_mean to ebay_lookup"
```

---

## Task 4: PriceCharting sealed lookup

**Files:**
- Modify: `webapp/pricecharting_lookup.py`
- Modify: `webapp/tests/test_sealed.py`

- [ ] **Step 1: Write failing tests**

Append to `webapp/tests/test_sealed.py`:

```python
# ---- pricecharting sealed tests ----
import pricecharting_lookup

def test_sealed_product_slug_map_complete():
    for pt in ('booster_pack', 'booster_box', 'etb', 'tin', 'bundle'):
        assert pt in pricecharting_lookup.SEALED_PRODUCT_SLUGS

def test_lookup_sealed_price_exists():
    import inspect
    assert hasattr(pricecharting_lookup, 'lookup_sealed_price')
    sig = inspect.signature(pricecharting_lookup.lookup_sealed_price)
    params = list(sig.parameters)
    assert 'set_name' in params
    assert 'product_type' in params
```

- [ ] **Step 2: Run to confirm failures**

```bash
cd ~/claude/CardApp/webapp
python -m pytest tests/test_sealed.py -k "sealed_price_exists or slug_map" -v
```

Expected: 2 failures.

- [ ] **Step 3: Add `SEALED_PRODUCT_SLUGS` and `lookup_sealed_price` to `webapp/pricecharting_lookup.py`**

After the `GRADE_TO_PC_ROW` dict (around line 63), add:

```python
SEALED_PRODUCT_SLUGS: dict[str, str] = {
    "booster_pack": "booster-pack",
    "booster_box":  "booster-box",
    "etb":          "elite-trainer-box",
    "tin":          "tin",
    "bundle":       "booster-bundle",
}
```

Append at the end of the file:

```python
async def lookup_sealed_price(
    set_name: str,
    product_type: str,
    language: str = "english",
) -> Optional[PriceChartingResult]:
    """Return PriceCharting's 'Sealed' price for a sealed Pokemon product.

    URL pattern: /game/pokemon-<set-slug>/<product-slug>
    Reads the 'Sealed' price row (vs 'Loose' for opened product).
    Returns None if the product isn't in PriceCharting's catalogue.
    """
    slug = SEALED_PRODUCT_SLUGS.get(product_type)
    if not slug:
        log.warning("Unknown product_type for PriceCharting sealed lookup: %r", product_type)
        return None

    jp_prefix = "japanese-" if language.lower() == "japanese" else ""
    set_slug = _slug(set_name)
    url = f"{PC_BASE}/game/pokemon-{jp_prefix}{set_slug}/{slug}"

    cached = _cache_get(url)
    if cached is not None:
        return PriceChartingResult(
            url=url, grade_label="Sealed",
            price_usd=cached.get("Sealed"),
            all_prices=cached, cached=True,
        )

    async with httpx.AsyncClient(timeout=15.0,
                                  headers={"User-Agent": USER_AGENT}) as client:
        try:
            r = await client.get(url, follow_redirects=True)
        except httpx.HTTPError as e:
            log.warning("PriceCharting sealed fetch failed %s: %s", url, e)
            return None

    if r.status_code != 200:
        log.warning("PriceCharting sealed returned %s for %s", r.status_code, url)
        return None
    final_path = str(r.url.path)
    if final_path.startswith("/search-products") or final_path == "/":
        log.info("PriceCharting sealed not found: %s", url)
        return None

    prices = _parse_price_table(r.text)
    if not prices:
        return None
    _cache_set(url, prices)
    return PriceChartingResult(
        url=url, grade_label="Sealed",
        price_usd=prices.get("Sealed"),
        all_prices=prices, cached=False,
    )
```

- [ ] **Step 4: Run tests — 2 should pass**

```bash
cd ~/claude/CardApp/webapp
python -m pytest tests/test_sealed.py -k "sealed_price_exists or slug_map" -v
```

Expected: `2 passed`

- [ ] **Step 5: Commit**

```bash
cd ~/claude/CardApp
git add webapp/pricecharting_lookup.py webapp/tests/test_sealed.py
git commit -m "feat: add lookup_sealed_price and SEALED_PRODUCT_SLUGS to pricecharting_lookup"
```

---

## Task 5: API sealed price route

**Files:**
- Modify: `webapp/app.py`

- [ ] **Step 1: Add `product_type` to `RefreshPriceRequest`**

In `webapp/app.py`, the `RefreshPriceRequest` class starts at line 131. Change it to:

```python
class RefreshPriceRequest(BaseModel):
    name: str
    set_name: Optional[str] = None
    card_number: Optional[str] = None
    language: str = "english"
    variant: Optional[str] = None
    condition: str = "NM"
    is_graded: bool = False
    grade_company: Optional[str] = None
    grade: Optional[float] = None
    product_type: str = "card"   # "card" | "booster_pack" | "booster_box" | "etb" | "tin" | "bundle"
```

- [ ] **Step 2: Add `_refresh_sealed_price` helper**

Insert this function just before the `refresh_price` endpoint (around line 602 in `app.py`):

```python
async def _refresh_sealed_price(req: RefreshPriceRequest) -> dict:
    """Price a sealed product: eBay recent-N-mean primary, PriceCharting fallback."""
    search_name = req.set_name or req.name or ""

    # eBay primary
    try:
        ebay_result = await ebay_lookup.lookup_sealed_recent_n_mean(
            name=search_name,
            set_name=req.set_name or "",
            product_type=req.product_type,
            language=req.language,
            n=5,
            period_days=90,
        )
    except Exception as e:
        log.warning("eBay sealed lookup failed: %s", e)
        ebay_result = None

    if ebay_result and ebay_result.sample_size >= 1:
        cache_tag = "cached" if ebay_result.cached else "live"
        return {
            "estimated_price": ebay_result.mean_usd,
            "nm_baseline_usd": None,
            "multiplier": None,
            "source": (f"eBay sold mean · n={ebay_result.sample_size} "
                       f"of last {ebay_result.requested_n} ({cache_tag})"),
            "note": (f"Mean of {ebay_result.sample_size} most-recent eBay sold listings "
                     f"(range ${ebay_result.low_usd:.2f}-${ebay_result.high_usd:.2f}, "
                     f"median ${ebay_result.median_usd:.2f}, "
                     f"{ebay_result.period_days}-day window)."),
            "ebay_sold_url": ebay_result.sold_url,
            "ebay_sales": ebay_result.sales,
            "ebay_median_usd": ebay_result.median_usd,
            "ebay_sample_size": ebay_result.sample_size,
        }

    # PriceCharting fallback
    try:
        pc = await pricecharting_lookup.lookup_sealed_price(
            set_name=req.set_name or req.name or "",
            product_type=req.product_type,
            language=req.language,
        )
    except Exception as e:
        log.warning("PriceCharting sealed lookup failed: %s", e)
        pc = None

    if pc and pc.price_usd is not None:
        cache_tag = "cached" if pc.cached else "live"
        return {
            "estimated_price": round(pc.price_usd, 2),
            "nm_baseline_usd": None,
            "multiplier": None,
            "source": f"PriceCharting Sealed ({cache_tag})",
            "note": "Sealed market price from PriceCharting.",
            "pricecharting_url": pc.url,
        }

    return {
        "estimated_price": None,
        "source": "no data",
        "note": (f"No price data found for {req.product_type!r} — "
                 f"try eBay manually or check PriceCharting."),
    }
```

- [ ] **Step 3: Add early branch in `refresh_price` endpoint**

In `webapp/app.py`, the `refresh_price` function starts at line 602. Add the sealed branch as the first thing inside the function body (before the eBay recent-mean call):

In `webapp/app.py` at the `refresh_price` function (line ~602), insert two lines immediately after the docstring and before the `# ----- eBay 5-recent-mean` comment:

```python
    # Sealed products use a dedicated pricing path.
    if req.product_type and req.product_type != "card":
        return await _refresh_sealed_price(req)
```

The rest of the function body is unchanged — the eBay recent-mean block and all fallback paths remain exactly as they are.

- [ ] **Step 4: Add `product_type` to the identify response**

In `webapp/app.py`, find the `_identify_inner` return statement around line 1438 that includes `"identity": { ... }`. Add `product_type` to that dict:

```python
    return {
        "mode": "photo_ocr",
        "identity": {
            "name": result.identity.name,
            "set_name": result.identity.set_name,
            "card_number": result.identity.card_number,
            "language": result.identity.language,
            "variant": result.identity.variant,
            "product_type": getattr(result, 'product_type', 'card'),
        },
        "confidence": result.confidence,
        ...  # rest unchanged
```

- [ ] **Step 5: Verify app still starts**

```bash
cd ~/claude/CardApp/webapp
source .venv/bin/activate
python -c "import app; print('OK')"
```

Expected: `OK`

- [ ] **Step 6: Commit**

```bash
cd ~/claude/CardApp
git add webapp/app.py
git commit -m "feat: add sealed price route to /api/refresh-price"
```

---

## Task 6: OCR prompt update

**Files:**
- Modify: `ocr_engine.py`

- [ ] **Step 1: Add `product_type` to `IdentifyResult` dataclass**

In `ocr_engine.py`, the `IdentifyResult` dataclass starts at line 66. Add `product_type`:

```python
@dataclass
class IdentifyResult:
    identity: CardIdentity
    confidence: float
    source: str
    phash: str
    raw_llm_json: Optional[str] = None
    ocr_card_number: Optional[str] = None
    notes: list[str] = field(default_factory=list)
    product_type: str = "card"
```

- [ ] **Step 2: Add migration to `IdentifyCache` schema**

In `IdentifyCache.SCHEMA` (around line 118), add the `product_type` column to the `identifications` table:

```python
    SCHEMA = """
    CREATE TABLE IF NOT EXISTS identifications (
        phash       TEXT PRIMARY KEY,
        name        TEXT NOT NULL,
        set_name    TEXT NOT NULL,
        card_number TEXT NOT NULL,
        language    TEXT NOT NULL,
        variant     TEXT,
        product_type TEXT NOT NULL DEFAULT 'card',
        confidence  REAL NOT NULL,
        source      TEXT NOT NULL,
        raw_llm_json TEXT,
        ocr_card_number TEXT,
        ts          REAL NOT NULL
    );
    ...
```

In `IdentifyCache.__init__()`, add a migration after `executescript`:

```python
    def __init__(self, path=DEFAULT_CACHE_PATH):
        self.path = str(path)
        self._conn = sqlite3.connect(self.path)
        self._conn.executescript(self.SCHEMA)
        try:
            self._conn.execute("ALTER TABLE identifications ADD COLUMN product_type TEXT NOT NULL DEFAULT 'card'")
            self._conn.commit()
        except sqlite3.OperationalError:
            pass  # column already exists
        self._conn.commit()
```

- [ ] **Step 3: Update `IdentifyCache.store()` and `_row_to_result()`**

In `store()` (around line 179), update the INSERT to include `product_type`:

```python
    def store(self, result):
        i = result.identity
        now = time.time()
        self._conn.execute(
            "INSERT OR REPLACE INTO identifications "
            "(phash,name,set_name,card_number,language,variant,product_type,confidence,source,"
            "raw_llm_json,ocr_card_number,ts) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (result.phash, i.name, i.set_name, i.card_number, i.language, i.variant,
             getattr(result, 'product_type', 'card'),
             result.confidence, result.source, result.raw_llm_json,
             result.ocr_card_number, now),
        )
        ...  # rest of store() unchanged
```

Update `_row_to_result()` (around line 211) to read `product_type` from the row:

```python
    @staticmethod
    def _row_to_result(row, query_phash):
        (phash, name, set_name, card_number, language, variant, product_type,
         confidence, source, raw_llm_json, ocr_card_number, _ts) = row
        result = IdentifyResult(
            identity=CardIdentity(name=name, set_name=set_name, card_number=card_number,
                                   language=language, variant=variant),
            confidence=confidence,
            source="cache" if phash == query_phash else "cache (fuzzy)",
            phash=phash,
            raw_llm_json=raw_llm_json,
            ocr_card_number=ocr_card_number,
        )
        result.product_type = product_type or "card"
        return result
```

- [ ] **Step 4: Update `LLM_SYSTEM_PROMPT`**

Replace the `LLM_SYSTEM_PROMPT` variable in `ocr_engine.py` (starts at line 229). The new prompt adds `product_type` to the schema and a SEALED PRODUCT section:

```python
LLM_SYSTEM_PROMPT = """\
You are a Pokemon TCG card and sealed product identifier. Look at the image and return
metadata as STRICT JSON. Do not include any prose, markdown, or commentary —
only a single JSON object.

Schema:
{
  "product_type": "card | booster_pack | booster_box | etb | tin | bundle",
  "name": "Pokemon name as printed, OR the set/product name for sealed products",
  "set_name": "set/expansion name in full English",
  "card_number": "printed number with denominator (137/086) — empty string for sealed products",
  "language": "english | japanese",
  "variant": "Holo | Reverse Holo | Full Art | Illustration Rare | Special Art Rare | Special Illustration Rare | Hyper Rare | Alt Art | Rainbow Rare | Gold Secret Rare | 1st Edition | Promo | null — null for sealed products",
  "confidence": 0..1
}

SEALED PRODUCT RULES:
- If the image shows the outside of a booster box, ETB, tin, or sealed bundle — NOT a card —
  set product_type to the appropriate value: "booster_box", "etb", "tin", "bundle", "booster_pack".
- For sealed products: set name = the set/product name (e.g. "Scarlet & Violet 151"),
  card_number = "", variant = null.
- ETB = Elite Trainer Box. A booster_box contains 36 booster packs.
- If it's clearly a single Pokemon card (not sealed packaging), set product_type = "card".

Rules (for cards):
- If you can't read the card number, set it to "" (empty string).
- ASCII names ("Pokemon" not "Pokémon"). Use the apostrophe form for trainer-owned
  Pokemon ("Team Rocket's Moltres ex", "Sabrina's Alakazam", "N's Zekrom").

LANGUAGE DETECTION:
- "japanese" if the card text uses kana (ひらがな/カタカナ) or kanji (漢字).
  Vintage JP Gym/Neo/Base cards have names like "ナツメのフーディン" (Sabrina's
  Alakazam JP). The "Pokémon" trademark line at the bottom is also Japanese.
- "english" if the card text is all in Latin letters with English game text.
- When the card name is Japanese, ALWAYS translate it to its standard English
  name in the `name` field (e.g. "ナツメのフーディン" → "Sabrina's Alakazam").

1ST EDITION RULES (this matters — wrong call inflates price 2-5×):
- "1st Edition" is an ENGLISH-ONLY print designation marked by a small black
  badge/stamp reading "Edition 1" or "1st Edition" on the lower-left of the
  card's illustration window. It only appears on Wizards-era (Base→Neo) and
  some EX-era English cards.
- JAPANESE CARDS NEVER use "1st Edition". JP Gym/Neo/Base sets had a single
  print run with no equivalent stamp. If the card is Japanese, variant must
  NOT be "1st Edition" — use "Holo" (for Rare Holo) or null instead.
- Modern English cards (Scarlet & Violet era, 2023+) don't have 1st Edition.
- Only return "1st Edition" if you can VISUALLY confirm the stamp is on the
  card. When in doubt, return "Holo" / "Unlimited" / null — NEVER guess
  "1st Edition" just because the card looks vintage.

GRADED-SLAB LABELS (PSA, CGC, BGS, SGC): the label sits ABOVE the card image.
Read it carefully — it tells you the set and variant explicitly. Common
abbreviations and their meanings:
  FA = Full Art         SAR = Special Art Rare / Special Illustration Rare
  SIR = Special Illustration Rare        SR = Secret Rare
  HR = Hyper Rare       AR = Illustration Rare        UR = Ultra Rare
  GOLD = Gold Secret    RKT = Team Rocket             GLORY = Glory of
  TR = Team Rocket      ROCKET GANG = Glory of Team Rocket (SV10)
For Japanese slabs you'll often see "P.M. JAPANESE SV" + an abbreviated set:
  "GLORY/RKT. GANG" or "ROCKET GANG"  → set_name "Glory of Team Rocket"
  "CRIMSON HAZE"                       → set_name "Crimson Haze"
  "BATTLE PARTNERS"                    → set_name "Battle Partners"
  "TERASTAL FES EX" / "TERASTAL FESTIVAL" → set_name "Terastal Festival ex"
  "151"                                → set_name "Pokemon Card 151"
  "WHITE FLARE"                        → set_name "White Flare"
  "BLACK BOLT"                         → set_name "Black Bolt"
If the slab label says "FA" or "SAR" or "SIR", set variant to "Special Art Rare".
If "HR" or "HYPER", set variant to "Hyper Rare". If "GOLD" or "SECRET" (alone),
"Gold Secret Rare". If "SR" + a rainbow/iridescent look, "Special Art Rare".

- JSON only, no backticks, no prose.
"""
```

- [ ] **Step 5: Update `_build_identity_from_json` to return 4-tuple**

Replace `_build_identity_from_json` in `ocr_engine.py`:

```python
def _build_identity_from_json(raw: str):
    parsed = _parse_llm_json(raw)
    language = parsed["language"].strip().lower()
    variant = (parsed.get("variant") or None)
    product_type = (parsed.get("product_type") or "card").strip().lower()
    # Validate product_type — reject unknown values so stale prompts don't pollute DB.
    _valid_types = {"card", "booster_pack", "booster_box", "etb", "tin", "bundle"}
    if product_type not in _valid_types:
        product_type = "card"

    if variant and language == "japanese":
        v = variant.strip().lower()
        if v in ("1st edition", "first edition", "1st ed", "edition 1"):
            log.info("Dropping LLM variant=%r on JP card — Japanese sets "
                     "have no 1st Edition; defaulting to Holo.", variant)
            variant = "Holo"

    return (
        CardIdentity(
            name=parsed["name"].strip(),
            set_name=parsed.get("set_name", "").strip(),
            card_number=parsed.get("card_number", "").strip(),
            language=language,
            variant=variant,
        ),
        float(parsed.get("confidence", 0.5)),
        raw,
        product_type,
    )
```

- [ ] **Step 6: Update `_identify_with_anthropic` and `_identify_with_gemini` to pass 4-tuple through**

Both functions end with `return _build_identity_from_json(raw)` — they already pass the 4-tuple through since they just return the result. No change needed.

- [ ] **Step 7: Update `identify_card` to destructure 4-tuple and set `product_type`**

In `identify_card` (around line 588), the line:
```python
identity, confidence, raw_json = identify_with_llm(image_path, model=llm_model)
```

Change to:
```python
identity, confidence, raw_json, product_type = identify_with_llm(image_path, model=llm_model)
```

And when constructing `result` (around line 632):
```python
    result = IdentifyResult(
        identity=identity,
        confidence=confidence,
        source=source,
        phash=phash,
        raw_llm_json=raw_json,
        ocr_card_number=ocr_number,
        notes=notes,
        product_type=product_type,
    )
```

- [ ] **Step 8: Verify OCR module imports cleanly**

```bash
cd ~/claude/CardApp
source webapp/.venv/bin/activate
python -c "import ocr_engine; print('OK')"
```

Expected: `OK`

- [ ] **Step 9: Commit**

```bash
cd ~/claude/CardApp
git add ocr_engine.py
git commit -m "feat: OCR prompt recognizes sealed products, IdentifyResult carries product_type"
```

---

## Task 7: Frontend core — api.jsx + components.jsx

**Files:**
- Modify: `webapp/static/api.jsx`
- Modify: `webapp/static/components.jsx`

- [ ] **Step 1: Add `product_type` to `normalizeCard` and add `isSealedProduct`**

In `webapp/static/api.jsx`, after the closing `}` of the returned object in `normalizeCard` (around line 161 where `tags:` is), add `product_type`:

```javascript
      tags:      c.tags ?? [],
      product_type: c.product_type ?? 'card',
    };
```

After the `normalizeCard` function definition, add the helper (around line 165, before `return`):

```javascript
  function isSealedProduct(card) {
    return card && card.product_type && card.product_type !== 'card';
  }
```

Expose it in the returned `api` object. Find the `normalizeCard,` export line (around line 794) and add below it:

```javascript
    normalizeCard,
    isSealedProduct,
```

- [ ] **Step 2: Pass `product_type` in `refreshPrice` and `quotePrice`**

In `refreshPrice` (around line 328), add `product_type` to the body:

```javascript
      const body = {
        name:          card.name,
        set_name:      card.set ?? null,
        card_number:   card.code ?? null,
        language:      denormalizeLang(card.lang),
        condition:     card.condition ?? 'NM',
        variant:       card.variant ?? null,
        is_graded:     Boolean(card.is_graded ?? card.grade),
        grade_company: card.grader ?? null,
        grade:         card.grade ?? null,
        product_type:  card.product_type ?? 'card',
      };
```

Apply the same `product_type` addition to `quotePrice` (around line 302).

- [ ] **Step 3: Add `ProductTypeBadge` to `webapp/static/components.jsx`**

At the end of `components.jsx`, add:

```javascript
// ---------------------------------------------------------------------------
// ProductTypeBadge — colored chip for sealed product types
// ---------------------------------------------------------------------------
const PRODUCT_TYPE_META = {
  booster_pack: { label: 'Pack',         color: '#8b5cf6' },
  booster_box:  { label: 'Booster Box',  color: '#dc2626' },
  etb:          { label: 'ETB',          color: '#2563eb' },
  tin:          { label: 'Tin',          color: '#d97706' },
  bundle:       { label: 'Bundle',       color: '#16a34a' },
};

function ProductTypeBadge({ type, style = {} }) {
  const meta = PRODUCT_TYPE_META[type];
  if (!meta) return null;
  return (
    <span style={{
      background: meta.color,
      color: '#fff',
      borderRadius: 4,
      padding: '2px 6px',
      fontSize: 11,
      fontWeight: 600,
      letterSpacing: '0.02em',
      display: 'inline-block',
      ...style,
    }}>
      {meta.label}
    </span>
  );
}
```

- [ ] **Step 4: Verify app loads (no JS errors)**

```bash
cd ~/claude/CardApp/webapp
./run.sh &
sleep 3
curl -s http://localhost:8000/ | grep -c "<script"
kill %1
```

Expected: number > 0 (page loads).

- [ ] **Step 5: Commit**

```bash
cd ~/claude/CardApp
git add webapp/static/api.jsx webapp/static/components.jsx
git commit -m "feat: add product_type to normalizeCard, isSealedProduct helper, ProductTypeBadge"
```

---

## Task 8: Frontend Home + Browse

**Files:**
- Modify: `webapp/static/screens/Home.jsx`
- Modify: `webapp/static/screens/Browse.jsx`

- [ ] **Step 1: Add sealed split to `Home.jsx`**

In `webapp/static/screens/Home.jsx`, after line 83:
```javascript
  const totalUSD = ownedCards.reduce((s, c) => s + (Number(c.usd) || 0), 0);
```

Add:
```javascript
  const totalCardsUSD  = ownedCards.filter(c => !isSealedProduct(c)).reduce((s, c) => s + (Number(c.usd) || 0), 0);
  const totalSealedUSD = ownedCards.filter(isSealedProduct).reduce((s, c) => s + (Number(c.usd) || 0), 0);
```

`isSealedProduct` comes from `window.api.isSealedProduct`. You need to destructure it in the component. Find where other api functions are used (look for `refreshPrice` in the props) — `isSealedProduct` is available as `window.api?.isSealedProduct` or pass it through props if the app already passes `api` functions down. The simplest approach that matches the existing pattern: use `window.api?.isSealedProduct` directly:

```javascript
  const isSealedProduct = window.api?.isSealedProduct ?? (() => false);
```

Add that line before the `totalCardsUSD` line.

Then, after the `<Price usd={totalUSD} .../>` line (around line 275), add the split display:

```javascript
              <Price usd={totalUSD} currency={cur} size="xxl" decimals={0} />
            )}
            ...
          </div>
          {totalSealedUSD > 0 && !valueHidden && (
            <div className="row gap-2" style={{ marginTop: 2, fontSize: 12, color: 'var(--ink-3)' }}>
              <span className="mono">Cards {fmtUSD(totalCardsUSD, { decimals: 0 })}</span>
              <span style={{ opacity: 0.4 }}>·</span>
              <span className="mono">Sealed {fmtUSD(totalSealedUSD, { decimals: 0 })}</span>
            </div>
          )}
```

- [ ] **Step 2: Add `sealed` filter chip to `Browse.jsx`**

In `webapp/static/screens/Browse.jsx`, add `isSealedProduct` access at the top of `BrowseScreen`:

```javascript
  const isSealedProduct = window.api?.isSealedProduct ?? (() => false);
```

In the filter chips array (around line 142), add the `sealed` chip:

```javascript
        {[
          { id: 'all', label: 'All' },
          { id: 'foil', label: 'Foil only' },
          { id: 'jp', label: 'JP' },
          { id: 'graded', label: 'Graded' },
          { id: 'sealed', label: 'Sealed' },
          { id: 'wishlist', label: 'Wishlist', count: wishlistCount },
        ].map(f => (
```

In the filter logic (around line 73), add:

```javascript
    if (filter === 'sealed') list = list.filter(c => isSealedProduct(c));
```

In the card list rendering (around line 273 where `{c.code} · {c.set}` is displayed), update to show product type badge for sealed items:

```javascript
                    {isSealedProduct(c)
                      ? <ProductTypeBadge type={c.product_type} />
                      : `${c.code} · ${c.set} · ${c.lang} · ${c.condition}`}
```

- [ ] **Step 3: Start server and manually verify**

```bash
cd ~/claude/CardApp/webapp
./run.sh
```

Open `http://localhost:8000/` in a browser. Navigate to Browse — verify the "Sealed" chip appears. Navigate to Home — no sealed items yet so the split row should be hidden (only shows when `totalSealedUSD > 0`).

- [ ] **Step 4: Commit**

```bash
cd ~/claude/CardApp
git add webapp/static/screens/Home.jsx webapp/static/screens/Browse.jsx
git commit -m "feat: add sealed split to Home portfolio, Sealed filter chip to Browse"
```

---

## Task 9: Frontend Scan + Detail

**Files:**
- Modify: `webapp/static/screens/Scan.jsx`
- Modify: `webapp/static/screens/Detail.jsx`

- [ ] **Step 1: Hide condition/grade in `Scan.jsx` for sealed products**

In `webapp/static/screens/Scan.jsx`, find the `CardPreview` component (around line 485) where `condition`, `grader`, and `grade` state are managed.

At the top of `CardPreview`, add:
```javascript
  const isSealedProduct = window.api?.isSealedProduct ?? (() => false);
  const isSealed = isSealedProduct(card);
```

Find the condition selector (around line 700-710, where `NM/LP/MP/HP/DMG` buttons are rendered). Wrap it in:

```javascript
            {!isSealed && (
              <div>
                {/* condition selector — existing JSX unchanged */}
              </div>
            )}
```

Find the grader selector (around line 720, `Raw/PSA/CGC...` buttons). Wrap it similarly:

```javascript
            {!isSealed && (
              <div>
                {/* grader + grade selector — existing JSX unchanged */}
              </div>
            )}
```

Find where the card is added to the collection (around line 858-887, where `condition`, `grader`, `grade` are passed). Add `product_type`:

```javascript
            await addToCollection({
              ...card,
              condition,
              lang,
              grader: isGraded ? grader : null,
              grade:  isGraded ? grade  : null,
              is_graded: isGraded,
              product_type: card.product_type ?? 'card',
            });
```

- [ ] **Step 2: Update `Detail.jsx` to show type badge and hide irrelevant fields for sealed**

In `webapp/static/screens/Detail.jsx`, find where the card name/header is displayed. Add `isSealedProduct` access:

```javascript
  const isSealedProduct = window.api?.isSealedProduct ?? (() => false);
  const isSealed = isSealedProduct(card);
```

Find where the condition/grade section is rendered (search for `condition` or `is_graded` in Detail.jsx). Wrap those sections:

```javascript
            {!isSealed && (
              <div>
                {/* condition / grade display — existing JSX unchanged */}
              </div>
            )}
```

Find where the card set + number are displayed (usually near the name). Add a `ProductTypeBadge` for sealed items:

```javascript
            {isSealed
              ? <ProductTypeBadge type={card.product_type} style={{ marginLeft: 4 }} />
              : <span className="mono" style={{ color: 'var(--ink-3)', fontSize: 13 }}>{card.code}</span>
            }
```

- [ ] **Step 3: Verify end-to-end with a test sealed product**

Start the server:
```bash
cd ~/claude/CardApp/webapp
./run.sh
```

In the running app:
1. Navigate to Scan, type "Scarlet Violet 151 Booster Box" in the search bar. The OCR text path won't identify it as a sealed product (text path goes to card catalog, not OCR), so manually add a sealed product via the API to test the display:

```bash
curl -s -X POST http://localhost:8000/api/users/1/cards \
  -H "Content-Type: application/json" \
  -d '{"name":"Scarlet & Violet 151","set_name":"Scarlet & Violet 151","language":"english","product_type":"booster_box","current_market_price":110.00,"purchase_price":95.00}' | python3 -m json.tool
```

2. Navigate to Browse — verify the Sealed chip filters to show the box; verify the `ProductTypeBadge` renders instead of card number.
3. Navigate to Home — verify the `Cards $0 · Sealed $110` split line appears.
4. Tap the sealed product — verify Detail shows the type badge and hides condition/grade section.

- [ ] **Step 4: Test the price refresh API for the sealed product**

```bash
curl -s -X POST http://localhost:8000/api/refresh-price \
  -H "Content-Type: application/json" \
  -d '{"name":"Scarlet & Violet 151","set_name":"Scarlet & Violet 151","language":"english","product_type":"booster_box"}' | python3 -m json.tool
```

Expected: response with `estimated_price`, `source` containing "eBay" or "PriceCharting", and `note`. If eBay returns 403 (anti-bot), you'll see `"source": "no data"` — that's expected and correct.

- [ ] **Step 5: Commit**

```bash
cd ~/claude/CardApp
git add webapp/static/screens/Scan.jsx webapp/static/screens/Detail.jsx
git commit -m "feat: hide condition/grade for sealed products in Scan and Detail"
```

---

## Done ✓

All 9 tasks complete. Verify the full test suite:

```bash
cd ~/claude/CardApp/webapp
source .venv/bin/activate
python -m pytest tests/ -v
```

Expected: all tests pass.
