"""
Pokemon Card OCR / identification engine.

Hybrid pipeline: pHash cache → Claude multimodal LLM → identity cache →
optional Tesseract card-number cross-check (skipped on holographic foil
variants where it reliably fails).
"""
from __future__ import annotations

import base64
import json
import logging
import os
import re
import sqlite3
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Optional

# Soft imports — module loads even without these; each call site checks.
try:
    from PIL import Image  # type: ignore
except ImportError:
    Image = None  # type: ignore

try:
    import imagehash  # type: ignore
except ImportError:
    imagehash = None  # type: ignore

try:
    import pytesseract  # type: ignore
except ImportError:
    pytesseract = None  # type: ignore

try:
    import anthropic  # type: ignore
except ImportError:
    anthropic = None  # type: ignore

try:
    import google.generativeai as genai  # type: ignore
except ImportError:
    genai = None  # type: ignore

# HEIC support so iPhone photos work directly without manual conversion.
try:
    import pillow_heif  # type: ignore
    pillow_heif.register_heif_opener()
except ImportError:
    pass

from pricing_engine import (
    CardIdentity,
    CardPriceReport,
    CardQuery,
    Fetcher,
    price_card_side_by_side,
)

log = logging.getLogger(__name__)


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


# ---------------------------------------------------------------------------
# Perceptual hashing
# ---------------------------------------------------------------------------

PHASH_HAMMING_THRESHOLD = 6


def compute_phash(image_path: str) -> str:
    if Image is None or imagehash is None:
        raise RuntimeError("Need Pillow and imagehash (pip install Pillow imagehash)")
    with Image.open(image_path) as im:
        return str(imagehash.phash(im, hash_size=8))


def hamming_distance(a: str, b: str) -> int:
    if len(a) != len(b):
        return max(len(a), len(b)) * 4
    return bin(int(a, 16) ^ int(b, 16)).count("1")


# ---------------------------------------------------------------------------
# SQLite cache (two-tier: pHash + identity)
# ---------------------------------------------------------------------------

DEFAULT_CACHE_PATH = (
    Path(os.environ.get("POKEMON_OCR_CACHE", ""))
    if os.environ.get("POKEMON_OCR_CACHE")
    else Path(__file__).parent / "ocr_cache.sqlite"
)


def _identity_key(identity: CardIdentity) -> str:
    return "|".join([
        identity.name.strip().lower(),
        identity.set_name.strip().lower(),
        identity.card_number.strip().lower(),
        identity.language.strip().lower(),
        (identity.variant or "").strip().lower(),
    ])


