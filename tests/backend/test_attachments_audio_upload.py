"""
@file_name: test_attachments_audio_upload.py
@author: NarraNexus
@date: 2026-05-02
@description: Integration tests for audio upload + transcription wiring.

Covers the contract between the upload route and audio_transcription:
  - audio/* MIME → is_transcription_available + transcribe_audio called
  - non-audio MIME → neither called, transcript fields stay None
  - Transcribe success → response carries the transcript
  - Transcribe returns None (no provider, error, etc.) → response has
    transcript=None but transcription_available reflects pre-check

Strategy:
  - Build a minimal FastAPI app with just the attachments router
  - Patch is_transcription_available + transcribe_audio at the module
    they're imported into (the route does a function-local import, so
    the patch target is the audio_transcription module itself)
  - Use TestClient for in-process HTTP — no real server needed
  - Patch store_uploaded_attachment to a tmp path so we don't need a
    workspace setup
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.routes import agents_attachments as attachments_mod
from xyz_agent_context.utils import audio_transcription as at_mod


@pytest.fixture
def upload_app(monkeypatch, tmp_path):
    """FastAPI app exposing only the attachments router, with the
    storage layer redirected to tmp_path so we don't need workspace
    bootstrap. MIME sniffing is bypassed: we set the MIME via the
    test's `Content-Type` and patch `_sniff_mime_type` to honour it."""
    app = FastAPI()
    app.include_router(attachments_mod.router, prefix="/api/agents")

    # Bypass libmagic — return whatever MIME was handed in via the
    # uploaded file's Content-Type header. Tests control the MIME from
    # the call site this way.
    def _sniff(file, raw_bytes):
        return file.content_type or "application/octet-stream"

    monkeypatch.setattr(attachments_mod, "_sniff_mime_type", _sniff)

    # Redirect storage to tmp_path so the route can complete without
    # needing a workspace.
    def _fake_store(agent_id, user_id, *, raw_bytes, original_name, mime_type):
        target = tmp_path / f"att_{abs(hash(original_name)) & 0xffffffff:08x}{Path(original_name).suffix}"
        target.write_bytes(raw_bytes)
        return target.stem, target

    monkeypatch.setattr(attachments_mod, "store_uploaded_attachment", _fake_store)

    return app


def _post_audio(client, mime_type: str = "audio/wav", filename: str = "voice.wav"):
    return client.post(
        "/api/agents/agent_x/attachments?user_id=user_y",
        files={"file": (filename, b"\x00" * 1024, mime_type)},
    )


def test_upload_audio_with_provider_returns_transcript(upload_app, monkeypatch):
    """is_transcription_available=True + transcribe_audio→"hi" → response carries transcript."""
    monkeypatch.setattr(
        at_mod, "is_transcription_available", AsyncMock(return_value=True)
    )
    monkeypatch.setattr(
        at_mod, "transcribe_audio", AsyncMock(return_value="hello world")
    )

    client = TestClient(upload_app)
    resp = _post_audio(client)

    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is True
    assert body["mime_type"] == "audio/wav"
    assert body["transcript"] == "hello world"
    assert body["transcription_available"] is True
    assert body["category"] == "media"


def test_upload_audio_anthropic_only_user(upload_app, monkeypatch):
    """User has no OpenAI-protocol provider → available=False, transcribe NOT called."""
    monkeypatch.setattr(
        at_mod, "is_transcription_available", AsyncMock(return_value=False)
    )
    transcribe_mock = AsyncMock(return_value="should not be called")
    monkeypatch.setattr(at_mod, "transcribe_audio", transcribe_mock)

    client = TestClient(upload_app)
    resp = _post_audio(client)

    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is True
    assert body["transcript"] is None
    assert body["transcription_available"] is False
    transcribe_mock.assert_not_called()


def test_upload_audio_transcribe_returns_none(upload_app, monkeypatch):
    """available=True but transcribe_audio→None (e.g. timeout) → response keeps available=True, transcript=None."""
    monkeypatch.setattr(
        at_mod, "is_transcription_available", AsyncMock(return_value=True)
    )
    monkeypatch.setattr(
        at_mod, "transcribe_audio", AsyncMock(return_value=None)
    )

    client = TestClient(upload_app)
    resp = _post_audio(client)

    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is True
    assert body["transcript"] is None
    # available STILL True — user has the capability, this single call failed
    assert body["transcription_available"] is True


def test_upload_non_audio_no_transcribe_call(upload_app, monkeypatch):
    """PNG upload → neither availability check nor transcribe_audio called.
    Both transcript fields stay None (not False — that would suggest the
    user lacks transcription capability, which is a separate signal)."""
    available_mock = AsyncMock(return_value=True)
    transcribe_mock = AsyncMock(return_value="x")
    monkeypatch.setattr(at_mod, "is_transcription_available", available_mock)
    monkeypatch.setattr(at_mod, "transcribe_audio", transcribe_mock)

    client = TestClient(upload_app)
    resp = client.post(
        "/api/agents/agent_x/attachments?user_id=user_y",
        files={"file": ("cat.png", b"\x89PNG\r\n\x1a\n" + b"\x00" * 100, "image/png")},
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is True
    assert body["mime_type"] == "image/png"
    assert body["category"] == "image"
    assert body["transcript"] is None
    # Critical: None, not False — the field doesn't apply to this upload
    assert body["transcription_available"] is None
    available_mock.assert_not_called()
    transcribe_mock.assert_not_called()


def test_upload_audio_passes_user_id_through_to_transcribe(upload_app, monkeypatch):
    """user_id must reach transcribe_audio so per-user provider lookup works."""
    monkeypatch.setattr(
        at_mod, "is_transcription_available", AsyncMock(return_value=True)
    )
    transcribe_mock = AsyncMock(return_value="ok")
    monkeypatch.setattr(at_mod, "transcribe_audio", transcribe_mock)

    client = TestClient(upload_app)
    _post_audio(client)

    transcribe_mock.assert_called_once()
    kwargs = transcribe_mock.call_args.kwargs
    assert kwargs["user_id"] == "user_y"
