"""
@file_name: test_web_search_brave_tool.py
@author: Bin Liang
@date: 2026-04-21
@description: Brave-backed web_search tool contract tests.

Mocks httpx.AsyncClient so tests never hit the real Brave endpoint.
"""
from __future__ import annotations

import asyncio
import time
from unittest.mock import MagicMock

import httpx
import pytest

from xyz_agent_context.module.common_tools_module._common_tools_impl import (
    web_search_brave_tool as brave,
)


def _make_response(status_code: int, json_data: dict | None = None, text: str = "") -> MagicMock:
    """Build a minimal httpx.Response-like mock."""
    resp = MagicMock(spec=httpx.Response)
    resp.status_code = status_code
    resp.json = MagicMock(return_value=json_data or {})
    resp.text = text
    return resp


def _patch_async_client(monkeypatch, handler):
    """Patch httpx.AsyncClient so each .get() call is routed to `handler(url, params, headers)`.

    handler must be an async callable returning a Response-like mock.
    """
    class _FakeClient:
        def __init__(self, *args, **kwargs):
            pass
        async def __aenter__(self):
            return self
        async def __aexit__(self, *a):
            return False
        async def get(self, url, params=None, headers=None):
            return await handler(url, params, headers)

    monkeypatch.setattr(brave.httpx, "AsyncClient", _FakeClient)


# -------- happy path -------------------------------------------------------


@pytest.mark.asyncio
async def test_happy_path_single_query_returns_formatted_results(monkeypatch):
    """One query → Brave returns 2 results → bundle shape matches DDGS output."""
    async def handler(url, params, headers):
        assert url == brave._ENDPOINT
        assert params["q"] == "asyncio cancel"
        assert headers["X-Subscription-Token"] == "test-key"
        return _make_response(200, {
            "web": {
                "results": [
                    {
                        "title": "Asyncio Docs",
                        "url": "https://docs.python.org/3/library/asyncio.html",
                        "description": "asyncio library reference",
                    },
                    {
                        "title": "Real Python: asyncio",
                        "url": "https://realpython.com/async-io/",
                        "description": "Tutorial on async",
                    },
                ]
            }
        })

    _patch_async_client(monkeypatch, handler)
    bundles = await brave._search_with_retry(["asyncio cancel"], 5, "test-key")

    assert len(bundles) == 1
    b = bundles[0]
    assert b["query"] == "asyncio cancel"
    assert b["error"] is None
    assert len(b["results"]) == 2
    assert b["results"][0] == {
        "title": "Asyncio Docs",
        "url": "https://docs.python.org/3/library/asyncio.html",
        "snippet": "asyncio library reference",
    }


@pytest.mark.asyncio
async def test_multiple_queries_run_in_parallel(monkeypatch):
    """N queries dispatched concurrently — wall time ≈ single-query time."""
    call_times: list[float] = []

    async def handler(url, params, headers):
        call_times.append(time.monotonic())
        await asyncio.sleep(0.2)  # each "request" takes 200ms
        return _make_response(200, {
            "web": {"results": [
                {"title": f"t-{params['q']}", "url": "https://x/", "description": "s"}
            ]}
        })

    _patch_async_client(monkeypatch, handler)
    start = time.monotonic()
    bundles = await brave._search_with_retry(["q1", "q2", "q3"], 5, "test-key")
    elapsed = time.monotonic() - start

    # 3 queries × 200ms sequential = 600ms; parallel = ~200ms + overhead
    assert elapsed < 0.5, f"queries ran sequentially ({elapsed:.2f}s)"
    assert len(bundles) == 3
    assert {b["query"] for b in bundles} == {"q1", "q2", "q3"}


@pytest.mark.asyncio
async def test_empty_queries_returns_empty_no_http_call(monkeypatch):
    """`web_search([])` short-circuits without hitting the network."""
    called = False

    async def handler(url, params, headers):
        nonlocal called
        called = True
        return _make_response(200, {"web": {"results": []}})

    _patch_async_client(monkeypatch, handler)
    bundles = await brave._search_with_retry([], 5, "test-key")
    assert bundles == []
    assert not called


@pytest.mark.asyncio
async def test_max_results_respected(monkeypatch):
    """The `count` query param is clamped to min(max_results, 20)."""
    captured_params: dict = {}

    async def handler(url, params, headers):
        captured_params.update(params)
        return _make_response(200, {"web": {"results": []}})

    _patch_async_client(monkeypatch, handler)
    await brave._search_with_retry(["x"], max_results=30, api_key="test-key")
    assert captured_params["count"] == 20  # clamped to max 20

    await brave._search_with_retry(["x"], max_results=3, api_key="test-key")
    assert captured_params["count"] == 3
