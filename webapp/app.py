"""
FastAPI backend for the Pokemon Trading webapp.

Endpoints:
  GET    /api/users
  GET    /api/users/{user_id}/portfolio
  GET    /api/users/{user_id}/cards
  POST   /api/users/{user_id}/cards
  PATCH  /api/cards/{card_id}
  DELETE /api/cards/{card_id}
  POST   /api/trade/propose
  POST   /api/identify              (multipart photo upload — uses ocr_engine if API key set)
  POST   /api/refresh-price/{card_id}  (uses pricing_engine if available)

Static frontend served from ./static/index.html at /.
"""
from __future__ import annotations

import sys
import os
import logging
from datetime import datetime
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)

# Make the engine modules importable from the parent directory.
sys.path.insert(0, str(Path(__file__).parent.parent))

from fastapi import FastAPI, HTTPException, UploadFile, File, Form
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

import db
from trade_proposer import propose_trades
import card_lookup
import pricecharting_lookup
import ebay_lookup

app = FastAPI(title="Pokemon Trading Claude")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
def _startup():
    db.init_db()
    # Start the daily refresh scheduler (7am CT). Safe if already running.
    try:
        from refresh_job import start_scheduler
        start_scheduler()
    except Exception as e:
        log.warning("scheduler did not start: %s", e)


@app.on_event("shutdown")
def _shutdown():
    try:
        from refresh_job import shutdown_scheduler
        shutdown_scheduler()
    except Exception:
        pass


@app.post("/api/refresh-prices/run-now")
async def run_refresh_now():
    """Manually trigger the daily refresh job. Useful for testing without
    waiting until 7am."""
    from refresh_job import refresh_all_cards
    summary = await refresh_all_cards()
    return summary


# ---------------------------------------------------------------------------
# Pydantic schemas (request bodies)
# ---------------------------------------------------------------------------

class CardCreate(BaseModel):
    name: str
    set_name: Optional[str] = None
    card_number: Optional[str] = None
    language: str = "english"
    variant: Optional[str] = None
    condition: str = "NM"
    is_graded: bool = False
    grade_company: Optional[str] = None
    grade: Optional[float] = None
    purchase_price: Optional[float] = None
    purchase_date: Optional[str] = None
    current_market_price: Optional[float] = None
    image_url: Optional[str] = None
    notes: Optional[str] = None


class CardPatch(BaseModel):
    name: Optional[str] = None
    set_name: Optional[str] = None
    card_number: Optional[str] = None
    language: Optional[str] = None
    variant: Optional[str] = None
    condition: Optional[str] = None
    is_graded: Optional[bool] = None
    grade_company: Optional[str] = None
    grade: Optional[float] = None
    purchase_price: Optional[float] = None
    purchase_date: Optional[str] = None
    current_market_price: Optional[float] = None
    image_url: Optional[str] = None
    notes: Optional[str] = None


class RefreshPriceRequest(BaseModel):
    name: str
    set_name: Optional[str] = None
    card_number: Optional[str] = None
    language: str = "english"
    variant: Optional[str] = None     # "1st Edition", "Unlimited", "Holo", etc.
    condition: str = "NM"
    is_graded: bool = False
    grade_company: Optional[str] = None
    grade: Optional[float] = None


class TradeRequest(BaseModel):
    user_id: int
    target_value: float
    tolerance: float = 5.0
    max_combo_size: int = 5
    max_results: int = 10
    exclude_card_ids: list[int] = []
    filter_tag_id: Optional[int] = None     # restrict combos to cards with this tag


class TagCreate(BaseModel):
    name: str
    color: str = "#94a3b8"
    is_trade_tag: bool = False


class TagPatch(BaseModel):
    name: Optional[str] = None
    color: Optional[str] = None
    is_trade_tag: Optional[bool] = None


class TagAttach(BaseModel):
    tag_id: int


# ---------------------------------------------------------------------------
# Users + portfolio
# ---------------------------------------------------------------------------

@app.get("/api/users")
def get_users():
    return [u.__dict__ for u in db.list_users()]


