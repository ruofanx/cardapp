"""
Card photo storage via Supabase Storage REST API.

Replaces local uploads/ directory. Photos are stored in the
SUPABASE_STORAGE_BUCKET bucket and accessed via signed URLs.
Falls back gracefully when not configured (SUPABASE_URL or
SUPABASE_SERVICE_KEY not set).
"""
import os
import requests
from dotenv import load_dotenv

load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), "../../.env"))

_SUPABASE_URL = os.environ.get("SUPABASE_URL", "")
_SERVICE_KEY  = os.environ.get("SUPABASE_SERVICE_KEY", "")
_BUCKET       = os.environ.get("SUPABASE_STORAGE_BUCKET", "card-photos")


def _headers():
    return {
        "Authorization": f"Bearer {_SERVICE_KEY}",
        "apikey": _SERVICE_KEY,
    }


def upload_photo(filename: str, data: bytes, content_type: str = "image/jpeg") -> str:
    """Upload photo bytes to Supabase Storage. Returns the storage path."""
    url = f"{_SUPABASE_URL}/storage/v1/object/{_BUCKET}/{filename}"
    resp = requests.post(
        url,
        headers={**_headers(), "Content-Type": content_type},
        data=data,
    )
    resp.raise_for_status()
    return filename  # storage path, stored in cards.photo_path


def get_signed_url(storage_path: str, expires_in: int = 3600) -> str:
    """Return a time-limited signed URL for a stored photo."""
    url = f"{_SUPABASE_URL}/storage/v1/object/sign/{_BUCKET}/{storage_path}"
    resp = requests.post(
        url,
        headers={**_headers(), "Content-Type": "application/json"},
        json={"expiresIn": expires_in},
    )
    resp.raise_for_status()
    signed = resp.json().get("signedURL", "")
    return f"{_SUPABASE_URL}/storage/v1{signed}" if signed.startswith("/") else signed


def delete_photo(storage_path: str) -> bool:
    """Delete a photo from storage. Returns True if deleted."""
    url = f"{_SUPABASE_URL}/storage/v1/object/{_BUCKET}/{storage_path}"
    resp = requests.delete(url, headers=_headers())
    return resp.status_code in (200, 204)


def is_configured() -> bool:
    """True if Supabase Storage env vars are set."""
    return bool(_SUPABASE_URL and _SERVICE_KEY)