class IdentifyCache:
    SCHEMA = """
    CREATE TABLE IF NOT EXISTS identifications (
        phash       TEXT PRIMARY KEY,
        name        TEXT NOT NULL,
        set_name    TEXT NOT NULL,
        card_number TEXT NOT NULL,
        language    TEXT NOT NULL,
        variant     TEXT,
        confidence  REAL NOT NULL,
        source      TEXT NOT NULL,
        raw_llm_json TEXT,
        ocr_card_number TEXT,
        ts          REAL NOT NULL
    );
    CREATE TABLE IF NOT EXISTS identity_index (
        identity_key TEXT PRIMARY KEY,
        phash        TEXT NOT NULL,
        first_seen   REAL NOT NULL,
        last_seen    REAL NOT NULL,
        photo_count  INTEGER NOT NULL DEFAULT 1
    );
    """

    def __init__(self, path=DEFAULT_CACHE_PATH):
        self.path = str(path)
        self._conn = sqlite3.connect(self.path)
        self._conn.executescript(self.SCHEMA)
        try:
            self._conn.execute(
                "ALTER TABLE identifications ADD COLUMN product_type TEXT NOT NULL DEFAULT 'card'"
            )
        except Exception:
            pass
        self._conn.commit()

    def lookup(self, phash):
        cur = self._conn.execute("SELECT * FROM identifications WHERE phash=?", (phash,))
        row = cur.fetchone()
        if row:
            return self._row_to_result(row, phash)
        cur = self._conn.execute("SELECT * FROM identifications")
        best = None
        for r in cur.fetchall():
            d = hamming_distance(phash, r[0])
            if d <= PHASH_HAMMING_THRESHOLD and (best is None or d < best[0]):
                best = (d, r)
        if best:
            res = self._row_to_result(best[1], phash)
            res.notes.append(f"phash fuzzy-match (hamming={best[0]})")
            return res
        return None

    def lookup_by_identity(self, identity):
        key = _identity_key(identity)
        cur = self._conn.execute("SELECT phash FROM identity_index WHERE identity_key=?", (key,))
        row = cur.fetchone()
        if not row:
            return None
        cur = self._conn.execute("SELECT * FROM identifications WHERE phash=?", (row[0],))
        ident_row = cur.fetchone()
        if not ident_row:
            return None
        res = self._row_to_result(ident_row, row[0])
        res.source = "cache (identity)"
        res.notes.append(f"identity-key hit: {key}")
        return res

    def store(self, result):
        i = result.identity
        now = time.time()
        self._conn.execute(
            "INSERT OR REPLACE INTO identifications "
            "(phash,name,set_name,card_number,language,variant,confidence,source,"
            "raw_llm_json,ocr_card_number,ts,product_type) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (result.phash, i.name, i.set_name, i.card_number, i.language, i.variant,
             result.confidence, result.source, result.raw_llm_json,
             result.ocr_card_number, now, getattr(result, "product_type", "card")),
        )
        key = _identity_key(i)
        cur = self._conn.execute("SELECT photo_count FROM identity_index WHERE identity_key=?", (key,))
        if cur.fetchone():
            self._conn.execute(
                "UPDATE identity_index SET phash=?, last_seen=?, photo_count=photo_count+1 WHERE identity_key=?",
                (result.phash, now, key),
            )
        else:
            self._conn.execute(
                "INSERT INTO identity_index (identity_key, phash, first_seen, last_seen) VALUES (?,?,?,?)",
                (key, result.phash, now, now),
            )
        self._conn.commit()

    def photo_count_for(self, identity):
        cur = self._conn.execute("SELECT photo_count FROM identity_index WHERE identity_key=?",
                                 (_identity_key(identity),))
        row = cur.fetchone()
        return row[0] if row else 0

    @staticmethod
    def _row_to_result(row, query_phash):
        (phash, name, set_name, card_number, language, variant, confidence,
         source, raw_llm_json, ocr_card_number, _ts) = row[:11]
        product_type = row[11] if len(row) > 11 else "card"
        product_type = product_type or "card"
        return IdentifyResult(
            identity=CardIdentity(name=name, set_name=set_name, card_number=card_number,
                                   language=language, variant=variant),
            confidence=confidence,
            source="cache" if phash == query_phash else "cache (fuzzy)",
            phash=phash,
            raw_llm_json=raw_llm_json,
            ocr_card_number=ocr_card_number,
            product_type=product_type,
        )


# ---------------------------------------------------------------------------
# LLM identification
# ---------------------------------------------------------------------------

LLM_SYSTEM_PROMPT = """\
You are a Pokemon TCG card identifier. Look at the image and return the card's
metadata as STRICT JSON. Do not include any prose, markdown, or commentary —
only a single JSON object.

Schema:
{
  "name": "Pokemon name as printed (include trainer prefix like Sabrina's Alakazam or Team Rocket's Moltres)",
  "set_name": "set/expansion name in full English",
  "card_number": "printed number with denominator (137/086) or just the number",
  "language": "english | japanese",
  "variant": "Holo | Reverse Holo | Full Art | Illustration Rare | Special Art Rare | Special Illustration Rare | Hyper Rare | Alt Art | Rainbow Rare | Gold Secret Rare | 1st Edition | Promo | null",
  "confidence": 0..1
}

Rules:
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

SEALED PRODUCT RECOGNITION:
Also identify if the image shows sealed product packaging rather than an individual card.
If it is sealed product packaging, set product_type to one of: booster_pack, booster_box, etb, tin, bundle.
If it is an individual card (or you cannot tell), set product_type to "card".
For sealed products, card_number and variant should be null or empty.
Add "product_type" to the JSON response alongside the other fields.

- JSON only, no backticks, no prose.
"""

DEFAULT_LLM_MODEL = "claude-sonnet-4-5"
DEFAULT_GEMINI_MODEL = "gemini-2.5-flash"


