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

import csv
import io
import sys
import os
import re
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)

from fastapi import BackgroundTasks, Depends, FastAPI, HTTPException, UploadFile, File, Form
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from dotenv import load_dotenv
load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), "../.env"))

try:
    import db_postgres as db
except Exception:
    import db  # local SQLite fallback during development

from supabase_storage import is_configured as _storage_configured, get_signed_url as _sign_photo
from auth import get_current_account, get_current_account_optional, get_current_profile, get_current_profile_optional

def _resolve_photo(d: dict) -> dict:
    """Rewrite Supabase storage paths to signed URLs in serialized card dicts."""
    path = d.get("photo_path")
    if path and _storage_configured() and not path.startswith("/uploads") and not path.startswith("http"):
        try:
            d["photo_path"] = _sign_photo(path)
        except Exception:
            pass
    return d

from trade_proposer import propose_trades
import card_lookup
import pricecharting_lookup
import ebay_lookup
import raw_price_resolver

app = FastAPI(title="Pokemon Trading Claude")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
def _startup():
    try:
        db.init_db()
    except Exception as e:
        log.warning("database initialization failed (continuing with static serve): %s", e)
    try:
        if hasattr(db, "run_migrations"):
            db.run_migrations()
    except Exception as e:
        log.warning("schema migration failed: %s", e)
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


@app.get("/api/health")
def health_check():
    """Lightweight liveness probe. Returns DB card count and scheduler state."""
    info: dict = {"ok": True, "ts": datetime.now(timezone.utc).isoformat()}
    try:
        with db.connect() as conn:
            row = conn.execute("SELECT COUNT(*) AS n FROM cards").fetchone()
            info["cards"] = row["n"] if row else 0
    except Exception as e:
        info["db_error"] = str(e)
    try:
        from refresh_job import _scheduler
        info["scheduler"] = "running" if (_scheduler and _scheduler.running) else "stopped"
    except Exception:
        info["scheduler"] = "unknown"
    return info


@app.post("/api/refresh-prices/run-now")
async def run_refresh_now(account: dict = Depends(get_current_account)):
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
    product_type: str = "card"
    # Optional initial tags, applied after row creation. Same semantics as
    # CardPatch.tags — auto-create missing tags on the user's tag list.
    tags: Optional[list[str]] = None


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
    photo_path: Optional[str] = None
    product_type: Optional[str] = None
    # When provided, this REPLACES the card's tag set. Names are matched
    # case-insensitively against the user's existing tags; new names are
    # auto-created. Pass [] to clear all tags.
    tags: Optional[list[str]] = None


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
    product_type: str = "card"


class SoldListingsRequest(BaseModel):
    name: str
    set_name: Optional[str] = None
    card_number: Optional[str] = None
    language: str = "english"
    variant: Optional[str] = None
    condition: str = "NM"
    is_graded: bool = False
    grade_company: Optional[str] = None
    grade: Optional[float] = None
    period_days: int = 60
    max_listings: int = 25


class TradeRequest(BaseModel):
    user_id: int
    target_value: float
    tolerance: float = 5.0
    max_combo_size: int = 5
    max_results: int = 10
    exclude_card_ids: list[int] = []
    filter_tag_id: Optional[int] = None     # restrict combos to cards with this tag


class IdentifyResponse(BaseModel):
    """Response schema for /api/identify. Centralised so callers can import
    the shape for type-checking and tests."""
    mode: str
    identity: Optional[dict] = None
    market_price: Optional[float] = None
    image_url: Optional[str] = None
    candidates: list = []
    candidate_count: int = 0
    product_type: str = "card"


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

FREE_SCAN_LIMIT = 20

@app.get("/api/account")
def get_account_info(account: dict = Depends(get_current_account)):
    """Return current account info (plan, trial status) + profiles + scan usage."""
    profiles = db.list_profiles(account["id"])
    month = datetime.now(timezone.utc).strftime("%Y-%m")
    scan_used = db.get_scan_count(account["id"], month)
    is_pro = account["plan"] == "pro" or (
        account["trial_ends_at"] is not None
        and account["trial_ends_at"] > datetime.now(timezone.utc)
    )
    return {
        **account,
        "profiles": profiles,
        "scan_used": scan_used,
        "scan_limit": None if is_pro else FREE_SCAN_LIMIT,
        "is_pro": is_pro,
    }


@app.get("/api/profiles")
def list_profiles(account: dict = Depends(get_current_account)):
    return db.list_profiles(account["id"])


@app.post("/api/profiles")
def create_profile(payload: dict, account: dict = Depends(get_current_account)):
    FREE_PROFILE_LIMIT = 1
    is_paid = account.get("plan") == "pro"
    count = db.count_profiles(account["id"])
    if not is_paid and count >= FREE_PROFILE_LIMIT:
        raise HTTPException(status_code=402, detail="Upgrade to add more profiles")
    name = str(payload.get("name", "")).strip()
    if not name:
        raise HTTPException(status_code=400, detail="name is required")
    color = payload.get("avatar_color", "#34d399")
    return db.create_profile(account["id"], name, color)


@app.patch("/api/profiles/trade-mode")
def set_trade_mode(payload: dict, profile: dict = Depends(get_current_profile)):
    enabled = bool(payload.get("enabled", False))
    db.set_trade_mode(profile["id"], enabled)
    return {"id": profile["id"], "trade_mode": enabled}


@app.get("/api/public/{profile_id}")
def get_public_profile(profile_id: int):
    """No auth — returns a profile's want list for trade show sharing."""
    data = db.get_public_profile(profile_id)
    if not data:
        raise HTTPException(status_code=404, detail="Profile not found or not in trade show mode")
    return data


@app.get("/api/profile/alerts")
def get_price_alerts(profile: dict = Depends(get_current_profile)):
    """Return cards whose current price has hit or dropped below the user's alert price."""
    return db.get_triggered_alerts(profile["id"])


