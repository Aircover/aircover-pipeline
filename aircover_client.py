"""
Aircover API client.

Three auth modes:

1. Username + password — call .login() after constructing.
2. Bearer token — pass access_token (and optionally refresh_token) to the
   constructor; no .login() call needed.
3. API token — pass api_token and customer_org; no .login() call needed,
   no refresh. The token is sent as ``Authorization: Bearer <api_token>``
   with a required ``X-Aircover-Org`` header.

Credentials are loaded from .env or environment variables if not passed
explicitly. See .env.example.
"""

from __future__ import annotations

import logging
import os
import random
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional
from urllib.parse import quote

import requests

__version__ = "1.0.3"

BASE_URL_DEFAULT = "https://api.aircover.ai"
LOGIN_PATH = "/auth/login"
REFRESH_PATH = "/refresh_token/"

log = logging.getLogger(__name__)


class AircoverAPIError(RuntimeError):
    """Raised when the API call itself succeeded transport-wise but the
    returned payload was unusable (malformed JSON, missing expected
    fields, etc.). Distinct from network errors and HTTP errors."""


class AircoverFetchAllFailed(RuntimeError):
    """Raised by get_meetings() when every chunk in the date range
    returned a non-200 status. Distinguishes 'API broken' from 'no
    meetings in window' (which returns an empty list)."""


@dataclass
class Credentials:
    """Holds either username/password or bearer tokens (or both)."""
    username: Optional[str] = None
    password: Optional[str] = None
    access_token: Optional[str] = None
    refresh_token: Optional[str] = None

    @property
    def can_login(self) -> bool:
        return bool(self.username and self.password)

    @property
    def has_bearer(self) -> bool:
        return bool(self.access_token)


def chunk_date_range(
    start_date: str,
    end_date: str,
    days: int = 90,
) -> list[tuple[datetime, datetime]]:
    """Split [start_date, end_date] into contiguous chunks of `days` days.

    Returns a list of (chunk_start, chunk_end) datetime tuples whose
    chunk_ends are inclusive and contiguous (no overlap, no gaps).
    Useful for paging large date ranges through APIs that cap the
    window per request.
    """
    start = datetime.strptime(start_date, "%Y-%m-%d")
    end = datetime.strptime(end_date, "%Y-%m-%d")
    if start > end:
        return []
    chunks = []
    cur = start
    while cur <= end:
        chunk_end = min(cur + timedelta(days=days), end)
        chunks.append((cur, chunk_end))
        cur = chunk_end + timedelta(days=1)
    return chunks


