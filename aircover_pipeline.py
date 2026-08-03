"""
End-to-end Aircover pipeline.

Logs in, pulls meetings for a date range, runs a coaching template against
each meeting, and writes per-meeting JSON outputs.

Usage:
    # List the coaching templates your account can use
    python aircover_pipeline.py --list-templates

    # Run a template across a date range
    python aircover_pipeline.py \\
        --start 2026-01-01 --end 2026-03-31 \\
        --template-id <TEMPLATE_ID> \\
        --output-dir ./out

    # Test on a small batch first
    python aircover_pipeline.py \\
        --start 2026-01-01 --end 2026-03-31 \\
        --template-id <TEMPLATE_ID> \\
        --output-dir ./out --limit 5

    # SSO-only account — paste bearer token from the Aircover web app
    python aircover_pipeline.py \\
        --access-token "eyJ..." --refresh-token "eyJ..." \\
        --start 2026-01-01 --end 2026-03-31 \\
        --template-id <TEMPLATE_ID> --output-dir ./out

Credentials are read from CLI flags, then environment variables, then a
.env file in the current directory (in that order). See .env.example.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

from aircover_client import (
    AircoverClient,
    AircoverFetchAllFailed,
    __version__,
)

log = logging.getLogger("aircover_pipeline")

# Exit codes
EXIT_OK = 0
EXIT_PARTIAL_FAILURE = 1
EXIT_AUTH_OR_TOTAL_FAILURE = 2
EXIT_CRASH = 3


def _setup_logging(verbose: bool) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s  %(levelname)-7s  %(message)s",
        datefmt="%H:%M:%S",
    )
    # SECURITY: even when --verbose enables DEBUG, suppress urllib3's
    # connectionpool DEBUG logging — it would log full request URLs
    # including the refresh_token query parameter on /refresh_token/.
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    logging.getLogger("urllib3.connectionpool").setLevel(logging.WARNING)


def _parse_iso_date(value: str, flag_name: str) -> str:
    """Validate that `value` is a YYYY-MM-DD string. Returns the string
    on success; raises argparse-friendly error on failure."""
    try:
        datetime.strptime(value, "%Y-%m-%d")
    except ValueError:
        raise argparse.ArgumentTypeError(
            f"{flag_name} must be in YYYY-MM-DD format (got: {value!r})"
        )
    return value


def _atomic_write_json(path: Path, payload: dict | list) -> None:
    """Write JSON atomically: write to a sibling .tmp file with restrictive
    permissions, fsync, then os.replace into final path. Prevents
    half-written files from being misinterpreted by --resume."""
    path.parent.mkdir(parents=True, exist_ok=True)
    # NamedTemporaryFile in same dir so os.replace is atomic (same filesystem)
    fd, tmp_path = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent),
    )
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(payload, f, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.chmod(tmp_path, 0o600)
        os.replace(tmp_path, path)
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def _build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description=(
            "Pull Aircover meetings for a date range and run a coaching "
            "template against each one."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--version", action="version", version=f"aircover-pipeline {__version__}")

    auth = p.add_argument_group("Authentication (or set in .env)")
    auth.add_argument(
        "--username",
        help=(
            "Aircover username. WARNING: passing secrets on the CLI exposes "
            "them to shell history and process listings. Prefer the .env "
            "file or env vars for credentials."
        ),
    )
    auth.add_argument("--password", help="Aircover password (see --username warning).")
    auth.add_argument(
        "--access-token",
        help=(
            "Bearer JWT. Use this for SSO-only accounts that can't log in "
            "with username/password. See README.md for how to obtain one. "
            "Prefer AIRCOVER_ACCESS_TOKEN in .env over the CLI flag."
        ),
    )
    auth.add_argument(
        "--refresh-token",
        help=(
            "Bearer refresh JWT. Paired with --access-token. Without it, "
            "auto-refresh is disabled and the access token will expire "
            "after ~1 hour."
        ),
    )
    auth.add_argument(
        "--base-url",
        help=(
            "Override the API base URL (default: https://api.aircover.ai). "
            "Set AIRCOVER_BASE_URL in .env for the same effect. Use to point "
            "at a staging environment."
        ),
    )
    auth.add_argument(
        "--api-token",
        help=(
            "Static API key (e.g. ac_...). Requires --customer-org. "
            "No login or token refresh needed. "
            "Prefer AIRCOVER_API_TOKEN in .env over the CLI flag."
        ),
    )
    auth.add_argument(
        "--customer-org",
        help=(
            "Customer org domain (e.g. postman.com). Sent as the "
            "X-Aircover-Org header on every API request. Required "
            "when using --api-token. "
            "Set AIRCOVER_CUSTOMER_ORG in .env for the same effect."
        ),
    )

    p.add_argument(
        "--list-templates",
        action="store_true",
        help="List coaching templates available to your account and exit.",
    )

    sel = p.add_argument_group("Selection (required unless --list-templates)")
    sel.add_argument(
        "--start",
        type=lambda v: _parse_iso_date(v, "--start"),
        help="Start date YYYY-MM-DD",
    )
    sel.add_argument(
        "--end",
        type=lambda v: _parse_iso_date(v, "--end"),
        help="End date YYYY-MM-DD",
    )
    sel.add_argument("--template-id", help="Coaching template id")
    sel.add_argument(
        "--limit",
        type=int,
        help="Cap on number of meetings to process. Useful for testing.",
    )
    sel.add_argument(
        "--exclude-domains",
        help=(
            "Comma-separated domains to drop from results. Matches the "
            "first segment of each meeting's deal_id (everything before "
            "the first '/')."
        ),
    )
    sel.add_argument(
        "--filter-emails",
        help=(
            "Comma-separated emails. Keep only meetings whose notes_sent_to "
            "field includes at least one of these."
        ),
    )
    sel.add_argument(
        "--filter-teams",
        help=(
            "Comma-separated team IDs. Keep only meetings whose team_ids "
            "field includes at least one of these. Meetings with no team "
            "assignments are dropped."
        ),
    )

    out = p.add_argument_group("Output")
    out.add_argument(
        "--output-dir",
        help=(
            "Where to write meetings.json, agent-outputs/*, summary.json, "
            "and failures.json. Created if it doesn't exist."
        ),
    )
    out.add_argument(
        "--resume",
        action="store_true",
        help=(
            "Skip meetings that already have an agent-outputs/agent_<id>.json "
            "file. Re-run after a crash without redoing work."
        ),
    )
    out.add_argument(
        "--sleep-ms",
        type=int,
        default=300,
        help="Delay between coaching calls in ms (default 300).",
    )
    out.add_argument(
        "--dry-run",
        action="store_true",
        help=(
            "Pull and filter meetings, write meetings.json, but skip the "
            "per-meeting coaching calls. Useful to preview how many "
            "meetings would be processed before committing the time."
        ),
    )
    out.add_argument(
        "--verbose",
        action="store_true",
        help="Enable debug logging.",
    )
    return p


def filter_by_excluded_domains(
    meetings: list[dict],
    excluded_csv: str,
) -> list[dict]:
    """Drop meetings whose deal_id starts with any excluded domain.

    `excluded_csv` is comma-separated. The deal_id format is
    '<domain>/<id>'; we split on the first '/' and compare the
    lowercased domain segment.
    """
    if not excluded_csv:
        return meetings
    excluded = {d.strip().lower() for d in excluded_csv.split(",") if d.strip()}
    if not excluded:
        return meetings

    def keep(m):
        deal = (m.get("deal_id") or "").strip()
        if not deal:
            return True  # no deal_id, can't match
        return deal.split("/")[0].lower() not in excluded

    return [m for m in meetings if keep(m)]


def filter_by_emails(meetings: list[dict], wanted_csv: str) -> list[dict]:
    """Keep meetings whose notes_sent_to contains any of the wanted emails.

    Uses exact-match on parsed emails (not substring), so
    'alice@example.com' won't match 'notalice@example.com'.
    """
    if not wanted_csv:
        return meetings
    wanted = {e.strip().lower() for e in wanted_csv.split(",") if e.strip()}
    if not wanted:
        return meetings

    def has_email(m):
        attendees_csv = (m.get("notes_sent_to") or "").lower()
        if not attendees_csv:
            return False
        attendees = {a.strip() for a in attendees_csv.split(",") if a.strip()}
        return bool(attendees & wanted)

    return [m for m in meetings if has_email(m)]


def filter_by_team_ids(meetings: list[dict], wanted_csv: str) -> list[dict]:
    """Keep meetings whose team_ids includes any of the wanted team IDs.

    Mirrors the team-filter mode in the Apps Script dialog: split the
    meeting's team_ids comma-separated string, check intersection with
    the wanted set. Meetings with no team_ids are dropped.
    """
    if not wanted_csv:
        return meetings
    wanted = {t.strip() for t in wanted_csv.split(",") if t.strip()}
    if not wanted:
        return meetings

    def has_team(m):
        team_csv = (m.get("team_ids") or "").strip()
        if not team_csv:
            return False
        meeting_teams = {t.strip() for t in team_csv.split(",")}
        return bool(meeting_teams & wanted)

    return [m for m in meetings if has_team(m)]


def _list_templates(client: AircoverClient) -> None:
    templates = client.get_templates()
    if not templates:
        log.warning("No templates available for this account.")
        return
    log.info("Available coaching templates (%d):", len(templates))
    for t in templates:
        print(f"  {t['name']}")
        print(f"    id: {t['id']}\n")


def _require(parser: argparse.ArgumentParser, args, names: list[str]) -> None:
    missing = [n for n in names if not getattr(args, n.replace("-", "_"))]
    if missing:
        parser.error(
            "Missing required: " + ", ".join("--" + n for n in missing)
        )


def main(argv: list[str] | None = None) -> int:
    parser = _build_arg_parser()
    args = parser.parse_args(argv)
    _setup_logging(args.verbose)

    try:
        client = AircoverClient(
            username=args.username,
            password=args.password,
            access_token=args.access_token,
            refresh_token=args.refresh_token,
            base_url=args.base_url,
            customer_org=args.customer_org,
            api_token=args.api_token,
        )
        client.login()  # no-op if bearer-token auth
    except (ValueError, RuntimeError) as e:
        log.error("Auth setup failed: %s", e)
        return EXIT_AUTH_OR_TOTAL_FAILURE

    if args.list_templates:
        _list_templates(client)
        return EXIT_OK

    _require(parser, args, ["start", "end", "template-id", "output-dir"])

    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    agent_dir = out_dir / "agent-outputs"
    agent_dir.mkdir(exist_ok=True)

    log.info("Pulling meetings: %s -> %s", args.start, args.end)
    try:
        meetings = client.get_meetings(args.start, args.end)
    except AircoverFetchAllFailed as e:
        log.error("Meeting fetch failed entirely: %s", e)
        return EXIT_AUTH_OR_TOTAL_FAILURE
    log.info("Got %d unique meetings before filters", len(meetings))

    before = len(meetings)
    if args.exclude_domains:
        meetings = filter_by_excluded_domains(meetings, args.exclude_domains)
        log.info(
            "After --exclude-domains: %d (removed %d)",
            len(meetings), before - len(meetings),
        )
        before = len(meetings)

    if args.filter_emails:
        meetings = filter_by_emails(meetings, args.filter_emails)
        log.info(
            "After --filter-emails: %d (removed %d)",
            len(meetings), before - len(meetings),
        )
        before = len(meetings)

    if args.filter_teams:
        meetings = filter_by_team_ids(meetings, args.filter_teams)
        log.info(
            "After --filter-teams: %d (removed %d)",
            len(meetings), before - len(meetings),
        )

    # `meetings_in_window` reflects the full filtered population, captured
    # BEFORE --resume and --limit pruning. Use it for "how many candidates
    # exist in this window" reporting.
    meetings_in_window = len(meetings)

    meetings_path = out_dir / "meetings.json"
    _atomic_write_json(meetings_path, meetings)
    log.info("Wrote %s (%d candidates)", meetings_path, meetings_in_window)

    if not meetings:
        # Still write summary.json so audit / monitoring can tell the run
        # happened — important for cron and CI consumers.
        _atomic_write_json(out_dir / "summary.json", {
            "schema_version": "1.0",
            "package_version": __version__,
            "ran_at": datetime.now(timezone.utc).isoformat(),
            "start": args.start,
            "end": args.end,
            "template_id": args.template_id,
            "base_url": client.base_url,
            "filters": {
                "exclude_domains": args.exclude_domains or None,
                "filter_emails": args.filter_emails or None,
                "filter_teams": args.filter_teams or None,
                "limit": args.limit,
            },
            "meetings_in_window": 0,
            "processed_this_run": 0,
            "success": 0,
            "failed": 0,
            "skipped_resume": 0,
        })
        log.info("No meetings to process. Done.")
        return 0

    if args.dry_run:
        # In dry-run we don't touch agent-outputs, so --resume / --limit
        # don't affect the preview count. Report the full window.
        preview_count = meetings_in_window
        if args.limit:
            preview_count = min(meetings_in_window, args.limit)
        log.info(
            "--dry-run: %d candidate meetings, template %s. Skipping "
            "coaching calls.", preview_count, args.template_id,
        )
        _atomic_write_json(out_dir / "summary.json", {
            "schema_version": "1.0",
            "package_version": __version__,
            "ran_at": datetime.now(timezone.utc).isoformat(),
            "dry_run": True,
            "start": args.start,
            "end": args.end,
            "template_id": args.template_id,
            "base_url": client.base_url,
            "filters": {
                "exclude_domains": args.exclude_domains or None,
                "filter_emails": args.filter_emails or None,
                "filter_teams": args.filter_teams or None,
                "limit": args.limit,
            },
            "meetings_in_window": meetings_in_window,
        })
        return EXIT_OK

    # ── --resume first, --limit second ────────────────────────────────────
    # Order matters: --limit means "process up to N MORE meetings this run."
    # If --resume is also set, we skip already-done rows BEFORE applying
    # the limit, so --limit=10 always processes up to 10 new rows even when
    # 1000 are already done. (Previously --limit applied to the whole list,
    # so --limit=10 with 9 already-done returned only 1 new row.)
    existing: set[str] = set()
    if args.resume:
        # Strip the 'agent_' prefix by slicing (not str.replace, which would
        # replace all occurrences).
        existing = {
            p.stem[len("agent_"):]
            for p in agent_dir.glob("agent_*.json")
            if p.stem.startswith("agent_")
        }
        meetings = [m for m in meetings if m["id"] not in existing]
        if existing:
            log.info(
                "--resume: %d candidates already have output, %d remain to process",
                len(existing), len(meetings),
            )

    if args.limit:
        before_limit = len(meetings)
        meetings = meetings[: args.limit]
        if before_limit > args.limit:
            log.info("Capped this run to --limit %d (of %d remaining)", args.limit, before_limit)

    sleep_s = max(args.sleep_ms, 0) / 1000.0
    success = 0
    failed: list[dict] = []
    # `meetings` is already pruned for --resume and --limit by this point,
    # so every row in the list will be attempted.
    total_to_run = len(meetings)
    log.info(
        "Running template %s on %d meetings...",
        args.template_id, total_to_run,
    )

    processed_this_run = 0
    for m in meetings:
        out = client.get_agent_output(m["id"], args.template_id)
        if out:
            _atomic_write_json(
                agent_dir / f"agent_{m['id']}.json",
                {
                    "meeting_id": m["id"],
                    "deal_id": m.get("deal_id", ""),
                    "date": m.get("date", ""),
                    "template_id": args.template_id,
                    "properties": out,
                },
            )
            success += 1
        else:
            failed.append({
                "meeting_id": m["id"],
                "deal_id": m.get("deal_id", ""),
                "date": m.get("date", ""),
            })
        processed_this_run += 1
        if processed_this_run % 10 == 0:
            log.info(
                "Progress: %d/%d  (success: %d, failed: %d)",
                processed_this_run, total_to_run, success, len(failed),
            )
        if sleep_s > 0:
            time.sleep(sleep_s)

    if failed:
        _atomic_write_json(out_dir / "failures.json", failed)

    summary = {
        "schema_version": "1.0",
        "package_version": __version__,
        "ran_at": datetime.now(timezone.utc).isoformat(),
        "start": args.start,
        "end": args.end,
        "template_id": args.template_id,
        "base_url": client.base_url,
        "filters": {
            "exclude_domains": args.exclude_domains or None,
            "filter_emails": args.filter_emails or None,
            "filter_teams": args.filter_teams or None,
            "limit": args.limit,
        },
        "meetings_in_window": meetings_in_window,
        "processed_this_run": processed_this_run,
        "success": success,
        "failed": len(failed),
        "skipped_resume": len(existing),
    }
    _atomic_write_json(out_dir / "summary.json", summary)

    log.info("Done.")
    log.info("  Success: %d", success)
    log.info("  Failed:  %d", len(failed))
    if existing:
        log.info("  Skipped (resume): %d", len(existing))
    log.info("  Output:  %s", out_dir)

    # Exit codes:
    #   0 = all attempted rows succeeded (or none to process)
    #   1 = partial failure (some succeeded, some failed)
    #   2 = auth/setup error OR every attempted row failed
    #   3 = uncaught exception (wired in __main__ below)
    if processed_this_run > 0 and success == 0:
        return EXIT_AUTH_OR_TOTAL_FAILURE
    if failed:
        return EXIT_PARTIAL_FAILURE
    return EXIT_OK


if __name__ == "__main__":
    try:
        sys.exit(main())
    except SystemExit:
        raise
    except KeyboardInterrupt:
        log.error("Interrupted by user.")
        sys.exit(EXIT_CRASH)
    except Exception as e:
        log.exception("Uncaught exception: %s", e)
        sys.exit(EXIT_CRASH)