@app.get("/api/users/{user_id}/portfolio")
def get_portfolio(user_id: int):
    try:
        return db.portfolio_summary(user_id).__dict__
    except ValueError as e:
        raise HTTPException(404, str(e))


# ---------------------------------------------------------------------------
# Cards
# ---------------------------------------------------------------------------

@app.get("/api/users/{user_id}/cards")
def get_cards(user_id: int, tag_id: Optional[int] = None):
    cards = db.list_cards(user_id, tag_id=tag_id)
    return [_card_to_dict(c) for c in cards]


# --- Tags -------------------------------------------------------------------

@app.get("/api/users/{user_id}/tags")
def get_tags(user_id: int):
    return [_tag_to_dict(t) for t in db.list_tags(user_id)]


@app.post("/api/users/{user_id}/tags")
def create_tag(user_id: int, payload: TagCreate):
    if not db.get_user(user_id):
        raise HTTPException(404, f"unknown user_id {user_id}")
    try:
        tag = db.create_tag(user_id, payload.name, payload.color, payload.is_trade_tag)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return _tag_to_dict(tag)


@app.patch("/api/tags/{tag_id}")
def patch_tag(tag_id: int, payload: TagPatch):
    changes = {k: v for k, v in payload.dict().items() if v is not None}
    if not changes:
        raise HTTPException(400, "no fields provided")
    tag = db.update_tag(tag_id, **changes)
    if not tag:
        raise HTTPException(404, "tag not found")
    return _tag_to_dict(tag)


@app.delete("/api/tags/{tag_id}")
def delete_tag(tag_id: int):
    if not db.delete_tag(tag_id):
        raise HTTPException(404, "tag not found")
    return {"deleted": True, "tag_id": tag_id}


@app.post("/api/cards/{card_id}/tags")
def attach_tag(card_id: int, payload: TagAttach):
    if not db.get_card(card_id):
        raise HTTPException(404, "card not found")
    if not db.get_tag(payload.tag_id):
        raise HTTPException(404, "tag not found")
    db.add_tag_to_card(card_id, payload.tag_id)
    return _card_to_dict(db.get_card(card_id))


@app.delete("/api/cards/{card_id}/tags/{tag_id}")
def detach_tag(card_id: int, tag_id: int):
    if not db.remove_tag_from_card(card_id, tag_id):
        raise HTTPException(404, "tag not on card")
    return _card_to_dict(db.get_card(card_id))


@app.post("/api/users/{user_id}/cards")
def create_card(user_id: int, payload: CardCreate):
    if not db.get_user(user_id):
        raise HTTPException(404, f"unknown user_id {user_id}")
    card = db.Card(id=None, user_id=user_id, **payload.dict())
    saved = db.create_card(card)
    return _card_to_dict(saved)


@app.patch("/api/cards/{card_id}")
def patch_card(card_id: int, payload: CardPatch):
    changes = {k: v for k, v in payload.dict().items() if v is not None}
    if not changes:
        raise HTTPException(400, "no fields provided")
    card = db.update_card(card_id, **changes)
    if not card:
        raise HTTPException(404, "card not found")
    return _card_to_dict(card)


@app.delete("/api/cards/{card_id}")
def delete_card(card_id: int):
    if not db.delete_card(card_id):
        raise HTTPException(404, "card not found")
    return {"deleted": True, "card_id": card_id}


@app.post("/api/cards/{card_id}/photo")
async def upload_card_photo(card_id: int, photo: UploadFile = File(...)):
    """Save a user-uploaded photo of this physical card. Stored under
    uploads/, served via /uploads/<filename>. Replaces any prior upload."""
    card = db.get_card(card_id)
    if not card:
        raise HTTPException(404, "card not found")

    uploads_dir = Path(__file__).parent / "uploads"
    uploads_dir.mkdir(exist_ok=True)
    suffix = Path(photo.filename or "card.jpg").suffix or ".jpg"
    if suffix.lower() not in {".jpg", ".jpeg", ".png", ".webp", ".heic"}:
        raise HTTPException(400, f"unsupported file type {suffix}")
    fname = f"card_{card_id}_{int(datetime.utcnow().timestamp() * 1000)}{suffix}"
    path = uploads_dir / fname
    path.write_bytes(await photo.read())

    # Public URL (relative): /uploads/<fname>. The static mount at /uploads
    # serves the file from the same directory.
    public_url = f"/uploads/{fname}"
    db.update_card(card_id, photo_path=public_url)
    return _card_to_dict(db.get_card(card_id))