class AircoverClient:
    """Thin client for the Aircover REST API.

    Mirrors the auth and refresh behavior used by Aircover's internal
    tooling: proactively refreshes the access token every 50 requests
    to avoid mid-batch 401s, falls back to re-login on refresh failure
    when username/password are available, and emits a clear error when
    neither path works.
    """

    PROACTIVE_REFRESH_EVERY = 50

    def __init__(
        self,
        username: Optional[str] = None,
        password: Optional[str] = None,
        access_token: Optional[str] = None,
        refresh_token: Optional[str] = None,
        env_path: Optional[Path] = None,
        timeout: float = 30.0,
        base_url: Optional[str] = None,
        max_retries: int = 3,
        customer_org: Optional[str] = None,
        api_token: Optional[str] = None,
    ):
        """
        Args:
            username, password: For username/password auth. Call .login()
                after constructing to authenticate.
            access_token, refresh_token: For bearer-token auth. Skip .login();
                tokens are usable immediately.
            api_token: Static API key (e.g. "ac_..."). Requires customer_org.
                No login or refresh needed.
            env_path: Path to a .env file to load credentials from. Defaults
                to ./.env relative to the current working directory.
            timeout: HTTP timeout in seconds for all API calls.
            base_url: Override the API base URL. Defaults to AIRCOVER_BASE_URL
                env var, or https://api.aircover.ai. Use this to point at a
                staging environment.
            max_retries: Max retry attempts on transient errors (429, 5xx).
            customer_org: Customer org domain (e.g. "postman.com"). Sent as
                the X-Aircover-Org header on every request. Required when
                using api_token auth. Falls back to AIRCOVER_CUSTOMER_ORG
                env var / .env.
        """
        self._customer_org = customer_org
        self._api_token = api_token
        self._creds = Credentials(
            username=username,
            password=password,
            access_token=access_token,
            refresh_token=refresh_token,
        )
        self._timeout = timeout
        self._request_count = 0
        self._base_url = (
            base_url
            or os.environ.get("AIRCOVER_BASE_URL")
            or BASE_URL_DEFAULT
        ).rstrip("/")
        self._max_retries = max_retries

        # Fall back to env / .env if nothing was passed directly
        if not (self._api_token or self._creds.has_bearer or self._creds.can_login):
            self._load_env_vars()
            if not (self._api_token or self._creds.has_bearer or self._creds.can_login):
                self._load_dotenv(env_path or Path.cwd() / ".env")

        if self._api_token:
            if not self._customer_org:
                raise ValueError(
                    "--customer-org (or AIRCOVER_CUSTOMER_ORG) is required "
                    "when using API-token auth."
                )
        elif not (self._creds.has_bearer or self._creds.can_login):
            raise ValueError(
                "No Aircover credentials found. Provide one of:\n"
                "  AIRCOVER_USERNAME + AIRCOVER_PASSWORD, or\n"
                "  AIRCOVER_ACCESS_TOKEN (+ AIRCOVER_REFRESH_TOKEN) for "
                "SSO-only accounts, or\n"
                "  AIRCOVER_API_TOKEN + AIRCOVER_CUSTOMER_ORG for "
                "API-token auth.\n"
                "Set these as environment variables or in a .env file."
            )

    @property
    def base_url(self) -> str:
        """Public read-only accessor for the resolved API base URL."""
        return self._base_url

    def _load_env_vars(self) -> None:
        if not self._creds.username:
            self._creds.username = os.environ.get("AIRCOVER_USERNAME")
        if not self._creds.password:
            self._creds.password = os.environ.get("AIRCOVER_PASSWORD")
        if not self._creds.access_token:
            self._creds.access_token = os.environ.get("AIRCOVER_ACCESS_TOKEN")
        if not self._creds.refresh_token:
            self._creds.refresh_token = os.environ.get("AIRCOVER_REFRESH_TOKEN")
        if not self._api_token:
            self._api_token = os.environ.get("AIRCOVER_API_TOKEN")
        if not self._customer_org:
            self._customer_org = os.environ.get("AIRCOVER_CUSTOMER_ORG")

    def _load_dotenv(self, path: Path) -> None:
        """Minimal .env parser — no python-dotenv dependency."""
        if not path.exists():
            return
        cred_keys = {
            "AIRCOVER_USERNAME": "username",
            "AIRCOVER_PASSWORD": "password",
            "AIRCOVER_ACCESS_TOKEN": "access_token",
            "AIRCOVER_REFRESH_TOKEN": "refresh_token",
        }
        with open(path) as f:
            for raw in f:
                line = raw.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, value = line.split("=", 1)
                key = key.strip()
                value = value.strip().strip("'\"")
                attr = cred_keys.get(key)
                if attr and not getattr(self._creds, attr):
                    setattr(self._creds, attr, value)
                if key == "AIRCOVER_API_TOKEN" and not self._api_token:
                    self._api_token = value
                if key == "AIRCOVER_CUSTOMER_ORG" and not self._customer_org:
                    self._customer_org = value

    # ── Auth ──────────────────────────────────────────────────────────────

    def login(self) -> None:
        """Authenticate via /auth/login. No-op if bearer/API tokens are set."""
        if self._api_token:
            log.debug("API-token mode — skipping login()")
            return
        if self._creds.has_bearer and not self._creds.can_login:
            log.debug("Bearer-token mode — skipping login()")
            return
        if not self._creds.can_login:
            raise ValueError(
                "login() called without username/password. If using "
                "bearer-token auth, skip login()."
            )

        response = requests.post(
            f"{self._base_url}{LOGIN_PATH}",
            json={
                "username": self._creds.username,
                "password": self._creds.password,
            },
            timeout=self._timeout,
        )
        response.raise_for_status()
        try:
            data = response.json()
        except ValueError as e:
            raise AircoverAPIError(f"Login response was not valid JSON: {e}") from e
        try:
            self._creds.access_token = data["data"][0]["access_token"]
            self._creds.refresh_token = data["data"][0]["refresh_token"]
        except (KeyError, IndexError, TypeError) as e:
            raise AircoverAPIError(
                f"Login response missing expected fields: {e}"
            ) from e
        self._request_count = 0
        log.info("Logged in as %s", self._creds.username)

    def set_tokens(
        self,
        access_token: str,
        refresh_token: Optional[str] = None,
    ) -> None:
        """Replace stored tokens at runtime (e.g., after grabbing a fresh
        pair from the web app's localStorage)."""
        self._creds.access_token = access_token
        self._creds.refresh_token = refresh_token
        self._request_count = 0

    def _refresh(self, allow_relogin: bool = True) -> bool:
        """Refresh the access token. Returns True on success.

        On failure (network error, expired refresh token, etc.), falls back
        to re-login via username/password if those are available and
        allow_relogin is True.

        Security note: the API requires the refresh token to be passed as
        a query parameter. Verbose / DEBUG-level logging from urllib3 would
        otherwise log the full URL (including the refresh token). The
        urllib3 connectionpool logger is forced to WARNING in setup_logging
        to prevent this leak.
        """
        if self._creds.refresh_token:
            try:
                resp = requests.post(
                    f"{self._base_url}{REFRESH_PATH}"
                    f"?refresh_token={quote(self._creds.refresh_token)}",
                    timeout=self._timeout,
                )
                if resp.status_code in (200, 201):
                    try:
                        data = resp.json()
                        self._creds.access_token = data["data"][0]["access_token"]
                        self._creds.refresh_token = data["data"][0]["refresh_token"]
                        self._request_count = 0
                        return True
                    except (ValueError, KeyError, IndexError, TypeError) as e:
                        log.warning(
                            "Refresh response was malformed (%s); will try re-login.",
                            type(e).__name__,
                        )
                else:
                    log.warning(
                        "Refresh token request returned %s. Will try re-login.",
                        resp.status_code,
                    )
            except requests.RequestException as e:
                log.warning("Refresh token network error: %s. Will try re-login.", e)

        if allow_relogin and self._creds.can_login:
            try:
                self.login()
                return bool(self._creds.access_token)
            except Exception as e:
                log.error("Re-login fallback failed: %s", e)
                return False
        return False

    # ── Request plumbing ──────────────────────────────────────────────────

    def _auth_headers(self) -> dict:
        token = self._api_token or self._creds.access_token
        if not token:
            raise RuntimeError(
                "No access token. Call .login() or pass access_token / "
                "api_token to the constructor."
            )
        headers = {"Authorization": f"Bearer {token}"}
        if self._customer_org:
            headers["X-Aircover-Org"] = self._customer_org
        return headers

    def _request(self, method: str, url: str, **kwargs) -> requests.Response:
        """Authenticated request with proactive refresh, 401 retry, and
        exponential-backoff retry on 429 / 5xx.

        Respects the Retry-After header on 429 when present.
        """
        self._request_count += 1
        if not self._api_token and self._request_count % self.PROACTIVE_REFRESH_EVERY == 0:
            self._refresh()

        attempt = 0
        while True:
            try:
                response = requests.request(
                    method,
                    url,
                    headers=self._auth_headers(),
                    timeout=self._timeout,
                    **kwargs,
                )
            except requests.RequestException as e:
                if attempt >= self._max_retries:
                    raise
                sleep_s = self._backoff(attempt, retry_after=None)
                log.warning(
                    "Network error on %s (attempt %d/%d): %s. Retrying in %.1fs.",
                    url, attempt + 1, self._max_retries, e, sleep_s,
                )
                time.sleep(sleep_s)
                attempt += 1
                continue

            # 401: refresh + retry once with new token, no backoff.
            # Skip for API-token auth — the token is static.
            if not self._api_token and response.status_code == 401 and self._refresh():
                response = requests.request(
                    method,
                    url,
                    headers=self._auth_headers(),
                    timeout=self._timeout,
                    **kwargs,
                )

            # Transient: 429 (rate limit) and 5xx (server). Backoff + retry.
            if response.status_code in (429, 500, 502, 503, 504):
                if attempt >= self._max_retries:
                    return response
                retry_after = response.headers.get("Retry-After")
                sleep_s = self._backoff(attempt, retry_after=retry_after)
                log.warning(
                    "%s returned %d (attempt %d/%d). Retrying in %.1fs.",
                    url, response.status_code, attempt + 1, self._max_retries, sleep_s,
                )
                time.sleep(sleep_s)
                attempt += 1
                continue

            return response

    @staticmethod
    def _backoff(attempt: int, retry_after: Optional[str] = None) -> float:
        """Compute sleep seconds for a retry. Honors Retry-After (seconds form);
        otherwise exponential with jitter."""
        if retry_after:
            try:
                return max(0.0, float(retry_after))
            except ValueError:
                pass  # HTTP-date form not supported; fall back to exponential
        base = min(60.0, 2.0 ** attempt)
        return base + random.uniform(0, base * 0.25)

    def get(self, path: str, params: Optional[dict] = None) -> requests.Response:
        return self._request("GET", f"{self._base_url}{path}", params=params)

    # ── Meetings ──────────────────────────────────────────────────────────

    def get_meetings(self, start_date: str, end_date: str) -> list[dict]:
        """Pull meetings for an ISO date range (YYYY-MM-DD).

        The range is split into 3-month chunks and fetched sequentially.
        Results are deduplicated by meeting id.

        End-date treatment: the end is sent as `<end>T00:00:00Z`, matching
        the production Apps Script behavior. Meetings later in the calendar
        day of `end_date` may be excluded; if you need full-day coverage,
        pass the next day as `--end`.

        Raises:
            AircoverFetchAllFailed: when every chunk returned a non-200
                response (distinguishes "API broken" from "no meetings
                in window", which returns an empty list).
        """
        chunks = chunk_date_range(start_date, end_date, days=90)
        if not chunks:
            return []

        all_meetings: list[dict] = []
        failed_chunks = 0
        for i, (cs, ce) in enumerate(chunks, 1):
            log.info(
                "Fetching meetings chunk %d/%d: %s to %s",
                i, len(chunks), cs.date(), ce.date(),
            )
            resp = self.get(
                "/analytics/",
                params={"start": cs.isoformat() + "Z", "end": ce.isoformat() + "Z"},
            )
            if resp.status_code != 200:
                log.warning(
                    "Meetings chunk %d returned %s: %s",
                    i, resp.status_code, resp.text[:200],
                )
                failed_chunks += 1
                continue
            try:
                payload = resp.json()
            except ValueError as e:
                log.warning(
                    "Meetings chunk %d: response was not valid JSON (%s)",
                    i, e,
                )
                failed_chunks += 1
                continue
            all_meetings.extend(self._parse_meetings(payload))

        if failed_chunks == len(chunks):
            raise AircoverFetchAllFailed(
                f"All {len(chunks)} /analytics chunks failed. "
                f"Check API status, auth scope, and network."
            )

        seen = set()
        unique = []
        for m in all_meetings:
            if m["id"] not in seen:
                seen.add(m["id"])
                unique.append(m)
        return unique

    @staticmethod
    def _parse_meetings(payload: dict) -> list[dict]:
        out: list[dict] = []
        if not (payload.get("data") and payload["data"][0]):
            return out
        meetings = payload["data"][0].get("meetings", {})
        for ts_key, m in meetings.items():
            mid = m.get("id")
            if not mid:
                continue
            out.append({
                "id": mid,
                "deal_id": m.get("deal_id", ""),
                "team_ids": ", ".join(str(t) for t in (m.get("team_ids") or [])),
                "date": ts_key.split("T")[0],
                "notes_sent_to": ", ".join(m.get("notes_sent_to") or []),
            })
        return out

    # ── Coaching agents ───────────────────────────────────────────────────

    def get_templates(self) -> list[dict]:
        """List coaching templates available to the current account."""
        resp = self.get("/coaching-templates")
        if resp.status_code != 200:
            log.warning("/coaching-templates returned %s", resp.status_code)
            return []
        try:
            data = resp.json()
            templates = data["data"][0].get("templates", []) or []
        except (ValueError, KeyError, IndexError, TypeError):
            log.warning("/coaching-templates response was malformed")
            return []
        out = []
        for t in templates:
            if not isinstance(t, dict):
                continue
            tid = t.get("id")
            name = t.get("name")
            if tid and name:
                out.append({"id": tid, "name": name})
        return out

    def get_agent_output(
        self,
        meeting_id: str,
        template_id: str,
    ) -> Optional[dict]:
        """Run a coaching template against one meeting.

        Returns a dict keyed by property id, with `title`, `result`, `score`,
        and `max_score` per property. Returns None if the API call fails or
        the response is malformed — one bad row never breaks the batch.
        """
        resp = self.get(
            "/transcript/coaching",
            params={"meeting_id": meeting_id, "template_id": template_id},
        )
        if resp.status_code != 200:
            log.info(
                "Coaching call failed for %s: HTTP %s body: %s",
                meeting_id, resp.status_code, resp.text[:200],
            )
            return None
        try:
            data = resp.json()
            properties = data["data"][0]["body"]["properties"]
        except (ValueError, KeyError, IndexError, TypeError) as e:
            log.info(
                "Coaching response for %s could not be parsed (%s)",
                meeting_id, type(e).__name__,
            )
            return None
        if not isinstance(properties, dict):
            return None
        return {
            key: {
                "title": (p.get("title", key) if isinstance(p, dict) else key),
                "result": (p.get("result", "N/A") if isinstance(p, dict) else "N/A"),
                "score": (p.get("score", 0) if isinstance(p, dict) else 0),
                "max_score": (p.get("maxScore", 0) if isinstance(p, dict) else 0),
            }
            for key, p in properties.items()
        }
