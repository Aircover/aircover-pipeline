"""Integration-style tests for main() — exercises the full pipeline driver
with mocked HTTP and a temp output directory. Covers semantics that
unit tests on helpers can't easily catch (e.g., --limit vs --resume
ordering, summary.json contents, atomic writes in the real flow)."""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

import aircover_pipeline
from aircover_pipeline import main


def _meeting(mid: str, deal: str = "", date: str = "2026-03-01") -> dict:
    return {
        "id": mid,
        "deal_id": deal,
        "team_ids": "",
        "date": date,
        "notes_sent_to": "",
    }


def _mock_client(meetings: list[dict], agent_output_per_meeting: dict | None = None):
    """Build a MagicMock AircoverClient that returns the given meetings
    list from get_meetings and returns a successful agent output for
    every meeting (or a specific dict per meeting_id if provided)."""
    client = MagicMock()
    client.base_url = "https://api.aircover.ai"
    client.login = MagicMock()
    client.get_meetings.return_value = meetings

    def _agent(meeting_id, template_id):
        if agent_output_per_meeting is None:
            return {"prop1": {"title": "P1", "result": "r", "score": 1, "max_score": 5}}
        return agent_output_per_meeting.get(meeting_id)

    client.get_agent_output.side_effect = _agent
    return client


def _run(tmp_path: Path, args: list[str], client) -> int:
    with patch.object(aircover_pipeline, "AircoverClient", return_value=client):
        return main(args)


# ── --limit interacts correctly with --resume ─────────────────────────────


class TestLimitResumeOrdering:
    def test_limit_counts_new_rows_not_done_rows(self, tmp_path, monkeypatch):
        """Regression: --limit 1 used to consume the limit on a row that
        was already done (skipped), processing zero new rows. Now --limit
        means 'up to N new rows this run'."""
        monkeypatch.setenv("AIRCOVER_ACCESS_TOKEN", "fake")

        meetings = [_meeting(f"m{i}") for i in range(5)]
        client = _mock_client(meetings)

        # Pre-seed: first 3 meetings are already "done" (output files exist)
        out_dir = tmp_path / "out"
        agent_dir = out_dir / "agent-outputs"
        agent_dir.mkdir(parents=True)
        for i in range(3):
            (agent_dir / f"agent_m{i}.json").write_text("{}")

        exit_code = _run(tmp_path, [
            "--start", "2026-03-01", "--end", "2026-03-31",
            "--template-id", "t1",
            "--output-dir", str(out_dir),
            "--resume",
            "--limit", "1",
        ], client)

        assert exit_code == 0
        # Should have processed exactly 1 new meeting (m3), not 0.
        new_files = sorted(p.name for p in agent_dir.glob("agent_*.json"))
        # m0, m1, m2 from pre-seed + exactly one new (m3 or m4)
        assert len(new_files) == 4
        # The one new file should be the next unprocessed (m3)
        assert "agent_m3.json" in new_files

    def test_limit_alone_caps_total_processed(self, tmp_path, monkeypatch):
        """Without --resume, --limit just caps the total."""
        monkeypatch.setenv("AIRCOVER_ACCESS_TOKEN", "fake")
        meetings = [_meeting(f"m{i}") for i in range(5)]
        client = _mock_client(meetings)

        exit_code = _run(tmp_path, [
            "--start", "2026-03-01", "--end", "2026-03-31",
            "--template-id", "t1",
            "--output-dir", str(tmp_path / "out"),
            "--limit", "2",
        ], client)

        assert exit_code == 0
        agent_files = list((tmp_path / "out" / "agent-outputs").glob("agent_*.json"))
        assert len(agent_files) == 2

    def test_resume_alone_processes_all_remaining(self, tmp_path, monkeypatch):
        monkeypatch.setenv("AIRCOVER_ACCESS_TOKEN", "fake")
        meetings = [_meeting(f"m{i}") for i in range(5)]
        client = _mock_client(meetings)

        out_dir = tmp_path / "out"
        agent_dir = out_dir / "agent-outputs"
        agent_dir.mkdir(parents=True)
        (agent_dir / "agent_m0.json").write_text("{}")
        (agent_dir / "agent_m1.json").write_text("{}")

        exit_code = _run(tmp_path, [
            "--start", "2026-03-01", "--end", "2026-03-31",
            "--template-id", "t1",
            "--output-dir", str(out_dir),
            "--resume",
        ], client)

        assert exit_code == 0
        agent_files = sorted(p.name for p in agent_dir.glob("agent_*.json"))
        # Pre-seed (m0, m1) + new (m2, m3, m4) = 5 total
        assert len(agent_files) == 5


# ── summary.json contents ────────────────────────────────────────────────


class TestSummaryJSON:
    def test_meetings_in_window_not_double_counted_with_resume(self, tmp_path, monkeypatch):
        """meetings_in_window is the filtered population, NOT the population
        plus resume-skip count."""
        monkeypatch.setenv("AIRCOVER_ACCESS_TOKEN", "fake")
        meetings = [_meeting(f"m{i}") for i in range(3)]
        client = _mock_client(meetings)

        out_dir = tmp_path / "out"
        agent_dir = out_dir / "agent-outputs"
        agent_dir.mkdir(parents=True)
        (agent_dir / "agent_m0.json").write_text("{}")  # 1 already done

        _run(tmp_path, [
            "--start", "2026-03-01", "--end", "2026-03-31",
            "--template-id", "t1",
            "--output-dir", str(out_dir),
            "--resume",
        ], client)

        summary = json.loads((out_dir / "summary.json").read_text())
        assert summary["meetings_in_window"] == 3  # not 4 (3 + 1 resumed)
        assert summary["skipped_resume"] == 1
        assert summary["processed_this_run"] == 2
        assert summary["success"] == 2

    def test_summary_records_audit_metadata(self, tmp_path, monkeypatch):
        monkeypatch.setenv("AIRCOVER_ACCESS_TOKEN", "fake")
        meetings = [_meeting("m1")]
        client = _mock_client(meetings)

        _run(tmp_path, [
            "--start", "2026-03-01", "--end", "2026-03-31",
            "--template-id", "tpl-xyz",
            "--output-dir", str(tmp_path / "out"),
            "--filter-emails", "alice@example.com",
        ], client)

        summary = json.loads((tmp_path / "out" / "summary.json").read_text())
        assert summary["schema_version"] == "1.0"
        assert summary["package_version"]  # non-empty
        assert summary["ran_at"]  # ISO timestamp
        assert summary["base_url"] == "https://api.aircover.ai"
        assert summary["filters"]["filter_emails"] == "alice@example.com"
        assert summary["filters"]["exclude_domains"] is None
        assert summary["template_id"] == "tpl-xyz"
