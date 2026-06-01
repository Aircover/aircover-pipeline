# Changelog

All notable changes to `aircover-pipeline` are documented here.

Format based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versioning follows [Semantic Versioning](https://semver.org/).

## [Unreleased]

## [1.0.3] - 2026-05-31

### Fixed
- `pyproject.toml` no longer rejected by setuptools ≥79: removed `mailto:` URL from `[project.urls]` (URL values must be HTTP/HTTPS).
- README test-count was stale ("65"); corrected to 71 with expanded coverage description.
- `summary.json` schema description for `meetings_in_window` accurately reflects when the value is captured (before `--resume` and `--limit`).
- README usage sections de-duplicated and renumbered (1–8 sequential).

## [1.0.2] - 2026-05-31

### Fixed
- `--limit` was applied before `--resume` pruning, so `--limit N --resume` could process fewer than N new meetings when prior runs had already finished some. Now `--limit N` always means "up to N new meetings this run."
- Empty post-filter result now writes `summary.json` with zero counts so cron / CI consumers can tell the run happened.
- Removed redundant resume log line.

### Added
- `AircoverClient.base_url` public property (replaces internal `_base_url` access).

## [1.0.1] - 2026-05-31

### Added
- `AircoverFetchAllFailed` exception raised when every `/analytics/` chunk fails — distinguishes API outage from empty result.
- `--base-url` flag and `AIRCOVER_BASE_URL` env var for pointing at a non-production environment (e.g., staging).
- `--version` flag; `summary.json` now records `package_version`, `ran_at` (ISO UTC), `base_url`, and applied filter values for audit trails.
- `--filter-teams` to filter meetings by team ID set membership.
- Exit code 3 for crashes / Ctrl+C (distinct from 1 = partial failure).
- Retry with exponential backoff on 429 / 5xx responses (honors `Retry-After` when present).
- `_parse_iso_date` validator for `--start` / `--end` with friendly argparse error messages.

### Fixed
- All `resp.json()` calls are guarded — malformed payloads no longer crash the batch; the affected row becomes a row-level failure.
- Refresh token no longer leaks under `--verbose`: `urllib3` connectionpool logger forced to WARNING regardless of root level.
- `filter_by_emails` used substring matching, so `alice@example.com` would erroneously match `notalice@example.com`. Now uses set membership on parsed emails.
- Per-meeting output writes are atomic (tempfile + fsync + `os.replace`, `0o600` permissions) so `--resume` cannot skip a half-written file.
- `--resume` ID extraction uses slicing (not `str.replace`), so IDs that contain the substring `agent_` stay intact.
- `meetings_in_window` was double-counting `--resume`'d rows.
- "Coaching call failed" log line elevated from DEBUG to INFO so per-row failures are visible without `--verbose`.
- `get_templates` skips malformed template entries instead of crashing.

## [1.0.0] - 2026-05-31

### Added
- Initial release.
- `AircoverClient` with username/password and bearer-token authentication.
- Automatic token refresh + re-login fallback.
- `get_meetings()` with 3-month date-range chunking and meeting-id deduplication.
- `get_agent_output()` for `/transcript/coaching` per meeting.
- `get_templates()` for `/coaching-templates`.
- CLI `aircover-pipeline` with `--list-templates`, `--start` / `--end`, `--template-id`, `--output-dir`, `--limit`, `--resume`, `--exclude-domains`, `--filter-emails`, `--dry-run`, `--verbose`.
- JSON Schemas for `meetings.json`, `agent-output`, `summary.json`, `failures.json` (draft 2020-12, versioned by filename).
- 41 unit tests (no real HTTP — all networking mocked).
- MIT license.

[Unreleased]: https://github.com/Aircover/aircover-pipeline/compare/v1.0.3...HEAD
[1.0.3]: https://github.com/Aircover/aircover-pipeline/compare/v1.0.2...v1.0.3
[1.0.2]: https://github.com/Aircover/aircover-pipeline/compare/v1.0.1...v1.0.2
[1.0.1]: https://github.com/Aircover/aircover-pipeline/compare/v1.0.0...v1.0.1
[1.0.0]: https://github.com/Aircover/aircover-pipeline/releases/tag/v1.0.0