@app.get("/api/profile/export")
def export_collection(profile: dict = Depends(get_current_profile)):
    """Download the current profile's full collection as CSV."""
    cards = db.list_cards(profile["id"])
    profile_name = profile.get("name", "collection")
    date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    filename = f"pokecollect-{profile_name}-{date_str}.csv".replace(" ", "-")

    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow([
        "Name", "Set", "Number", "Language", "Condition",
        "Graded", "Grader", "Grade",
        "Current Price (USD)", "Purchase Price (USD)", "Purchase Date", "Gain/Loss (%)",
        "Notes", "Tags", "Image URL", "Added",
    ])
    for c in cards:
        tags = ", ".join(t if isinstance(t, str) else (t.get("name") or "") for t in (c.tags or []))
        gain_loss = ""
        if c.current_market_price and c.purchase_price and c.purchase_price > 0:
            gain_loss = f"{((c.current_market_price - c.purchase_price) / c.purchase_price * 100):.1f}%"
        writer.writerow([
            c.name or "",
            c.set_name or "",
            c.card_code or "",
            "JP" if c.language == "japanese" else "EN",
            c.condition or "NM",
            "Yes" if c.is_graded else "No",
            c.grade_company or "",
            c.grade or "",
            f"{c.current_market_price:.2f}" if c.current_market_price else "",
            f"{c.purchase_price:.2f}" if c.purchase_price else "",
            c.purchase_date or "",
            gain_loss,
            c.notes or "",
            tags,
            c.image_url or "",
            c.created_at.strftime("%Y-%m-%d") if c.created_at else "",
        ])

    buf.seek(0)
    return StreamingResponse(
        iter([buf.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@app.get("/api/users")
def get_users(account: dict | None = Depends(get_current_account_optional)):
    if account:
        return db.list_profiles(account["id"])
    return [u.__dict__ for u in db.list_users()]  # unauthenticated fallback (local dev)


@app.get("/api/users/{user_id}/portfolio")
def get_portfolio(user_id: int, account: dict = Depends(get_current_account)):
    try:
        return db.portfolio_summary(user_id).__dict__
    except ValueError as e:
        raise HTTPException(404, str(e))


@app.get("/api/users/{user_id}/portfolio-history")
def get_portfolio_history(user_id: int, days: int = 365,
                           account: dict = Depends(get_current_account)):
    """Daily portfolio value series. Returns [{date, value}] oldest-first."""
    return {"points": db.portfolio_history(user_id, days=min(days, 1095))}


# ---------------------------------------------------------------------------
# Cards
# ---------------------------------------------------------------------------

@app.get("/api/users/{user_id}/cards")
def get_cards(user_id: int, tag_id: Optional[int] = None, account: dict = Depends(get_current_account)):
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


async def _auto_price_task(card_id: int, name: str, set_name: str, card_number: str,
                           language: str, variant: Optional[str],
                           is_graded: bool, grade_company: Optional[str],
                           grade: Optional[float], condition: str) -> None:
    """Fire-and-forget: resolve the best price (and cover image) for a newly-added card."""
    try:
        import pricecharting_lookup as pc
        from refresh_job import _price_for_card
        card = db.get_card(card_id)
        if not card:
            return
        price = await _price_for_card(card)
        if price is None:
            return
        updates: dict = {"current_market_price": price}
        # If the card has no image yet (or only a generic TCG API scan), check
        # whether PriceCharting has a cover photo — useful for pattern variants
        # like "Master Ball" whose PC page has a photo that looks different
        # from the plain holofoil scan in the Pokemon TCG API.
        if not is_graded and variant:
            try:
                pc_result = await pc.lookup_raw_price(
                    name, set_name, card_number, language=language, variant=variant
                )
                if pc_result and pc_result.image_url_large:
                    current = db.get_card(card_id)
                    if current and not current.image_url:
                        updates["image_url"] = pc_result.image_url_large
            except Exception:
                pass
        db.update_card(card_id, **updates)
        db.log_price(card_id, price, source="auto_refresh")
        log.info("auto-priced card %s (%s) → $%.2f", card_id, name, price)
    except Exception as e:
        log.warning("auto_price_task failed for card %s: %s", card_id, e)


async def _backfill_history_task(card_id: int, name: str, set_name: str, card_number: str,
                                  language: str, variant: Optional[str], product_type: str) -> None:
    """Fire-and-forget async background task: seed price_history from PriceCharting chart data."""
    import time
    import pricecharting_lookup as pc
    from price_history_refresh import refresh_one

    cutoff_ms = time.time() * 1000 - 1095 * 86400_000  # 3 years back
    try:
        if product_type != "card":
            await refresh_one(card_id, name, cutoff_ms,
                lambda: pc.fetch_sealed_chart_history(name, set_name, product_type, language=language))
        else:
            await refresh_one(card_id, name, cutoff_ms,
                lambda: pc.fetch_chart_history(name, set_name, card_number, language=language, variant=variant))
    except Exception as e:
        log.debug("background history backfill for card %s failed: %s", card_id, e)


@app.post("/api/users/{user_id}/cards")
def create_card(user_id: int, payload: CardCreate, background_tasks: BackgroundTasks,
                account: dict = Depends(get_current_account)):
    if not db.get_user(user_id):
        raise HTTPException(404, f"unknown user_id {user_id}")
    # db.Card has no `tags` column — that's the join table. Strip before
    # constructing the row, then sync tags after the row exists.
    body = payload.dict()
    tag_names = body.pop("tags", None)
    card = db.Card(id=None, user_id=user_id, **body)
    saved = db.create_card(card)
    if tag_names:
        _sync_card_tags(saved, tag_names)
        saved = db.get_card(saved.id)
    # Seed price history with the card's initial market price (if any) so
    # the chart has an anchor point on day one.
    if saved.id and saved.current_market_price is not None:
        try:
            db.log_price(saved.id, saved.current_market_price, source="create")
        except Exception as e:
            log.warning("price_history log on create failed: %s", e)
    # Auto-price: resolve the best market price in the background so the card
    # doesn't sit at the stale TCGplayer search-result price (often wrong for
    # pattern variants like "Master Ball" or JP-only cards).
    if saved.id:
        background_tasks.add_task(
            _auto_price_task,
            saved.id, saved.name or "", saved.set_name or "",
            saved.card_number or "", saved.language or "english",
            saved.variant, bool(saved.is_graded),
            saved.grade_company, float(saved.grade) if saved.grade else None,
            saved.condition or "NM",
        )
    # Backfill historical price data from PriceCharting in the background.
    # Skip graded cards — PriceCharting chart data is only for raw/ungraded.
    if saved.id and not saved.is_graded:
        background_tasks.add_task(
            _backfill_history_task,
            saved.id, saved.name or "", saved.set_name or "",
            saved.card_number or "", saved.language or "english",
            saved.variant, saved.product_type or "card",
        )
    return _card_to_dict(saved)


@app.patch("/api/cards/{card_id}")
def patch_card(card_id: int, payload: CardPatch, account: dict = Depends(get_current_account)):
    # Use exclude_unset so the client can explicitly null a field (e.g.
    # "reset image_url to clear bad art that got attached during lookup").
    # Pre-exclude_unset behavior dropped every None, so it was impossible to
    # clear image_url / current_market_price / grade_company via PATCH.
    changes = payload.dict(exclude_unset=True)
    if not changes:
        raise HTTPException(400, "no fields provided")

    # Tags are stored in their own table (cards ↔ tags ↔ card_tags), so we
    # pull them out before update_card. The list is treated as the desired
    # full set: existing tags not in the list are detached, new names are
    # auto-created on this user's tag list.
    tag_names = changes.pop("tags", None)

    if changes:
        card = db.update_card(card_id, **changes)
        if not card:
            raise HTTPException(404, "card not found")
        # If this PATCH set a new market price, append a price_history row.
        # The frontend's refresh-price flow lands here, so this is the
        # single place we log: keep it idempotent + cheap.
        if "current_market_price" in changes and changes["current_market_price"] is not None:
            try:
                db.log_price(card_id, float(changes["current_market_price"]),
                             source="patch")
            except Exception as e:
                log.warning("price_history log on patch failed: %s", e)
    else:
        card = db.get_card(card_id)
        if not card:
            raise HTTPException(404, "card not found")

    if tag_names is not None:
        _sync_card_tags(card, tag_names)
        card = db.get_card(card_id)

    return _card_to_dict(card)


@app.get("/api/cards/{card_id}/price-history")
def card_price_history(card_id: int, since: Optional[str] = None,
                       limit: Optional[int] = None):
    """Return the price-history series for one card, oldest-first.

    Query params:
      since   ISO timestamp — only points recorded at/after this time
      limit   keep at most N most-recent points

    Response: {points: [{at, price}], current: float|None, currency: 'USD'}.
    """
    card = db.get_card(card_id)
    if not card:
        raise HTTPException(404, "card not found")
    points = db.get_price_history(card_id, since=since, limit=limit)
    return {
        "points":   points,
        "current":  card.current_market_price,
        "currency": "USD",
    }


def _sync_card_tags(card: "db.Card", desired_names: list[str]):
    """Reconcile the card's tag set against the desired name list.

    Creates missing tags on the user's tag list, attaches new ones, detaches
    tags that are no longer in the list. Case-insensitive name comparison so
    "For Trade" and "for trade" don't both get created as duplicates.
    """
    # Normalize: trim, drop empties, dedup case-insensitively while keeping
    # the first-seen casing for new tag creation.
    seen_lower = set()
    cleaned = []
    for raw in desired_names:
        if not raw:
            continue
        name = str(raw).strip()
        if not name:
            continue
        key = name.lower()
        if key in seen_lower:
            continue
        seen_lower.add(key)
        cleaned.append(name)

    user_id = card.user_id
    existing = {t.name.lower(): t for t in db.list_tags(user_id)}

    desired_ids = set()
    for name in cleaned:
        existing_tag = existing.get(name.lower())
        if existing_tag:
            desired_ids.add(existing_tag.id)
        else:
            try:
                new_tag = db.create_tag(user_id, name, "#94a3b8", False)
                desired_ids.add(new_tag.id)
                existing[name.lower()] = new_tag
            except ValueError:
                # Tag with this name already exists (race) — re-fetch list.
                refreshed = {t.name.lower(): t for t in db.list_tags(user_id)}
                if name.lower() in refreshed:
                    desired_ids.add(refreshed[name.lower()].id)

    current_ids = {t.id for t in (card.tags or [])}
    for tid in desired_ids - current_ids:
        db.add_tag_to_card(card.id, tid)
    for tid in current_ids - desired_ids:
        db.remove_tag_from_card(card.id, tid)


@app.delete("/api/cards/{card_id}")
def delete_card(card_id: int, account: dict = Depends(get_current_account)):
    if not db.delete_card(card_id):
        raise HTTPException(404, "card not found")
    return {"deleted": True, "card_id": card_id}


@app.post("/api/cards/{card_id}/photo")
async def upload_card_photo(card_id: int, photo: UploadFile = File(...), account: dict = Depends(get_current_account)):
    """Save a user-uploaded photo of this physical card. Stored under
    uploads/, served via /uploads/<filename>. Replaces any prior upload.
    HEIC files are converted to JPEG so all browsers can display them."""
    card = db.get_card(card_id)
    if not card:
        raise HTTPException(404, "card not found")

    uploads_dir = Path(__file__).parent / "uploads"
    uploads_dir.mkdir(exist_ok=True)
    suffix = Path(photo.filename or "card.jpg").suffix or ".jpg"
    if suffix.lower() not in {".jpg", ".jpeg", ".png", ".webp", ".heic"}:
        raise HTTPException(400, f"unsupported file type {suffix}")

    raw = await photo.read()
    ts = int(datetime.utcnow().timestamp() * 1000)

    # Convert HEIC → JPEG so all browsers can display it.
    if suffix.lower() == ".heic":
        try:
            import pillow_heif
            from PIL import Image as PilImage
            import io
            pillow_heif.register_heif_opener()
            img = PilImage.open(io.BytesIO(raw))
            buf = io.BytesIO()
            img.save(buf, format="JPEG", quality=90)
            raw = buf.getvalue()
            suffix = ".jpg"
        except Exception as e:
            log.warning("HEIC conversion failed, storing raw: %s", e)

    fname = f"card_{card_id}_{ts}{suffix}"
    path = uploads_dir / fname
    path.write_bytes(raw)

    # Public URL (relative): /uploads/<fname>. The static mount at /uploads
    # serves the file from the same directory.
    public_url = f"/uploads/{fname}"
    db.update_card(card_id, photo_path=public_url)

    # Upload to Supabase Storage if configured (cloud deployment)
    from supabase_storage import is_configured, upload_photo
    if is_configured():
        storage_path = f"cards/{card_id}/{fname}"
        try:
            upload_photo(storage_path, raw, content_type=photo.content_type or "image/jpeg")
            db.update_card(card_id, photo_path=storage_path)
        except Exception as e:
            log.warning("Supabase Storage upload failed, keeping local path: %s", e)

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


# Grader-specific multiplier-against-NM-raw, used as a fallback when
# PriceCharting has no entry for the card. The grading services have very
# different market premiums — PSA commands the most, BGS Black Label often
# exceeds PSA 10, CGC and SGC trade at noticeable discounts. These factors
# are industry rule-of-thumb averages; precision comes from PriceCharting
# (or eventually live eBay sold scraping).
#
# Note: 10.5 is the sentinel for special top grades that sit ABOVE 10 Gem Mint:
#   CGC 10.5 = "10 Pristine"     (CGC's perfect-card grade)
#   BGS 10.5 = "10 Black Label"  (all sub-grades = 10; vanishingly rare)
#   SGC 10.5 = "10 Pristine"     (SGC's perfect-card grade)
# PSA does not have a grade above 10, so PSA 10.5 is not defined.
GRADED_MULTIPLIERS = {
    # Modern (post-EX era, ~2003+) grading premiums. Used as the default
    # fallback when PriceCharting has nothing for the card.
    # PSA — the dominant grader, sets the price ceiling for non-Black-Label
    ("PSA",  10):    4.00,
    ("PSA",  9.5):   2.60,
    ("PSA",  9):     1.60,
    ("PSA",  8):     0.90,
    ("PSA",  7):     0.55,
    # CGC — generally trades at ~70-80% of PSA at the same grade. CGC 10
    # Pristine (sentinel 10.5) is its own SKU and trades at a premium to PSA 10.
    ("CGC",  10.5):  5.00,    # CGC 10 Pristine — premium to PSA 10
    ("CGC",  10):    3.00,    # ~75% of PSA 10
    ("CGC",  9.5):   2.10,    # ~80% of PSA 9.5
    ("CGC",  9):     1.20,    # ~75% of PSA 9
    ("CGC",  8):     0.70,
    ("CGC",  7):     0.45,
    # BGS — strict graders; BGS 10 is rare, BGS 10 Black Label (sentinel 10.5)
    # commands a huge premium because all four sub-grades must be 10.
    ("BGS",  10.5):  7.00,    # BGS 10 Black Label — top of the market
    ("BGS",  10):    4.50,
    ("BGS",  9.5):   2.90,    # close to PSA 9.5 — BGS 9.5 is well-respected
    ("BGS",  9):     1.50,
    ("BGS",  8):     0.80,
    # SGC — smaller market, trades at a discount everywhere. SGC 10 Pristine
    # (sentinel 10.5) is a niche premium.
    ("SGC",  10.5):  4.20,
    ("SGC",  10):    3.40,
    ("SGC",  9.5):   2.20,
    ("SGC",  9):     1.30,
    ("SGC",  8):     0.70,
}

# VINTAGE multipliers (WotC + e-Card era, pre-2003). Grading premiums are
# dramatically higher for old cards — PSA 10 vintage often trades 8-12× NM,
# PSA 9 trades 2.5-3× NM. Measured against actual eBay sold data:
#   Team Rocket Dark Dragonite Holo: NM ~$172, PSA 9 ~$445 → 2.59×
#   Fossil Gengar Holo: NM ~$201, PSA 9 ~$673 → 3.35×, PSA 10 ~$4559 → 22.7×
# These are rough averages — when PriceCharting has the card we use that
# instead. This table only fires when both eBay and PC return nothing.
GRADED_MULTIPLIERS_VINTAGE = {
    ("PSA",  10):    9.00,   # was 4.00 — vintage PSA 10 is a different market
    ("PSA",  9.5):   4.50,
    ("PSA",  9):     2.70,   # was 1.60 — matches the user's eBay finding
    ("PSA",  8):     1.40,
    ("PSA",  7):     0.85,
    ("CGC",  10.5): 10.00,
    ("CGC",  10):    6.50,
    ("CGC",  9.5):   3.50,
    ("CGC",  9):     2.10,
    ("CGC",  8):     1.10,
    ("CGC",  7):     0.65,
    ("BGS",  10.5): 13.00,
    ("BGS",  10):    8.00,
    ("BGS",  9.5):   4.20,
    ("BGS",  9):     2.40,
    ("BGS",  8):     1.20,
    ("SGC",  10.5):  7.50,
    ("SGC",  10):    5.50,
    ("SGC",  9.5):   3.20,
    ("SGC",  9):     2.00,
    ("SGC",  8):     1.00,
}

# Sets that count as "vintage" for the multiplier selector. Used when the
# variant string doesn't explicitly say "1st Edition" / "Shadowless" but
# the set name implies pre-2003.
_VINTAGE_SET_KEYWORDS = {
    "base set", "base set 2", "jungle", "fossil", "team rocket",
    "gym heroes", "gym challenge",
    "neo genesis", "neo discovery", "neo revelation", "neo destiny",
    "legendary collection",
    "expedition", "aquapolis", "skyridge",
}


def _is_vintage_card(set_name: Optional[str], variant: Optional[str]) -> bool:
    """True when the card is pre-2003 WotC/e-Card era — used to pick the
    higher-premium grading multipliers."""
    if variant:
        v = variant.lower()
        # Explicit print indicators — 1st Edition / Shadowless / Unlimited
        # all imply vintage (no modern card has these prints).
        if any(k in v for k in ("1st edition", "first edition", "shadowless",
                                "unlimited holo", "unlimited")):
            return True
    if set_name:
        s = set_name.lower()
        if any(k in s for k in _VINTAGE_SET_KEYWORDS):
            return True
    return False


def _pick_multiplier(grade_company: str, grade: float,
                     set_name: Optional[str], variant: Optional[str]) -> Optional[float]:
    """Pick the right grading multiplier (vintage vs modern) and return it,
    or None if the (grader, grade) combination isn't in the table."""
    key = (grade_company.upper(), float(grade))
    if _is_vintage_card(set_name, variant):
        m = GRADED_MULTIPLIERS_VINTAGE.get(key)
        if m is not None:
            return m
    return GRADED_MULTIPLIERS.get(key)


# Recognized sealed-product type phrases in free-text search queries,
# checked in order (longer/more-specific patterns first). The matched
# portion is stripped from the query to derive the product's name/set —
# e.g. "chaos rising etb" -> name "Chaos Rising", product_type "etb".
_SEALED_PRODUCT_TYPE_PATTERNS: list[tuple[str, str]] = [
    (r"\bleague battle decks?\b", "league_battle_deck"),
    (r"\belite trainer box(es)?\b", "etb"),
    (r"\betbs?\b", "etb"),
    (r"\bbooster box(es)?\b", "booster_box"),
    (r"\bbooster pack(s)?\b", "booster_pack"),
    (r"\btins?\b", "tin"),
    (r"\bbundles?\b", "bundle"),
]


def _parse_sealed_text_query(query: str) -> tuple[str, str, str]:
    """Parse a free-text sealed-product search into (name, set_name, product_type).

    The Scan screen's search bar has a SEALED toggle for typing a product
    name directly (no photo) — e.g. "chaos rising etb" or "151 booster box".
    Strips the recognized product-type phrase to get the set/product name;
    defaults to "etb" (the most commonly individually-tracked sealed item)
    when no type phrase is present.
    """
    text = query.strip()
    product_type = "etb"
    for pattern, ptype in _SEALED_PRODUCT_TYPE_PATTERNS:
        m = re.search(pattern, text, re.IGNORECASE)
        if m:
            product_type = ptype
            text = (text[:m.start()] + text[m.end():]).strip()
            break
    name = " ".join(w.capitalize() for w in text.split()) or query.strip()
    return name, name, product_type


async def _identify_text_sealed(query: str) -> dict:
    """Text-search path for the Scan screen's SEALED toggle.

    Sealed products (booster boxes, ETBs, tins, bundles) aren't in the
    single-card catalogues `card_lookup.search_cards` queries, so a typed
    name like "chaos rising etb" can't go through that path. Parse the
    query into a product name + type, then reuse `_refresh_sealed_price`
    (eBay active-listing price + image — see `lookup_sealed_image`) to
    build a single candidate, same as the photo-identify sealed branch.
    """
    import time as _t
    _t0 = _t.time()
    name, set_name, product_type = _parse_sealed_text_query(query)
    candidate = {
        "name": name,
        "set_name": set_name,
        "card_number": "",
        "image_url": None,
        "image_url_large": None,
        "rarity": None,
        "market_price": None,
        "tcg_id": None,
        "language": "english",
        "variant": None,
        "product_type": product_type,
    }
    try:
        sealed_price = await _refresh_sealed_price(RefreshPriceRequest(
            name=name, set_name=set_name, language="english", product_type=product_type,
        ))
        candidate["market_price"] = sealed_price.get("estimated_price")
        candidate["image_url"] = sealed_price.get("image_url")
        candidate["image_url_large"] = sealed_price.get("image_url_large")
    except Exception as e:
        log.warning("sealed text-search price lookup failed: %s", e)
    log.info("/api/identify text-path sealed q=%r -> %r (%s) in %.2fs",
             query, name, product_type, _t.time() - _t0)
    return {
        "mode": "text_search_sealed",
        "query": query,
        "product_type": product_type,
        "identity": {
            "name": candidate["name"],
            "set_name": candidate["set_name"],
            "card_number": "",
            "language": "english",
            "variant": None,
        },
        "market_price": candidate["market_price"],
        "image_url": candidate["image_url"],
        "candidates": [candidate],
        "candidate_count": 1,
    }


async def _refresh_sealed_price(req: RefreshPriceRequest) -> dict:
    """Price + image lookup for sealed products.

    Price: eBay sold-listing mean first, PriceCharting fallback.
    Image: PriceCharting's product-cover art (official Pokemon TCG box
    art, see pricecharting_lookup.lookup_sealed_price) is preferred —
    it's higher quality than eBay's active-listing seller photos. eBay's
    image is used only when PriceCharting has no cover image for this
    product (see ebay_browse_api.lookup_sealed_image for why any matching
    listing's photo is a true picture of "this product").
    """
    from ebay_lookup import lookup_sealed_recent_n_mean
    from pricecharting_lookup import lookup_sealed_price
    import ebay_browse_api

    pc = await lookup_sealed_price(
        name=req.name,
        set_name=req.set_name or "",
        product_type=req.product_type,
        language=req.language or "english",
    )

    image_url: Optional[str] = pc.image_url if pc else None
    image_url_large: Optional[str] = pc.image_url_large if pc else None

    if not image_url:
        try:
            img = await ebay_browse_api.lookup_sealed_image(
                name=req.name,
                set_name=req.set_name or "",
                product_type=req.product_type,
                language=req.language or "english",
            )
            if img:
                image_url = img.image_url
                image_url_large = img.image_url_large
        except Exception as e:
            log.warning("eBay sealed image lookup failed: %s", e)

    ebay = await lookup_sealed_recent_n_mean(
        name=req.name,
        set_name=req.set_name or "",
        product_type=req.product_type,
        language=req.language or "english",
        n=5,
        period_days=90,
    )
    if ebay:
        return {
            "estimated_price": ebay.mean_usd,
            "source": "ebay_sealed",
            "image_url": image_url,
            "image_url_large": image_url_large,
        }

    if pc and pc.price_usd:
        return {
            "estimated_price": pc.price_usd,
            "source": "pricecharting_sealed",
            "image_url": image_url,
            "image_url_large": image_url_large,
        }

    # Tier 3: eBay Browse API active-listing median. Unlike
    # lookup_sealed_recent_n_mean (HTML scrape of sold listings, blocked by
    # eBay's anti-bot with a hard 403), the Browse API is an authenticated
    # REST endpoint and works. Active asking prices run a bit high vs sold
    # comps, but for products PriceCharting doesn't catalogue at all (e.g.
    # promo League Battle Decks) this is far better than no price.
    try:
        active = await ebay_browse_api.median_sealed_active_price(
            name=req.name,
            set_name=req.set_name or "",
            product_type=req.product_type,
            language=req.language or "english",
        )
    except Exception as e:
        log.warning("eBay active-listing median lookup failed: %s", e)
        active = None
    if active:
        return {
            "estimated_price": active["median_usd"],
            "source": "ebay_active_median",
            "image_url": image_url,
            "image_url_large": image_url_large,
        }

    return {
        "estimated_price": None,
        "source": "not_found",
        "image_url": image_url,
        "image_url_large": image_url_large,
    }


@app.post("/api/refresh-price")
async def refresh_price(req: RefreshPriceRequest):
    """Get a market-price estimate for a given condition or grade.

    Headline source for BOTH raw and graded is the mean of the 5 most-recent
    eBay sold listings (`ebay_lookup.lookup_recent_n_mean`). It's the closest
    proxy to "what is this card actually trading at right now."

    Fallback order if eBay returns nothing (sandbox 403, anti-bot block, or
    genuinely thin comps):
      - Graded → PriceCharting per-grade, then NM × grader multiplier
      - Raw    → raw_price_resolver.resolve_raw_price() — catalogue baseline
        (TCGplayer / Cardmarket / eBay Browse) blended with PriceCharting's
        sold-comp-derived "Ungraded" price
    """
    if req.product_type and req.product_type != "card":
        return await _refresh_sealed_price(req)

    # ----- eBay 5-recent-mean (primary for both raw & graded) --------------
    ebay_recent = None
    if req.name:
        try:
            ebay_recent = await ebay_lookup.lookup_recent_n_mean(
                name=req.name,
                set_name=req.set_name or "",
                card_number=req.card_number or "",
                language=req.language,
                condition=(req.condition or "NM") if not req.is_graded else "NM",
                is_graded=req.is_graded,
                grade_company=req.grade_company if req.is_graded else None,
                grade=req.grade if req.is_graded else None,
                variant=req.variant,
                n=5,
                period_days=90,
            )
        except Exception as e:
            log.warning("eBay recent-N-mean lookup failed: %s", e)
            ebay_recent = None

    if ebay_recent and ebay_recent.sample_size >= 1:
        grade_tag = ""
        if req.is_graded and req.grade_company and req.grade is not None:
            grade_tag = f"{req.grade_company} {req.grade} · "
        cache_tag = "cached" if ebay_recent.cached else "live"
        return {
            "estimated_price": ebay_recent.mean_usd,
            "nm_baseline_usd": None,
            "multiplier": None,
            "source": (f"eBay sold mean · {grade_tag}n={ebay_recent.sample_size} "
                       f"of last {ebay_recent.requested_n} ({cache_tag})"),
            "note": (f"Mean of the {ebay_recent.sample_size} most-recent eBay "
                     f"sold listings (range ${ebay_recent.low_usd:.2f}-"
                     f"${ebay_recent.high_usd:.2f}, median "
                     f"${ebay_recent.median_usd:.2f}, "
                     f"{ebay_recent.period_days}-day window)."),
            "ebay_sold_url": ebay_recent.sold_url,
            "ebay_sales": ebay_recent.sales,
            "ebay_median_usd": ebay_recent.median_usd,
            "ebay_sample_size": ebay_recent.sample_size,
        }

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
        # PC didn't have it. For JP graded cards try eBay Browse API with
        # grade qualifier in the query (e.g. "PSA 10 Umbreon ex 217 Japanese")
        # so we get graded-specific active listing prices directly.
        if req.language.lower() == "japanese":
            try:
                import ebay_browse_api
                br_g = await ebay_browse_api.median_relevant_price(
                    req.name, req.set_name, req.card_number,
                    language="japanese",
                    grade_company=req.grade_company,
                    grade=req.grade,
                )
            except Exception as e:
                log.warning("eBay Browse graded median lookup failed: %s", e)
                br_g = None
            if br_g and br_g["median_usd"]:
                g_label = f"{req.grade_company} {int(req.grade) if req.grade == int(req.grade) else req.grade}"
                return {
                    "estimated_price": round(float(br_g["median_usd"]), 2),
                    "nm_baseline_usd": None,
                    "multiplier": None,
                    "source": (
                        f"{g_label} eBay Browse median (JP active listings, "
                        f"n={br_g['sample_size']} of {br_g['raw_sample_size']}, "
                        f"range ${br_g['low_usd']:.2f}-${br_g['high_usd']:.2f})"
                    ),
                    "note": (
                        f"Trimmed-median of {br_g['sample_size']} active {g_label} listings "
                        f"from eBay (query: {br_g['query']!r}). Based on active listings, "
                        f"not completed sales — use as directional estimate."
                    ),
                }

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

    if req.is_graded and req.grade_company and req.grade is not None:
        mult = _pick_multiplier(req.grade_company, req.grade, req.set_name, req.variant)
        if mult is None:
            raise HTTPException(400,
                f"unsupported grade {req.grade_company} {req.grade}. "
                f"Try a standard grade like PSA 10, CGC 9, BGS 9.5.")
        era_tag = "vintage" if _is_vintage_card(req.set_name, req.variant) else "modern"
        source = (f"{req.grade_company} {req.grade} estimate ({mult:.2f}× NM "
                  f"${nm_price:.2f} from {baseline_label}, {era_tag} multiplier) — "
                  f"PriceCharting had no entry for this card")
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
# Sold listings — eBay recent comps for the Detail screen's "Sold Listings" tab
# ---------------------------------------------------------------------------

@app.post("/api/sold-listings")
async def sold_listings(req: SoldListingsRequest):
    """Return recent eBay sold listings for this card.

    Filters by language and (when graded) grader+grade. Each row carries
    price + sold-date + title + url. The frontend renders them as a table
    so the user sees actual recent comps, not synthesized mock data.
    """
    try:
        result = await ebay_lookup.lookup_sold_listings(
            name=req.name,
            set_name=req.set_name or "",
            card_number=req.card_number or "",
            language=req.language,
            condition=req.condition or "NM",
            is_graded=req.is_graded,
            grade_company=req.grade_company,
            grade=req.grade,
            variant=req.variant,
            period_days=req.period_days,
            max_listings=req.max_listings,
        )
    except AssertionError as e:
        raise HTTPException(400, f"invalid query: {e}")
    except Exception as e:
        log.warning("sold-listings lookup failed: %s", e)
        raise HTTPException(502, f"eBay lookup failed: {e}")

    if not result:
        return {
            "sales": [], "median_usd": None, "sample_size": 0,
            "raw_sample_size": 0, "period_days": req.period_days,
            "low_usd": None, "high_usd": None,
            "sold_url": None, "cached": False,
            "note": "no relevant sold listings found",
        }
    return {
        "sales": result.sales,
        "median_usd": result.median_usd,
        "sample_size": result.sample_size,
        "raw_sample_size": result.raw_sample_size,
        "period_days": result.period_days,
        "low_usd": result.low_usd,
        "high_usd": result.high_usd,
        "sold_url": result.sold_url,
        "cached": result.cached,
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


@app.get("/api/cards/search/jp")
async def cards_search_jp(q: str = "", limit: int = 15):
    """Search TCGdex JP by name with PriceCharting prices attached.

    Replaces the direct client-side searchTCGdex(lang='ja') call so that JP
    widening results (e.g. Neo Genesis Lugia) go through _attach_live_prices
    and show real prices in the Scan filmstrip instead of '—'.
    """
    q = q.strip()
    if len(q) < 2:
        return []
    results = await card_lookup.search_jp_cards(q, limit=limit)
    return [r.to_dict() for r in results]


@app.get("/api/pricecharting/search")
async def pricecharting_search(q: str, limit: int = 10):
    """Last-resort card-identity search — PriceCharting indexes some
    Chinese-exclusive sets (e.g. "Pokemon Chinese CSV4C") that TCGdex has
    registered as a set but never populated with card data. Scan falls back
    here only after Pokemon TCG API and TCGdex both return nothing."""
    if len(q.strip()) < 2:
        return {"results": []}
    results = await pricecharting_lookup.search_products(q, limit=limit)
    return {"results": [r.to_dict() for r in results]}


# ---------------------------------------------------------------------------
# Batch card lookup — replaces the 89-call fan-out the React frontend was
# doing to fetch JP-card translations one at a time.
# ---------------------------------------------------------------------------

class BatchCardsRequest(BaseModel):
    ids: list[str]                          # e.g. ["SV8a-041", "S6a-002"]
    language: str = "ja"                    # "ja" | "en" — TCGdex side; Pokemon TCG API ignores
    source: str = "auto"                    # "auto" | "tcgdex" | "pokemontcg"


_BATCH_CACHE: dict[str, dict] = {}          # in-memory cache for the batch path
_BATCH_CACHE_LOCK = None                    # asyncio.Lock — created lazily
_BATCH_SEMAPHORE = None                     # asyncio.Semaphore(12)


def _get_batch_runtime():
    """Lazy-init asyncio primitives (event loop has to exist first)."""
    global _BATCH_CACHE_LOCK, _BATCH_SEMAPHORE
    import asyncio
    if _BATCH_CACHE_LOCK is None:
        _BATCH_CACHE_LOCK = asyncio.Lock()
    if _BATCH_SEMAPHORE is None:
        # Cap concurrency at 6 — TCGdex rate-limits bursts above ~10/sec
        # and returns 5xx that we'd otherwise count as errors and retry.
        _BATCH_SEMAPHORE = asyncio.Semaphore(6)
    return _BATCH_CACHE_LOCK, _BATCH_SEMAPHORE


def _looks_tcgdex(card_id: str) -> bool:
    """True if the ID matches TCGdex's set-uppercase convention.

    Examples that flag tcgdex: "SV8a-041", "S6a-002", "SVLN-009".
    Examples that flag pokemontcg: "sv8a-41", "base1-58", "swsh9-076".

    Pokemon TCG API IDs are typically lowercase set + digit number with no
    leading zeros; TCGdex IDs are uppercase set + zero-padded number.
    """
    if "-" not in card_id:
        return False
    set_part, num_part = card_id.split("-", 1)
    # Uppercase set + zero-padded numeric local-id → TCGdex
    if set_part.isupper() and num_part.isdigit() and num_part.zfill(len(num_part)) == num_part and len(num_part) >= 2:
        return True
    return False


async def _fetch_one_tcgdex(client, card_id: str, language: str) -> dict:
    """Fetch a single card from TCGdex with one EN fallback.

    Some TCGdex card IDs (e.g. S6a-002, S10D-003 from old Sword&Shield-era
    JP sets) exist in TCGdex's English dataset but not in `ja`. Fall back
    to EN so the frontend still gets a name + image to render.
    """
    primary = language
    secondary = "en" if language != "en" else None

    for lang_try in [primary, secondary]:
        if not lang_try:
            continue
        try:
            r = await client.get(
                f"https://api.tcgdex.net/v2/{lang_try}/cards/{card_id}",
                timeout=6.0,
            )
        except Exception:
            continue
        if r.status_code != 200:
            continue
        try:
            d = r.json()
        except Exception:
            continue
        if not d.get("id"):
            continue
        image = d.get("image")
        return {
            "id": d.get("id"),
            "name": d.get("name") or "",
            "rarity": d.get("rarity"),
            "set_id": (d.get("set") or {}).get("id"),
            "set_name": (d.get("set") or {}).get("name"),
            "card_number": d.get("localId"),
            "image_url": f"{image}/low.webp" if image else None,
            "image_url_large": f"{image}/high.webp" if image else None,
            "language": lang_try,
        }
    return {}


async def _fetch_one_pokemontcg(client, card_id: str) -> dict:
    """Fetch a single card from Pokemon TCG API."""
    try:
        r = await client.get(f"https://api.pokemontcg.io/v2/cards/{card_id}",
                              timeout=8.0)
        if r.status_code != 200:
            return {}
        d = r.json().get("data") or {}
        if not d.get("id"):
            return {}
        images = d.get("images") or {}
        return {
            "id": d.get("id"),
            "name": d.get("name") or "",
            "rarity": d.get("rarity"),
            "set_id": (d.get("set") or {}).get("id"),
            "set_name": (d.get("set") or {}).get("name"),
            "card_number": d.get("number"),
            "image_url": images.get("small"),
            "image_url_large": images.get("large"),
            "language": "en",
        }
    except Exception:
        return {}


@app.post("/api/cards/batch")
async def cards_batch(req: BatchCardsRequest):
    """Fetch up to 200 cards in parallel by ID. Replaces the per-card fan-out
    the React frontend was doing for the multi-language "Names" pipeline
    step — 89 sequential 100ms requests → 1 concurrent batch (~2s wall).

    Request:  {"ids": ["SV8a-041", "S6a-002", ...], "language": "ja",
               "source": "auto"}
    Response: {"cards": {id: {...}}, "errors": [...], "fetched": N,
               "elapsed_ms": ...}

    `source` defaults to "auto": uppercase IDs go to TCGdex (JP), lowercase
    go to Pokemon TCG API. Override with "tcgdex" or "pokemontcg" to force.

    Results are cached in-process for 1 hour (cards don't change shape).
    """
    import asyncio, time as _t
    _t0 = _t.time()

    ids = [i for i in (req.ids or []) if isinstance(i, str) and i.strip()]
    if not ids:
        return {"cards": {}, "errors": [], "fetched": 0, "elapsed_ms": 0}
    if len(ids) > 200:
        raise HTTPException(400, f"too many ids ({len(ids)}); max 200 per batch")

    lang = (req.language or "ja").lower()
    source = (req.source or "auto").lower()

    lock, sem = _get_batch_runtime()
    out_cards: dict[str, dict] = {}
    errors: list[str] = []
    to_fetch: list[tuple[str, str]] = []   # (card_id, fetch_kind)

    # Resolve from cache vs queue for fetch
    async with lock:
        for cid in ids:
            cache_key = f"{cid}|{lang}"
            cached = _BATCH_CACHE.get(cache_key)
            if cached:
                age = _t.time() - cached["_at"]
                if cached.get("_neg") and age < 600:        # 10-min negative cache
                    errors.append(cid)
                    continue
                if not cached.get("_neg") and age < 3600:   # 1-hour positive cache
                    out_cards[cid] = {k: v for k, v in cached.items()
                                       if not k.startswith("_")}
                    continue
            kind = (
                "tcgdex" if source == "tcgdex"
                else "pokemontcg" if source == "pokemontcg"
                else ("tcgdex" if _looks_tcgdex(cid) else "pokemontcg")
            )
            to_fetch.append((cid, kind))

    if to_fetch:
        import httpx
        async with httpx.AsyncClient(headers={"User-Agent": "Mozilla/5.0"}) as client:
            async def _go(cid: str, kind: str):
                async with sem:
                    if kind == "tcgdex":
                        data = await _fetch_one_tcgdex(client, cid, lang)
                    else:
                        data = await _fetch_one_pokemontcg(client, cid)
                return cid, data

            results = await asyncio.gather(
                *[_go(cid, kind) for cid, kind in to_fetch],
                return_exceptions=True,
            )
            now = _t.time()
            async with lock:
                for r in results:
                    if isinstance(r, Exception):
                        continue
                    cid, data = r
                    if data:
                        out_cards[cid] = data
                        _BATCH_CACHE[cid + "|" + lang] = {**data, "_at": now}
                    else:
                        errors.append(cid)
                        # Negative-cache 404s for a shorter window (10 min)
                        # so we don't re-hit dead IDs every batch call.
                        _BATCH_CACHE[cid + "|" + lang] = {
                            "_at": now, "_neg": True,
                        }

    return {
        "cards": out_cards,
        "errors": errors,
        "fetched": len(out_cards),
        "elapsed_ms": int((_t.time() - _t0) * 1000),
    }


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

from fastapi import Request


@app.post("/api/identify")
async def identify(request: Request, account: dict | None = Depends(get_current_account_optional)):
    if account:
        month = datetime.now(timezone.utc).strftime("%Y-%m")
        is_pro = account["plan"] == "pro" or (
            account["trial_ends_at"] is not None
            and account["trial_ends_at"] > datetime.now(timezone.utc)
        )
        if not is_pro:
            count = db.get_scan_count(account["id"], month)
            if count >= FREE_SCAN_LIMIT:
                raise HTTPException(
                    status_code=429,
                    detail={"error": "scan_limit_reached", "limit": FREE_SCAN_LIMIT, "used": count},
                )
        db.increment_scan_count(account["id"], month)
    try:
        return await _identify_inner(request)
    except HTTPException:
        raise
    except Exception as e:
        # Surface the full traceback in the server log — uvicorn swallows
        # it otherwise and the user sees only a generic 500. Frontend gets
        # the exception class + message so we can diagnose from the
        # network response, not just the access log.
        import traceback as _tb
        log.exception("/api/identify crashed: %s", e)
        raise HTTPException(
            500,
            f"{type(e).__name__}: {e} — see server log for full traceback",
        )


async def _identify_inner(request: Request):
    """Identify a card from EITHER a photo upload OR a text query.

    The frontend's Scan screen posts here for both: file upload (multipart
    with `photo`) when the camera is used, and a JSON `{query: "..."}` body
    when the user types in the search bar. We branch on Content-Type.

    Photo path:  multipart/form-data, field `photo`  → ocr_engine.identify_card
    Text path:   application/json, body {query, q, or text}  → card_lookup.search_cards
    """
    content_type = request.headers.get("content-type", "").lower()
    photo: Optional[UploadFile] = None
    query: Optional[str] = None
    product_type_hint: Optional[str] = None

    if ("multipart/form-data" in content_type
            or "application/x-www-form-urlencoded" in content_type):
        # Form-style POSTs — multipart (with or without file) and plain
        # urlencoded. The latter is what TestClient / curl produce when a
        # form has no file attached.
        form = await request.form()
        upload = form.get("photo") or form.get("file") or form.get("image")
        # Duck-type the upload — Starlette's UploadFile and FastAPI's
        # UploadFile aren't always the same class instance under TestClient.
        if upload is not None and hasattr(upload, "read") and hasattr(upload, "filename"):
            photo = upload
        q_val = form.get("query") or form.get("q") or form.get("text")
        if isinstance(q_val, str) and q_val.strip():
            query = q_val.strip()
        # Pre-scan TYPE toggle on the Scan screen — "card" or "sealed". Biases
        # the OCR prompt and (for "card") forces the response's product_type,
        # so the user's explicit choice wins over an LLM misclassification.
        hint_val = form.get("product_type_hint")
        if isinstance(hint_val, str) and hint_val.strip().lower() in ("card", "sealed"):
            product_type_hint = hint_val.strip().lower()
    elif "application/json" in content_type or content_type == "":
        try:
            body = await request.json()
        except Exception:
            body = {}
        if isinstance(body, dict):
            q_val = body.get("query") or body.get("q") or body.get("text")
            if isinstance(q_val, str) and q_val.strip():
                query = q_val.strip()
            hint_val = body.get("product_type_hint")
            if isinstance(hint_val, str) and hint_val.strip().lower() in ("card", "sealed"):
                product_type_hint = hint_val.strip().lower()
    else:
        raise HTTPException(400, f"unsupported content-type: {content_type!r}")

    # ---- TEXT PATH: prefer the text query over any attached photo ------------
    # The frontend's Scan screen sends multipart with BOTH a stale camera
    # photo AND the typed query when the user presses Find. The query
    # is what they actually want — ignore the photo in that case.
    if query:
        # SEALED toggle on a typed query — sealed products (booster boxes,
        # ETBs, tins, bundles) aren't in the single-card catalogues
        # `card_lookup.search_cards` searches below, so route to the
        # eBay-backed sealed lookup instead (see _identify_text_sealed).
        if product_type_hint == "sealed":
            return await _identify_text_sealed(query)

        import time as _t
        _t0 = _t.time()
        try:
            # Skip live_prices on /api/identify — it's latency-sensitive
            # (frontend stays on viewfinder until response lands). The
            # full grade ladder comes from /api/refresh-price when the
            # user picks a candidate. /api/cards/search still includes
            # live_prices for the explorer/browse paths.
            results = await card_lookup.search_cards(
                query, limit=20, attach_live_prices=False,
            )
        except Exception as e:
            raise HTTPException(500, f"search failed: {e}")
        log.info("/api/identify text-path q=%r → %d results in %.2fs",
                 query, len(results), _t.time() - _t0)
        if not results:
            return {
                "mode": "text_search",
                "query": query,
                "candidates": [],
                "candidate_count": 0,
                "identity": None,
                "market_price": None,
                "image_url": None,
            }
        first = results[0]
        return {
            "mode": "text_search",
            "query": query,
            "identity": {
                "name": first.name,
                "set_name": first.set_name,
                "card_number": first.card_number,
                "language": first.language,
                "variant": first.variant,
            },
            "market_price": first.market_price,
            "image_url": first.image_url,
            "candidates": [r.to_dict() for r in results],
            "candidate_count": len(results),
        }

    if photo is None:
        raise HTTPException(400, "expected either `photo` (multipart) or `query` (json)")

    # ---- PHOTO PATH: existing OCR flow ---------------------------------------
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
    raw_name = getattr(photo, "filename", None) or "card.jpg"
    suffix = Path(raw_name).suffix.lower() or ".jpg"
    tmp_path = uploads_dir / f"upload_{int(datetime.utcnow().timestamp() * 1000)}{suffix}"
    try:
        body = await photo.read()
    except Exception as e:
        log.exception("photo.read() failed")
        raise HTTPException(400, f"could not read uploaded photo: {e}")
    if not body:
        raise HTTPException(400, "empty photo upload")
    tmp_path.write_bytes(body)

    try:
        result = ocr_engine.identify_card(str(tmp_path), product_type_hint=product_type_hint)
    except Exception as e:
        log.exception("OCR identify_card failed for %s", tmp_path)
        raise HTTPException(
            500,
            f"OCR failed ({type(e).__name__}): {e}",
        )

    # Defensive: OCR should always return an identity, but guard against the
    # rare case where the LLM returns garbage that the parser couldn't fix.
    if not result or not result.identity or not (result.identity.name or "").strip():
        raise HTTPException(
            502,
            "OCR returned no card name — try retaking the photo with better "
            "lighting / focus on the card title.",
        )

    # The pre-scan TYPE toggle (Auto/Card/Sealed) is a stronger signal than
    # the LLM's own guess: "Card" means run it through the normal individual
    # card pipeline below no matter what OCR thought it saw. "Sealed" / "Auto"
    # defer to OCR's product_type (already biased by the hint inside the LLM
    # prompt — see ocr_engine._identify_user_prompt).
    effective_product_type = result.product_type
    if product_type_hint == "card":
        effective_product_type = "card"

    market_price: Optional[float] = None
    image_url: Optional[str] = None
    candidates: list[dict] = []
    import time as _t
    _t0 = _t.time()

    if effective_product_type != "card":
        # Sealed products (booster boxes, ETBs, tins, bundles) aren't in the
        # individual-card catalogues queried below — Pokemon TCG API and
        # TCGdex only index single cards, and a name-only search there
        # would return unrelated single-card art. Build the candidate
        # straight from the OCR identity; _refresh_sealed_price fills in
        # market_price AND image_url (via eBay active-listing photos —
        # see ebay_browse_api.lookup_sealed_image for why that's safe for
        # sealed products specifically). If that comes back empty, the
        # frontend falls back to the user's own scan photo.
        candidates = [{
            "name": result.identity.name,
            "set_name": result.identity.set_name,
            "card_number": result.identity.card_number,
            "image_url": None,
            "image_url_large": None,
            "rarity": None,
            "market_price": None,
            "tcg_id": None,
            "language": result.identity.language or "english",
            "variant": result.identity.variant,
            "product_type": effective_product_type,
        }]
        try:
            sealed_price = await _refresh_sealed_price(RefreshPriceRequest(
                name=result.identity.name,
                set_name=result.identity.set_name,
                language=result.identity.language or "english",
                product_type=effective_product_type,
            ))
            market_price = sealed_price.get("estimated_price")
            candidates[0]["market_price"] = market_price
            image_url = sealed_price.get("image_url")
            candidates[0]["image_url"] = image_url
            candidates[0]["image_url_large"] = sealed_price.get("image_url_large")
        except Exception as e:
            log.warning("sealed price lookup failed: %s", e)
        log.info("/api/identify photo-path sealed product: %r (%s) in %.2fs",
                 result.identity.name, effective_product_type, _t.time() - _t0)
    else:
        # The OCR engine gave us STRUCTURED fields (name, set, number, variant,
        # language) — use lookup_card directly. It handles number normalization,
        # variant-aware scoring, JP→EN fallback, and apostrophe escaping. This
        # is more accurate than jamming everything into a free-text search_cards
        # query (which mishandles bare 2-3 digit numbers like "27").
        try:
            hit = await card_lookup.lookup_card(
                name=result.identity.name,
                set_name=result.identity.set_name,
                card_number=result.identity.card_number,
                language=result.identity.language,
                variant=result.identity.variant,
            )
            if hit:
                candidates = [hit.to_dict()]
                market_price = hit.market_price
                image_url = hit.image_url
                log.info("/api/identify photo-path structured hit: %s in %.2fs",
                         hit.tcg_id, _t.time() - _t0)
        except Exception as e:
            log.warning("photo-path structured lookup failed: %s", e)

        # ALWAYS augment with a broad name search so the user sees more than
        # one card to choose from. If lookup_card found a structured hit
        # (high-confidence single match), keep it first; append the broad
        # results dedupe'd. If lookup_card found nothing, the broad search is
        # the only source — catches uncataloged sets like First Partner
        # Bulbasaur Collection by surfacing every Bulbasaur the catalog knows.
        try:
            broad = await card_lookup.search_cards(
                result.identity.name, limit=10, attach_live_prices=False,
            )
            seen_ids = {c.get("tcg_id") for c in candidates if c.get("tcg_id")}
            for r in broad:
                d = r.to_dict()
                if d.get("tcg_id") and d.get("tcg_id") in seen_ids:
                    continue
                candidates.append(d)
                seen_ids.add(d.get("tcg_id"))
            if not market_price and broad:
                market_price = broad[0].market_price
            if not image_url and broad:
                # Only trust this as THIS card's artwork if the printed
                # number OCR read off the photo agrees with the broad
                # (name-only) match's number. A name-only hit can be a
                # different printing of the same Pokemon (e.g. a brand-new
                # promo not yet in the catalogue vs. an older base-set
                # rare) with completely different art — showing that art
                # as "this card" is misleading. Leave image_url null; the
                # frontend falls back to the user's own captured photo.
                ocr_num = card_lookup._normalize_number(result.identity.card_number)
                broad_num = card_lookup._normalize_number(broad[0].card_number)
                if not ocr_num or ocr_num == broad_num:
                    image_url = broad[0].image_url
        except Exception as e:
            log.warning("photo-path broad search failed: %s", e)

        # ALSO append eBay Browse API matches. This is the catch-all for cards
        # neither Pokemon TCG API nor TCGdex has indexed (Pokemon Center
        # promos, First Partner Illustration Collection, regional exclusives,
        # stamped reprints). Each item becomes a candidate the user can pick.
        try:
            import ebay_browse_api
            parts = [result.identity.name]
            if result.identity.set_name:
                parts.append(result.identity.set_name)
            if result.identity.card_number:
                parts.append(str(result.identity.card_number))
            ebay_q = " ".join(p for p in parts if p)
            ebay_items = await ebay_browse_api.search_items(ebay_q, limit=8)
            for it in ebay_items:
                candidates.append({
                    "name": result.identity.name,
                    "set_name": None,
                    "card_number": None,
                    "image_url": it.image_url,
                    "image_url_large": it.image_url_large,
                    "rarity": result.identity.variant or "Promo",
                    "market_price": it.price_usd,
                    "tcg_id": f"ebay-{it.item_id}",
                    "language": result.identity.language or "english",
                    "source": "ebay-browse",
                    "variant": result.identity.variant,
                    "ebay_title": it.title,
                    "ebay_condition": it.condition,
                    "ebay_url": it.item_url,
                })
            if not image_url and ebay_items:
                image_url = ebay_items[0].image_url
            if not market_price and ebay_items:
                market_price = ebay_items[0].price_usd
        except Exception as e:
            log.warning("eBay Browse augment failed: %s", e)

        log.info("/api/identify photo-path: %d total candidates "
                 "(structured + broad + eBay) in %.2fs",
                 len(candidates), _t.time() - _t0)

    return {
        "mode": "photo_ocr",
        "product_type": effective_product_type,
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
        "candidates": candidates,
        "candidate_count": len(candidates),
    }


# ---------------------------------------------------------------------------
# Static frontend
# ---------------------------------------------------------------------------

STATIC_DIR = Path(__file__).parent / "static"
STATIC_DIR.mkdir(exist_ok=True)
UPLOADS_DIR = Path(__file__).parent / "uploads"
UPLOADS_DIR.mkdir(exist_ok=True)

# Serve the Vite build output (static/dist/) when available, otherwise fall
# back to the legacy Babel-in-browser static/ directory.
_DIST_DIR = STATIC_DIR / "dist"
FRONTEND_DIR = _DIST_DIR if (_DIST_DIR / "index.html").exists() else STATIC_DIR

app.mount("/uploads", StaticFiles(directory=str(UPLOADS_DIR)), name="uploads")


@app.get("/")
def index():
    return FileResponse(str(FRONTEND_DIR / "index.html"))


# Serves frontend assets from FRONTEND_DIR at the site root. Must be
# mounted last — Starlette matches routes in registration order, so /api/*,
# /uploads, and the explicit "/" route above all take precedence over this
# catch-all.
app.mount("/", StaticFiles(directory=str(FRONTEND_DIR)), name="static")


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
