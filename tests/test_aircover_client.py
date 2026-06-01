"""Unit tests for aircover_client.

No real HTTP — anything that would hit the API is mocked via
unittest.mock so the tests are hermetic and fast.
"""
from __future__ import annotations

import json
from datetime import datetime
from unittest.mock import MagicMock, patch

import pytest

from aircover_client import AircoverClient, chunk_date_range


# ── chunk_date_range ──────────────────────────────────────────────────────


class TestChunkDateRange:
    def test_single_chunk_when_range_fits(self):
        chunks = chunk_date_range("2026-01-01", "2026-03-01", days=90)
        assert len(chunks) == 1
        assert chunks[0] == (datetime(2026, 1, 1), datetime(2026, 3, 1))

    def test_multiple_chunks_when_range_exceeds_window(self):
        chunks = chunk_date_range("2026-01-01", "2026-06-30", days=90)
        # 2026-01-01 + 90d = 2026-04-01 (chunk 1 end)
        # 2026-04-02 + 90d = 2026-06-30 (clipped to end; chunk 2 end)
        assert len(chunks) == 2
        assert chunks[0][0] == datetime(2026, 1, 1)
        assert chunks[0][1] == datetime(2026, 4, 1)
        assert chunks[1][0] == datetime(2026, 4, 2)
        assert chunks[1][1] == datetime(2026, 6, 30)

    def test_chunks_are_contiguous_no_gaps_no_overlap(self):
        chunks = chunk_date_range("2024-01-01", "2025-12-31", days=90)
        from datetime import timedelta
        for i in range(len(chunks) - 1):
            assert chunks[i + 1][0] == chunks[i][1] + timedelta(days=1), (
                f"gap or overlap between chunk {i} and {i+1}"
            )

    def test_empty_when_start_after_end(self):
        assert chunk_date_range("2026-06-01", "2026-01-01", days=90) == []

    def test_single_day_range(self):
        chunks = chunk_date_range("2026-03-15", "2026-03-15", days=90)
        assert len(chunks) == 1
        assert chunks[0] == (datetime(2026, 3, 15), datetime(2026, 3, 15))


# ── AircoverClient construction & .env loading ────────────────────────────


