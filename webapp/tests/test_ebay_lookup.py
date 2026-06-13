"""Tests for ebay_lookup._fetch_ebay_html — direct vs ScraperAPI-routed fetch."""
from __future__ import annotations

import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

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