_BILLING_ERROR_HINTS = (
    # Billing / quota — the original triggers
    "credit balance is too low",
    "billing",
    "insufficient",
    "quota",
    "exceeded your current quota",
    "resource has been exhausted",
    "rate limit",
    "rate_limit",
    # Auth — a broken/invalid/revoked key on the primary should fall back
    # to the secondary provider, not 500 the request. Same intent: "this
    # provider can't serve right now; try the other one."
    "api key not valid",
    "invalid api key",
    "invalid_api_key",
    "authentication",
    "unauthorized",
    "permission denied",
    "permission_denied",
)


def _looks_like_billing_error(exc: Exception) -> bool:
    """True for billing, quota, rate-limit, AND auth failures. Both kinds
    mean "the primary LLM provider can't serve this request right now",
    so the fallback should fire when an alternate key is configured.
    """
    return any(hint in str(exc).lower() for hint in _BILLING_ERROR_HINTS)


def _claude_model(m: Optional[str]) -> str:
    """Use the caller's model name only if it's actually a Claude model;
    otherwise substitute the provider's default. Prevents passing a Gemini
    model name to Anthropic or vice versa."""
    return m if m and m.lower().startswith("claude") else DEFAULT_LLM_MODEL


def _gemini_model(m: Optional[str]) -> str:
    return m if m and m.lower().startswith("gemini") else DEFAULT_GEMINI_MODEL


def identify_with_llm(image_path: str, model: Optional[str] = None):
    """Identify a card via Gemini (default) with Claude as a last-resort fallback.

    Hard rule: if GOOGLE_API_KEY is set, Gemini is used. Claude is only
    touched when GOOGLE_API_KEY is missing entirely, OR when Gemini
    returns a quota/billing error AND ANTHROPIC_API_KEY is set.

    The `LLM_PROVIDER` env var can force one provider explicitly, but
    "anthropic" is honored only when GOOGLE_API_KEY isn't set — preventing
    the previous footgun where an old env var routed everything to Claude
    even after the user moved billing to Google.
    """
    has_gemini_key = bool(os.environ.get("GOOGLE_API_KEY"))
    has_anthropic_key = bool(os.environ.get("ANTHROPIC_API_KEY"))
    explicit = os.environ.get("LLM_PROVIDER", "").strip().lower()

    if not has_gemini_key and not has_anthropic_key:
        raise RuntimeError(
            "No LLM key configured. Set GOOGLE_API_KEY (free, "
            "https://aistudio.google.com/apikey) or ANTHROPIC_API_KEY."
        )

    # If the user explicitly forces a provider AND has its key, honor it.
    if explicit == "anthropic" and has_anthropic_key and not has_gemini_key:
        primary, fallback = "anthropic", None
    elif explicit == "gemini" and has_gemini_key:
        primary, fallback = "gemini", "anthropic" if has_anthropic_key else None
    elif has_gemini_key:
        # DEFAULT — Gemini wins whenever its key exists. This is the path
        # the user asked for after moving billing to Google.
        primary, fallback = "gemini", "anthropic" if has_anthropic_key else None
    else:
        primary, fallback = "anthropic", None

    log.info("OCR provider: primary=%s fallback=%s "
             "(has_gemini=%s has_anthropic=%s LLM_PROVIDER=%r)",
             primary, fallback, has_gemini_key, has_anthropic_key, explicit)

    def _run(provider: str):
        if provider == "anthropic":
            return _identify_with_anthropic(image_path, _claude_model(model))
        return _identify_with_gemini(image_path, _gemini_model(model))

    try:
        return _run(primary)
    except Exception as e:
        if fallback and _looks_like_billing_error(e):
            log.warning(
                "%s returned a quota/billing error (%s) — falling back to %s",
                primary, e, fallback,
            )
            return _run(fallback)
        raise


def _identify_with_anthropic(image_path: str, model: str):
    if anthropic is None:
        raise RuntimeError("pip install anthropic")
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise RuntimeError("ANTHROPIC_API_KEY not set")

    image_bytes = Path(image_path).read_bytes()
    image_b64 = base64.standard_b64encode(image_bytes).decode("ascii")
    media_type = _guess_media_type(image_path)

    client = anthropic.Anthropic(api_key=api_key)
    msg = client.messages.create(
        model=model,
        max_tokens=400,
        system=LLM_SYSTEM_PROMPT,
        messages=[{
            "role": "user",
            "content": [
                {"type": "image",
                 "source": {"type": "base64", "media_type": media_type, "data": image_b64}},
                {"type": "text", "text": "Identify this card."},
            ],
        }],
    )
    raw = msg.content[0].text.strip()
    identity, confidence, raw_json, product_type = _build_identity_from_json(raw)
    return identity, confidence, raw_json, product_type