@app.delete("/api/cards/{card_id}/photo")
def delete_card_photo(card_id: int):
    """Remove the user-uploaded photo (if any). Catalogue image_url stays."""
    card = db.get_card(card_id)
    if not card:
        raise HTTPException(404, "card not found")
    if card.photo_path:
        # Best-effort delete the file too; ignore if already gone.
        try:
            local = Path(__file__).parent / card.photo_path.lstrip("/")
            if local.exists() and local.is_file():
                local.unlink()
        except OSError:
            pass
    db.update_card(card_id, photo_path=None)
    return _card_to_dict(db.get_card(card_id))


# ---------------------------------------------------------------------------
# Trade proposer
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Price refresh — apply condition/grade multipliers to NM baseline
# ---------------------------------------------------------------------------
#
# Multipliers are industry-rough averages drawn from cross-referenced
# TCGplayer + eBay sold data. They're estimates, not authoritative quotes.
# For a real eBay-sourced quote, use the existing pricing_engine module.

RAW_CONDITION_MULTIPLIERS = {
    "NM":  1.00,
    "LP":  0.85,
    "MP":  0.65,
    "HP":  0.45,
    "DMG": 0.25,
}


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

# Grader-specific multiplier-against-NM-raw, used as a fallback when
# PriceCharting has no entry for the card. The grading services have very
# different market premiums — PSA commands the most, BGS Black Label often
# exceeds PSA 10, CGC and SGC trade at noticeable discounts. These factors
# are industry rule-of-thumb averages; precision comes from PriceCharting
# (or eventually live eBay sold scraping).
GRADED_MULTIPLIERS = {
    # PSA — the dominant grader, sets the price ceiling for non-Black-Label
    ("PSA",  10):    4.00,
    ("PSA",  9.5):   2.60,
    ("PSA",  9):     1.60,
    ("PSA",  8):     0.90,
    ("PSA",  7):     0.55,
    # CGC — generally trades at ~70-80% of PSA at the same grade, but their
    # "Pristine 10" is its own product (handled by PriceCharting if available)
    ("CGC",  10):    3.00,    # ~75% of PSA 10
    ("CGC",  9.5):   2.10,    # ~80% of PSA 9.5
    ("CGC",  9):     1.20,    # ~75% of PSA 9
    ("CGC",  8):     0.70,
    ("CGC",  7):     0.45,
    # BGS — strict graders; BGS 10 is rare, BGS 10 Black Label commands huge
    # premium. Sub-10 grades trade above CGC but below PSA on most cards.
    ("BGS",  10):    4.50,
    ("BGS",  9.5):   2.90,    # close to PSA 9.5 — BGS 9.5 is well-respected
    ("BGS",  9):     1.50,
    ("BGS",  8):     0.80,
    # SGC — smaller market, trades at a discount everywhere
    ("SGC",  10):    3.40,
    ("SGC",  9.5):   2.20,
    ("SGC",  9):     1.30,
    ("SGC",  8):     0.70,
}


