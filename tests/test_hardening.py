"""Unit tests for the v1.0.1 hardening: base_url override, retry/backoff
on 429/5xx, all-failed signaling, malformed-JSON tolerance, atomic writes,
filter substring-bug fix, __version__."""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import requests

from aircover_client import (
    AircoverAPIError,
    AircoverClient,
    AircoverFetchAllFailed,
    __version__,
)
from aircover_pipeline import _atomic_write_json, _parse_iso_date


# ── __version__ ───────────────────────────────────────────────────────────


def test_version_string_is_semver_ish():
    parts = __version__.split(".")
    assert len(parts) >= 2
    for p in parts:
        assert p.isdigit()


# ── base_url override ─────────────────────────────────────────────────────


class TestBaseURL:
    def test_default_base_url(self, monkeypatch):
        monkeypatch.delenv("AIRCOVER_BASE_URL", raising=False)
        client = AircoverClient(access_token="x")
        assert client.base_url == "https://api.aircover.ai"

    def test_constructor_overrides_env(self, monkeypatch):
        monkeypatch.setenv("AIRCOVER_BASE_URL", "https://from-env.example.com")
        client = AircoverClient(access_token="x", base_url="https://override.example.com")
        assert client.base_url == "https://override.example.com"

    def test_env_var_used_when_no_constructor(self, monkeypatch):
        monkeypatch.setenv("AIRCOVER_BASE_URL", "https://stageapi.aircover.ai")
        client = AircoverClient(access_token="x")
        assert client.base_url == "https://stageapi.aircover.ai"

    def test_trailing_slash_stripped(self):
        client = AircoverClient(access_token="x", base_url="https://api.aircover.ai/")
        assert client.base_url == "https://api.aircover.ai"

    def test_base_url_is_read_only_property(self):
        client = AircoverClient(access_token="x")
        with pytest.raises(AttributeError):
            client.base_url = "https://bad.example.com"  # type: ignore[misc]


# ── get_meetings: distinguishes all-failed from empty ─────────────────────


class TestGetMeetingsFailureSignal:
    def test_all_chunks_failed_raises(self):
        client = AircoverClient(access_token="x")
        bad_resp = MagicMock(status_code=500, text="server error")
        with patch.object(client, "get", return_value=bad_resp):
            with pytest.raises(AircoverFetchAllFailed):
                client.get_meetings("2026-01-01", "2026-01-31")

    def test_partial_failure_returns_what_succeeded(self):
        client = AircoverClient(access_token="x")
        ok_payload = {
            "data": [{"meetings": {"2026-01-15T10:00:00Z": {"id": "m1"}}}]
        }
        ok_resp = MagicMock(status_code=200)
        ok_resp.json.return_value = ok_payload
        bad_resp = MagicMock(status_code=500, text="server error")
        # 6-month range -> 2 chunks. First OK, second fails.
        with patch.object(client, "get", side_effect=[ok_resp, bad_resp]):
            meetings = client.get_meetings("2026-01-01", "2026-06-30")
        assert [m["id"] for m in meetings] == ["m1"]  # no raise

    def test_empty_window_returns_empty_list(self):
        client = AircoverClient(access_token="x")
        empty = MagicMock(status_code=200)
        empty.json.return_value = {"data": [{"meetings": {}}]}
        with patch.object(client, "get", return_value=empty):
            assert client.get_meetings("2026-01-01", "2026-01-31") == []


# ── Retry on 429 / 5xx ────────────────────────────────────────────────────


class TestRetryAndBackoff:
    def test_5xx_retried_then_succeeds(self):
        client = AircoverClient(
            access_token="x", refresh_token="r", max_retries=3,
        )
        resp_503 = MagicMock(status_code=503, headers={})
        resp_200 = MagicMock(status_code=200, headers={})
        with patch(
            "aircover_client.requests.request",
            side_effect=[resp_503, resp_503, resp_200],
        ) as mock_req, patch("aircover_client.time.sleep") as mock_sleep:
            result = client._request("GET", "https://api.aircover.ai/test")
        assert result is resp_200
        assert mock_req.call_count == 3
        assert mock_sleep.call_count == 2

    def test_429_respects_retry_after_seconds(self):
        client = AircoverClient(access_token="x", max_retries=2)
        resp_429 = MagicMock(status_code=429, headers={"Retry-After": "0.5"})
        resp_200 = MagicMock(status_code=200, headers={})
        sleep_calls = []
        with patch(
            "aircover_client.requests.request",
            side_effect=[resp_429, resp_200],
        ), patch("aircover_client.time.sleep", side_effect=sleep_calls.append):
            result = client._request("GET", "https://api.aircover.ai/test")
        assert result is resp_200
        assert sleep_calls == [0.5]

    def test_gives_up_after_max_retries(self):
        client = AircoverClient(access_token="x", max_retries=2)
        resp_500 = MagicMock(status_code=500, headers={})
        with patch(
            "aircover_client.requests.request",
            return_value=resp_500,
        ), patch("aircover_client.time.sleep"):
            result = client._request("GET", "https://api.aircover.ai/test")
        assert result is resp_500  # surfaces the last failure