def _identify_with_gemini(image_path: str, model: str):
    """Free-tier path. Get a key at https://aistudio.google.com/apikey."""
    if genai is None:
        raise RuntimeError("pip install google-generativeai")
    api_key = os.environ.get("GOOGLE_API_KEY")
    if not api_key:
        raise RuntimeError("GOOGLE_API_KEY not set")

    genai.configure(api_key=api_key)
    gemini_model = genai.GenerativeModel(
        model,
        system_instruction=LLM_SYSTEM_PROMPT,
        generation_config={"temperature": 0.1, "response_mime_type": "application/json"},
    )

    # Gemini accepts PIL.Image directly which auto-handles HEIC if pillow_heif
    # is registered, jpg/png/webp natively.
    if Image is None:
        raise RuntimeError("pip install Pillow (needed to load images for Gemini)")
    img = Image.open(image_path)
    response = gemini_model.generate_content(
        [img, "Identify this card."],
        request_options={"timeout": 30},
    )
    raw = response.text.strip()
    identity, confidence, raw_json, product_type = _build_identity_from_json(raw)
    return identity, confidence, raw_json, product_type


def _build_identity_from_json(raw: str):
    parsed = _parse_llm_json(raw)
    language = parsed["language"].strip().lower()
    variant = (parsed.get("variant") or None)

    # SANITY-FILTER for the LLM's variant guess. The model occasionally
    # hallucinates "1st Edition" on vintage-looking holos that don't
    # actually carry the stamp — most often on Japanese cards (which
    # never used the 1st Edition designation at all). Server-side
    # enforcement so a bad LLM output doesn't pollute downstream pricing.
    if variant and language == "japanese":
        v = variant.strip().lower()
        if v in ("1st edition", "first edition", "1st ed", "edition 1"):
            log.info("Dropping LLM variant=%r on JP card — Japanese sets "
                     "have no 1st Edition; defaulting to Holo.", variant)
            variant = "Holo"

    product_type = str(parsed.get("product_type", "card") or "card").lower().strip()
    VALID_PRODUCT_TYPES = {"card", "booster_pack", "booster_box", "etb", "tin", "bundle"}
    if product_type not in VALID_PRODUCT_TYPES:
        product_type = "card"

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


def _guess_media_type(path):
    ext = Path(path).suffix.lower()
    return {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png",
            ".webp": "image/webp", ".heic": "image/heic"}.get(ext, "image/jpeg")


def _parse_llm_json(raw):
    cleaned = raw.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```[a-zA-Z]*\n?", "", cleaned)
        cleaned = re.sub(r"\n?```$", "", cleaned)
    return json.loads(cleaned)


# ---------------------------------------------------------------------------
# OCR card-number cross-check
# ---------------------------------------------------------------------------

CARD_NUMBER_RE = re.compile(r"\b(\d{1,3})\s*[/／]\s*(\d{1,3})\b")

_HOLO_VARIANTS_NO_OCR = {
    "rainbow rare", "secret rare", "hyper rare",
    "alt art", "alternate art",
    "special art rare", "special illustration rare", "sar",
    "gold", "gold secret rare",
    "shiny rare", "shiny secret rare",
}


def should_skip_ocr_for_variant(variant):
    if not variant:
        return False
    return variant.strip().lower() in _HOLO_VARIANTS_NO_OCR


def extract_card_number_via_ocr(image_path):
    if pytesseract is None or Image is None:
        return None
    try:
        with Image.open(image_path) as im:
            w, h = im.size
            crop = im.crop((0, int(h * 0.85), int(w * 0.45), h))
            text = pytesseract.image_to_string(crop, config="--psm 7")
    except Exception as e:
        log.warning("OCR failed: %s", e)
        return None
    m = CARD_NUMBER_RE.search(text)
    if not m:
        return None
    return f"{m.group(1).zfill(3)}/{m.group(2).zfill(3)}"