@app.post("/api/refresh-price")
async def refresh_price(req: RefreshPriceRequest):
    """Get a market-price estimate for a given condition or grade.

    Routing:
      - Graded → PriceCharting (real per-grade market values). Falls back
        to multiplier estimate if PC doesn't carry the card.
      - Raw    → TCGplayer baseline (Pokemon TCG API for EN, TCGdex/Cardmarket
        for JP), then multiplied by condition factor.
    """
    # ----- GRADED path: PriceCharting --------------------------------------
    if req.is_graded and req.grade_company and req.grade is not None:
        if not req.name:
            raise HTTPException(400, "card name required for graded lookup")
        try:
            pc = await pricecharting_lookup.lookup_graded_price(
                req.name, req.set_name or "", req.card_number or "",
                req.language, req.grade_company, req.grade,
                variant=req.variant,
            )
        except Exception as e:
            log.warning("PriceCharting lookup error: %s", e)
            pc = None

        if pc and pc.price_usd is not None:
            cached_label = "cached" if pc.cached else "live"
            return {
                "estimated_price": round(pc.price_usd, 2),
                "nm_baseline_usd": None,
                "multiplier": None,
                "source": (f"{req.grade_company} {req.grade} from PriceCharting "
                           f"({pc.grade_label}, {cached_label})"),
                "note": (f"Live graded market price. PriceCharting tracks "
                         f"grader-specific 10s + cross-service averages for "
                         f"sub-10 grades."),
                "pricecharting_url": pc.url,
            }
        # PC didn't have it — fall through to multiplier estimate so the
        # user always gets a number.

    # ----- baseline lookup for raw or graded-fallback ----------------------
    nm_price: Optional[float] = None
    baseline_label = "TCGplayer (EN)"
    extra_note = None

    # PriceCharting "Ungraded" — best for OLD cards where 1st Edition is its
    # own product (different page slug), and for JP cards where Cardmarket
    # is consistently stale. Also try for any card with variant context.
    if req.name and (req.language.lower() == "japanese" or _is_old_variant(req.variant)):
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

    if req.is_graded and req.grade_company and req.grade is not None:
        key = (req.grade_company.upper(), float(req.grade))
        mult = GRADED_MULTIPLIERS.get(key)
        if mult is None:
            raise HTTPException(400,
                f"unsupported grade {req.grade_company} {req.grade}. "
                f"Try a standard grade like PSA 10, CGC 9, BGS 9.5.")
        source = (f"{req.grade_company} {req.grade} estimate ({mult:.2f}× NM "
                  f"${nm_price:.2f} from {baseline_label}) — PriceCharting had no "
                  f"entry for this card")
    else:
        mult = RAW_CONDITION_MULTIPLIERS.get(req.condition.upper(), 1.0)
        source = f"{req.condition} estimate ({mult:.2f}× NM ${nm_price:.2f} from {baseline_label})"

    estimated = round(nm_price * mult, 2)
    note = extra_note
    if note is None:
        if baseline_label.startswith("Cardmarket"):
            note = "JP price from Cardmarket EUR, converted at ~1.10 USD/EUR."
        elif baseline_label.startswith("PriceCharting Ungraded"):
            note = "JP raw price from PriceCharting Ungraded — compiles real sold listings."
    return {
        "estimated_price": estimated,
        "nm_baseline_usd": nm_price,
        "multiplier": mult,
        "source": source,
        "note": note,
    }


# ---------------------------------------------------------------------------
# Card search (Pokemon TCG API typeahead)
# ---------------------------------------------------------------------------

@app.get("/api/cards/search")
async def cards_search(q: str, limit: int = 20):
    if len(q.strip()) < 2:
        return {"results": []}
    results = await card_lookup.search_cards(q, limit=limit)
    return {"results": [r.to_dict() for r in results]}


@app.delete("/api/identify-cache")
def clear_identify_cache():
    """Clear the OCR pHash + identity cache. Useful when an early identification
    was wrong (e.g. a JP card got cached with EN data) and you want fresh
    lookups on the next photo upload."""
    try:
        import ocr_engine
        cache_path = ocr_engine.DEFAULT_CACHE_PATH
        if cache_path.exists():
            cache_path.unlink()
            return {"cleared": True, "path": str(cache_path)}
        return {"cleared": False, "reason": "no cache file existed"}
    except Exception as e:
        raise HTTPException(500, f"could not clear cache: {e}")


@app.post("/api/trade/propose")
def trade_propose(req: TradeRequest):
    # Restrict the candidate pool by tag if requested. If filter_tag_id is
    # absent, default to the user's "for trade" tag (the seeded one) when
    # available — that's the natural intent of the trade flow.
    filter_tag_id = req.filter_tag_id
    if filter_tag_id is None:
        for t in db.list_tags(req.user_id):
            if t.is_trade_tag:
                filter_tag_id = t.id
                break

    cards = db.list_cards(req.user_id, tag_id=filter_tag_id)
    options = propose_trades(
        collection=cards,
        target_value=req.target_value,
        tolerance=req.tolerance,
        max_combo_size=req.max_combo_size,
        max_results=req.max_results,
        exclude_card_ids=set(req.exclude_card_ids),
    )
    priced_count = sum(1 for c in cards if c.current_market_price is not None)
    return {
        "target_value": req.target_value,
        "tolerance": req.tolerance,
        "user_id": req.user_id,
        "filter_tag_id": filter_tag_id,
        "candidate_count": len(cards),
        "priced_candidate_count": priced_count,
        "options": [o.to_dict() for o in options],
    }