class TestConstructor:
    def test_username_password_via_kwargs(self):
        client = AircoverClient(username="a@b.com", password="x")
        assert client._creds.can_login
        assert not client._creds.has_bearer

    def test_bearer_token_via_kwargs(self):
        client = AircoverClient(access_token="eyJ.body.sig")
        assert client._creds.has_bearer
        assert client._creds.access_token == "eyJ.body.sig"
        assert not client._creds.can_login

    def test_bearer_token_with_refresh(self):
        client = AircoverClient(
            access_token="eyJ.access",
            refresh_token="eyJ.refresh",
        )
        assert client._creds.access_token == "eyJ.access"
        assert client._creds.refresh_token == "eyJ.refresh"

    def test_raises_when_no_credentials(self, tmp_path, monkeypatch):
        # Point env_path to a nonexistent file and clear env vars
        monkeypatch.delenv("AIRCOVER_USERNAME", raising=False)
        monkeypatch.delenv("AIRCOVER_PASSWORD", raising=False)
        monkeypatch.delenv("AIRCOVER_ACCESS_TOKEN", raising=False)
        monkeypatch.delenv("AIRCOVER_REFRESH_TOKEN", raising=False)
        empty = tmp_path / "nonexistent.env"
        with pytest.raises(ValueError, match="No Aircover credentials"):
            AircoverClient(env_path=empty)

    def test_loads_from_env_vars(self, monkeypatch, tmp_path):
        monkeypatch.setenv("AIRCOVER_USERNAME", "env@user.com")
        monkeypatch.setenv("AIRCOVER_PASSWORD", "envpass")
        monkeypatch.delenv("AIRCOVER_ACCESS_TOKEN", raising=False)
        monkeypatch.delenv("AIRCOVER_REFRESH_TOKEN", raising=False)
        client = AircoverClient(env_path=tmp_path / "nope.env")
        assert client._creds.username == "env@user.com"
        assert client._creds.password == "envpass"

    def test_loads_bearer_tokens_from_env(self, monkeypatch, tmp_path):
        monkeypatch.delenv("AIRCOVER_USERNAME", raising=False)
        monkeypatch.delenv("AIRCOVER_PASSWORD", raising=False)
        monkeypatch.setenv("AIRCOVER_ACCESS_TOKEN", "eyJ.acc")
        monkeypatch.setenv("AIRCOVER_REFRESH_TOKEN", "eyJ.ref")
        client = AircoverClient(env_path=tmp_path / "nope.env")
        assert client._creds.access_token == "eyJ.acc"
        assert client._creds.refresh_token == "eyJ.ref"

    def test_loads_from_dotenv_file(self, tmp_path, monkeypatch):
        for var in [
            "AIRCOVER_USERNAME", "AIRCOVER_PASSWORD",
            "AIRCOVER_ACCESS_TOKEN", "AIRCOVER_REFRESH_TOKEN",
        ]:
            monkeypatch.delenv(var, raising=False)
        env_file = tmp_path / ".env"
        env_file.write_text(
            "# comment line\n"
            "AIRCOVER_USERNAME=file@user.com\n"
            "AIRCOVER_PASSWORD='quoted_password'\n"
            "AIRCOVER_ACCESS_TOKEN=\"eyJ.from_file\"\n"
            "\n"
        )
        client = AircoverClient(env_path=env_file)
        assert client._creds.username == "file@user.com"
        assert client._creds.password == "quoted_password"
        assert client._creds.access_token == "eyJ.from_file"

    def test_kwargs_override_env(self, monkeypatch, tmp_path):
        monkeypatch.setenv("AIRCOVER_USERNAME", "env@user.com")
        monkeypatch.setenv("AIRCOVER_PASSWORD", "envpass")
        client = AircoverClient(
            username="kw@user.com",
            password="kwpass",
            env_path=tmp_path / "nope.env",
        )
        assert client._creds.username == "kw@user.com"
        assert client._creds.password == "kwpass"


# ── set_tokens ────────────────────────────────────────────────────────────


class TestSetTokens:
    def test_set_tokens_replaces_existing(self):
        client = AircoverClient(access_token="old.access", refresh_token="old.ref")
        client._request_count = 42
        client.set_tokens("new.access", "new.ref")
        assert client._creds.access_token == "new.access"
        assert client._creds.refresh_token == "new.ref"
        assert client._request_count == 0  # reset

    def test_set_tokens_without_refresh(self):
        client = AircoverClient(access_token="x")
        client.set_tokens("just-access")
        assert client._creds.access_token == "just-access"
        assert client._creds.refresh_token is None


# ── _parse_meetings ───────────────────────────────────────────────────────


class TestParseMeetings:
    def test_empty_payload(self):
        assert AircoverClient._parse_meetings({}) == []
        assert AircoverClient._parse_meetings({"data": []}) == []
        assert AircoverClient._parse_meetings({"data": [None]}) == []
        assert AircoverClient._parse_meetings({"data": [{"meetings": {}}]}) == []

    def test_parses_meetings_to_expected_shape(self):
        payload = {
            "data": [{
                "meetings": {
                    "2026-03-15T14:00:00Z": {
                        "id": "abc123",
                        "deal_id": "example.com/deal-1",
                        "team_ids": [1, 2, 3],
                        "notes_sent_to": ["alice@example.com", "bob@example.com"],
                    },
                    "2026-03-16T10:00:00Z": {
                        "id": "def456",
                        # missing deal_id, team_ids, notes_sent_to
                    },
                }
            }]
        }
        out = AircoverClient._parse_meetings(payload)
        assert len(out) == 2
        by_id = {m["id"]: m for m in out}
        assert by_id["abc123"]["deal_id"] == "example.com/deal-1"
        assert by_id["abc123"]["team_ids"] == "1, 2, 3"
        assert by_id["abc123"]["date"] == "2026-03-15"
        assert by_id["abc123"]["notes_sent_to"] == "alice@example.com, bob@example.com"
        # Missing fields default to empty string
        assert by_id["def456"]["deal_id"] == ""
        assert by_id["def456"]["team_ids"] == ""
        assert by_id["def456"]["notes_sent_to"] == ""

    def test_skips_meetings_without_id(self):
        payload = {"data": [{"meetings": {"2026-03-15T14:00:00Z": {"deal_id": "x/y"}}}]}
        assert AircoverClient._parse_meetings(payload) == []