def disambiguate_card_number(llm_identity, ocr_number, notes):
    if not ocr_number:
        return llm_identity
    if not llm_identity.card_number:
        notes.append(f"OCR filled in missing card number: {ocr_number}")
        return _replace_card_number(llm_identity, ocr_number)
    if _normalize_number(llm_identity.card_number) == _normalize_number(ocr_number):
        return llm_identity
    notes.append(f"OCR card number ({ocr_number}) overrides LLM ({llm_identity.card_number})")
    return _replace_card_number(llm_identity, ocr_number)


def _normalize_number(s):
    m = CARD_NUMBER_RE.search(s.replace("／", "/"))
    if not m:
        return s.strip()
    return f"{int(m.group(1))}/{int(m.group(2))}"


def _replace_card_number(identity, new_number):
    return CardIdentity(name=identity.name, set_name=identity.set_name,
                         card_number=new_number, language=identity.language,
                         variant=identity.variant)


# ---------------------------------------------------------------------------
# Top-level
# ---------------------------------------------------------------------------

def _correct_identity(identity: "CardIdentity") -> "CardIdentity":
    """Apply post-hoc sanity rules to a CardIdentity regardless of whether it
    came from a fresh LLM call OR the cache. Runs on every identify_card path.

    Currently strips "1st Edition" from Japanese cards (JP printings never
    used the 1st Edition designation; the LLM occasionally hallucinates it
    on vintage holos and the bad variant routes to the 2-5× 1st Ed price tier).
    """
    if not identity:
        return identity
    variant = identity.variant
    language = (identity.language or "").strip().lower()
    if variant and language == "japanese":
        v = variant.strip().lower()
        if v in ("1st edition", "first edition", "1st ed", "edition 1"):
            log.info("Cache-read correction: dropping variant=%r on JP %r "
                     "(JP sets have no 1st Edition; defaulting to Holo)",
                     variant, identity.name)
            return CardIdentity(
                name=identity.name, set_name=identity.set_name,
                card_number=identity.card_number,
                language=identity.language, variant="Holo",
            )
    return identity


def identify_card(image_path, cache=None, *, llm_model=DEFAULT_LLM_MODEL, use_ocr=True):
    if cache is None:
        cache = IdentifyCache()

    phash = compute_phash(image_path)

    cached = cache.lookup(phash)
    if cached:
        # Apply the JP→no-1st-Edition correction to cached entries too —
        # the cache may contain stale identities from before this fix
        # shipped. Cheap idempotent rewrite; persist the fix so subsequent
        # reads of the same pHash don't need to re-correct.
        corrected = _correct_identity(cached.identity)
        if corrected is not cached.identity:
            cached.identity = corrected
            cached.notes.append("auto-corrected variant (JP cards have no 1st Edition)")
            try:
                cache.store(cached)
            except Exception as e:
                log.warning("could not persist corrected cache entry: %s", e)
        return cached

    identity, confidence, raw_json, product_type = identify_with_llm(image_path, model=llm_model)
    identity = _correct_identity(identity)
    notes = []

    prior = cache.lookup_by_identity(identity)
    if prior:
        prior.identity = _correct_identity(prior.identity)
        prior.notes.append(f"new pHash for known identity ({cache.photo_count_for(identity)} photos so far)")
        prior.phash = phash
        cache.store(prior)
        return prior

    if should_skip_ocr_for_variant(identity.variant):
        notes.append(f"OCR skipped — variant {identity.variant!r} uses foil text")
        ocr_number = None
    else:
        ocr_number = extract_card_number_via_ocr(image_path) if use_ocr else None

    pre_ocr_number = identity.card_number
    identity = disambiguate_card_number(identity, ocr_number, notes)
    source = "llm+ocr_correction" if identity.card_number != pre_ocr_number else "llm"

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
    cache.store(result)
    return result


def identify_and_price(image_path, fetchers, *, cache=None, period_days=30, pair_jp=None):
    result = identify_card(image_path, cache=cache)
    identity = result.identity

    en_query = jp_query = None
    raw_query = CardQuery(card=identity, is_graded=False, condition="NM")
    if identity.language == "english":
        en_query = raw_query
        if pair_jp:
            jp_query = CardQuery(card=pair_jp, is_graded=False, condition="NM")
    else:
        jp_query = raw_query

    report = price_card_side_by_side(en_query=en_query, jp_query=jp_query,
                                      fetchers=fetchers, period_days=period_days)
    return result, report