# ── Malformed-JSON tolerance ──────────────────────────────────────────────


class TestMalformedJSONTolerance:
    def test_login_raises_aircover_api_error_on_bad_json(self):
        client = AircoverClient(username="a@b.com", password="x")
        resp = MagicMock()
        resp.raise_for_status = MagicMock()
        resp.json.side_effect = ValueError("invalid json")
        with patch("aircover_client.requests.post", return_value=resp):
            with pytest.raises(AircoverAPIError):
                client.login()

    def test_get_meetings_skips_chunk_with_bad_json(self):
        client = AircoverClient(access_token="x")
        bad = MagicMock(status_code=200)
        bad.json.side_effect = ValueError("bad")
        good = MagicMock(status_code=200)
        good.json.return_value = {
            "data": [{"meetings": {"2026-01-15T10:00:00Z": {"id": "m1"}}}]
        }
        # 2 chunks: first bad-JSON, second good. Should return m1, not raise.
        with patch.object(client, "get", side_effect=[bad, good]):
            meetings = client.get_meetings("2026-01-01", "2026-06-30")
        assert [m["id"] for m in meetings] == ["m1"]

    def test_get_agent_output_returns_none_on_bad_json(self):
        client = AircoverClient(access_token="x")
        resp = MagicMock(status_code=200)
        resp.json.side_effect = ValueError("bad")
        with patch.object(client, "get", return_value=resp):
            assert client.get_agent_output("meeting1", "tpl1") is None

    def test_get_agent_output_returns_none_on_missing_fields(self):
        client = AircoverClient(access_token="x")
        resp = MagicMock(status_code=200)
        resp.json.return_value = {"data": [{"body": {}}]}  # missing 'properties'
        with patch.object(client, "get", return_value=resp):
            assert client.get_agent_output("meeting1", "tpl1") is None

    def test_get_templates_returns_empty_on_bad_json(self):
        client = AircoverClient(access_token="x")
        resp = MagicMock(status_code=200)
        resp.json.side_effect = ValueError("bad")
        with patch.object(client, "get", return_value=resp):
            assert client.get_templates() == []

    def test_get_templates_skips_malformed_template_entries(self):
        client = AircoverClient(access_token="x")
        resp = MagicMock(status_code=200)
        resp.json.return_value = {
            "data": [{"templates": [
                {"id": "t1", "name": "Good"},
                "not a dict",                              # skipped
                {"id": "t2"},                              # missing name → skipped
                {"name": "no id"},                         # missing id → skipped
                {"id": "t3", "name": "Also good"},
            ]}]
        }
        with patch.object(client, "get", return_value=resp):
            out = client.get_templates()
        assert [t["id"] for t in out] == ["t1", "t3"]


# ── Atomic write helper ───────────────────────────────────────────────────


class TestAtomicWriteJSON:
    def test_writes_payload(self, tmp_path):
        path = tmp_path / "test.json"
        _atomic_write_json(path, {"hello": "world"})
        assert json.loads(path.read_text()) == {"hello": "world"}

    def test_no_leftover_tmp_files_on_success(self, tmp_path):
        path = tmp_path / "test.json"
        _atomic_write_json(path, [1, 2, 3])
        leftovers = list(tmp_path.glob(".test.json*.tmp"))
        assert leftovers == []

    def test_file_permissions_restrictive(self, tmp_path):
        path = tmp_path / "secrets.json"
        _atomic_write_json(path, {"token": "x"})
        mode = path.stat().st_mode & 0o777
        # On POSIX, expect 0o600. Don't enforce on Windows where chmod is a no-op.
        import platform
        if platform.system() != "Windows":
            assert mode == 0o600


# ── _parse_iso_date validator ─────────────────────────────────────────────


class TestParseISODate:
    def test_valid_date_returns_string(self):
        assert _parse_iso_date("2026-03-15", "--start") == "2026-03-15"

    def test_invalid_format_raises(self):
        import argparse
        with pytest.raises(argparse.ArgumentTypeError, match="YYYY-MM-DD"):
            _parse_iso_date("03/15/2026", "--start")

    def test_bad_value_raises(self):
        import argparse
        with pytest.raises(argparse.ArgumentTypeError):
            _parse_iso_date("2026-13-01", "--end")  # month 13 doesn't exist