# ── Auth: login + refresh + re-login fallback ─────────────────────────────


class TestLoginAndRefresh:
    def test_login_skipped_in_bearer_mode_without_credentials(self):
        client = AircoverClient(access_token="eyJ.body.sig")
        # No HTTP call should happen; just returns
        with patch("aircover_client.requests.post") as mock_post:
            client.login()
            mock_post.assert_not_called()
        assert client._creds.access_token == "eyJ.body.sig"

    def test_login_posts_credentials(self):
        client = AircoverClient(username="a@b.com", password="x")
        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "data": [{"access_token": "new.access", "refresh_token": "new.ref"}]
        }
        mock_resp.raise_for_status = MagicMock()
        with patch("aircover_client.requests.post", return_value=mock_resp) as mock_post:
            client.login()
            mock_post.assert_called_once()
            args, kwargs = mock_post.call_args
            assert "/auth/login" in args[0]
            assert kwargs["json"]["username"] == "a@b.com"
            assert kwargs["json"]["password"] == "x"
        assert client._creds.access_token == "new.access"
        assert client._creds.refresh_token == "new.ref"

    def test_refresh_uses_existing_refresh_token(self):
        client = AircoverClient(access_token="old.acc", refresh_token="old.ref")
        mock_resp = MagicMock()
        mock_resp.status_code = 201
        mock_resp.json.return_value = {
            "data": [{"access_token": "fresh.acc", "refresh_token": "fresh.ref"}]
        }
        with patch("aircover_client.requests.post", return_value=mock_resp):
            assert client._refresh() is True
        assert client._creds.access_token == "fresh.acc"
        assert client._creds.refresh_token == "fresh.ref"

    def test_refresh_falls_back_to_login_on_failure(self):
        """When refresh fails but username/password are available, we
        re-login instead of giving up."""
        client = AircoverClient(
            username="a@b.com",
            password="x",
            access_token="old.acc",
            refresh_token="bad.ref",
        )
        refresh_resp = MagicMock(status_code=401)
        login_resp = MagicMock()
        login_resp.raise_for_status = MagicMock()
        login_resp.json.return_value = {
            "data": [{"access_token": "relogin.acc", "refresh_token": "relogin.ref"}]
        }
        with patch(
            "aircover_client.requests.post",
            side_effect=[refresh_resp, login_resp],
        ) as mock_post:
            assert client._refresh() is True
            assert mock_post.call_count == 2  # refresh attempt + login
        assert client._creds.access_token == "relogin.acc"

    def test_refresh_returns_false_when_no_options_left(self):
        """No refresh token, no credentials, no re-login fallback → False."""
        client = AircoverClient(access_token="lonely.acc")  # bearer-only, no refresh
        assert client._refresh() is False


# ── Request layer: 401 retry ──────────────────────────────────────────────


class TestRequestRetry:
    def test_401_triggers_refresh_and_retry(self):
        client = AircoverClient(access_token="bearer.acc", refresh_token="bearer.ref")

        # First call: 401. Refresh: success. Retry: 200.
        resp_401 = MagicMock(status_code=401)
        resp_200 = MagicMock(status_code=200)
        refresh_resp = MagicMock(status_code=200)
        refresh_resp.json.return_value = {
            "data": [{"access_token": "fresh.acc", "refresh_token": "fresh.ref"}]
        }

        with patch(
            "aircover_client.requests.request",
            side_effect=[resp_401, resp_200],
        ) as mock_req, patch(
            "aircover_client.requests.post", return_value=refresh_resp
        ):
            result = client._request("GET", "https://api.aircover.ai/test")
            assert result is resp_200
            assert mock_req.call_count == 2  # initial + retry