# ---------------------------------------------------------------------------
# Photo identify (requires ANTHROPIC_API_KEY)
# ---------------------------------------------------------------------------

@app.post("/api/identify")
async def identify(photo: UploadFile = File(...)):
    """Upload a photo, return the LLM's identification + a market-price hint."""
    if not (os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("GOOGLE_API_KEY")):
        raise HTTPException(503,
            "Photo identification needs ANTHROPIC_API_KEY or GOOGLE_API_KEY in the server's env. "
            "Free Gemini key: https://aistudio.google.com/apikey · "
            "Restart with: GOOGLE_API_KEY=AI... uvicorn app:app --port 8000")

    try:
        import ocr_engine
    except ImportError as e:
        raise HTTPException(503, f"ocr_engine not available: {e}")

    # Save the upload to a temp path
    uploads_dir = Path(__file__).parent / "uploads"
    uploads_dir.mkdir(exist_ok=True)
    suffix = Path(photo.filename or "card.jpg").suffix or ".jpg"
    tmp_path = uploads_dir / f"upload_{int(datetime.utcnow().timestamp() * 1000)}{suffix}"
    tmp_path.write_bytes(await photo.read())

    try:
        result = ocr_engine.identify_card(str(tmp_path))
    except Exception as e:
        raise HTTPException(500, f"identification failed: {e}")

    # Look up market price + image. JP cards go to TCGdex (proper JP imagery
    # and Cardmarket EUR pricing); EN goes to Pokemon TCG API. Best-effort —
    # if the card isn't in either catalogue we still return the LLM's identity.
    market_price: Optional[float] = None
    image_url: Optional[str] = None
    try:
        hit = await card_lookup.lookup_card(
            name=result.identity.name,
            set_name=result.identity.set_name,
            card_number=result.identity.card_number,
            language=result.identity.language,
        )
        if hit:
            market_price = hit.market_price
            image_url = hit.image_url
    except Exception:
        pass  # Don't let a price-lookup failure sink the whole identify call.

    return {
        "identity": {
            "name": result.identity.name,
            "set_name": result.identity.set_name,
            "card_number": result.identity.card_number,
            "language": result.identity.language,
            "variant": result.identity.variant,
        },
        "confidence": result.confidence,
        "source": result.source,
        "phash": result.phash,
        "ocr_card_number": result.ocr_card_number,
        "notes": result.notes,
        "photo_path": str(tmp_path.relative_to(Path(__file__).parent)),
        "market_price": market_price,
        "image_url": image_url,
    }


# ---------------------------------------------------------------------------
# Static frontend
# ---------------------------------------------------------------------------

STATIC_DIR = Path(__file__).parent / "static"
STATIC_DIR.mkdir(exist_ok=True)
UPLOADS_DIR = Path(__file__).parent / "uploads"
UPLOADS_DIR.mkdir(exist_ok=True)

app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
app.mount("/uploads", StaticFiles(directory=str(UPLOADS_DIR)), name="uploads")


@app.get("/")
def index():
    return FileResponse(str(STATIC_DIR / "index.html"))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _card_to_dict(c: db.Card) -> dict:
    d = c.__dict__.copy()
    d["gain_loss"] = c.gain_loss()
    d["gain_loss_pct"] = c.gain_loss_pct()
    d["tags"] = [_tag_to_dict(t) for t in (c.tags or [])]
    return d


def _tag_to_dict(t: db.Tag) -> dict:
    return {
        "id": t.id, "user_id": t.user_id, "name": t.name,
        "color": t.color, "is_trade_tag": t.is_trade_tag,
        "card_count": t.card_count,
    }
