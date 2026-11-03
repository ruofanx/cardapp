"""Fair-value prediction depends on live Pokemon TCG API lookups that fail
transiently (500/502/timeouts); they must retry and cache set release dates."""
import asyncio
import os
import sys

import httpx

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

import app as app_module


def _client_factory(responses, calls):
    class FakeClient:
        def __init__(self, *a, **k): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def get(self, url, params=None):
            calls.append(url)
            r = responses.pop(0)
            if isinstance(r, Exception):
                raise r
            return r
    return FakeClient


def _ok(body):
    return httpx.Response(200, json=body, request=httpx.Request("GET", "http://x"))


def _no_sleep(monkeypatch):
    async def fast(_): return None
    monkeypatch.setattr(app_module.asyncio, "sleep", fast)


def test_retries_transient_failure_then_succeeds(monkeypatch):
    _no_sleep(monkeypatch)
    calls = []
    responses = [httpx.ConnectTimeout("t"), _ok({"data": [{"releaseDate": "2022/05/27"}]})]
    monkeypatch.setattr(httpx, "AsyncClient", _client_factory(responses, calls))
    app_module._SET_RELEASE_CACHE.clear()

    assert asyncio.run(app_module._resolve_set_release_date("Astral Radiance")) == "2022-05-27"
    assert len(calls) == 2


def test_gives_up_after_all_attempts(monkeypatch):
    _no_sleep(monkeypatch)
    calls = []
    responses = [httpx.ConnectTimeout("t")] * 3
    monkeypatch.setattr(httpx, "AsyncClient", _client_factory(responses, calls))
    app_module._SET_RELEASE_CACHE.clear()

    assert asyncio.run(app_module._resolve_set_release_date("Nope")) is None
    assert len(calls) == 3


def test_successful_release_date_is_cached(monkeypatch):
    calls = []
    app_module._SET_RELEASE_CACHE.clear()
    app_module._SET_RELEASE_CACHE["astral radiance"] = "2022-05-27"
    monkeypatch.setattr(httpx, "AsyncClient", _client_factory([], calls))

    assert asyncio.run(app_module._resolve_set_release_date("Astral Radiance")) == "2022-05-27"
    assert calls == []
