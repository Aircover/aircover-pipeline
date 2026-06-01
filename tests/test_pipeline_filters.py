"""Unit tests for the pipeline's filter helpers."""
from __future__ import annotations

import pytest

from aircover_pipeline import (
    filter_by_emails,
    filter_by_excluded_domains,
    filter_by_team_ids,
)


SAMPLE = [
    {"id": "m1", "deal_id": "example.com/d1", "team_ids": "1, 2, 3",
     "notes_sent_to": "alice@example.com, bob@example.com"},
    {"id": "m2", "deal_id": "other.io/d2", "team_ids": "7",
     "notes_sent_to": "carol@other.io"},
    {"id": "m3", "deal_id": "", "team_ids": "",
     "notes_sent_to": "internal@your-company.com"},
    {"id": "m4", "deal_id": "EXAMPLE.COM/d4", "team_ids": "5,3",
     "notes_sent_to": ""},  # case test
]


# ── filter_by_excluded_domains ────────────────────────────────────────────


class TestExcludeDomains:
    def test_empty_filter_returns_all(self):
        assert filter_by_excluded_domains(SAMPLE, "") == SAMPLE
        assert filter_by_excluded_domains(SAMPLE, "   ") == SAMPLE

    def test_drops_matching_domain(self):
        out = filter_by_excluded_domains(SAMPLE, "example.com")
        ids = [m["id"] for m in out]
        # m1 and m4 are example.com (case-insensitive); m2 and m3 stay
        assert "m1" not in ids
        assert "m4" not in ids
        assert "m2" in ids
        assert "m3" in ids  # empty deal_id passes through

    def test_multiple_excluded_domains(self):
        out = filter_by_excluded_domains(SAMPLE, "example.com,other.io")
        # m1, m2, m4 excluded; only m3 (empty deal_id) remains
        assert [m["id"] for m in out] == ["m3"]

    def test_case_insensitive(self):
        # Excluding EXAMPLE.COM should drop both example.com and EXAMPLE.COM rows
        out = filter_by_excluded_domains(SAMPLE, "EXAMPLE.COM")
        ids = [m["id"] for m in out]
        assert "m1" not in ids
        assert "m4" not in ids

    def test_empty_deal_id_passes_through(self):
        # Row m3 has empty deal_id — can't be filtered by domain
        out = filter_by_excluded_domains(SAMPLE, "your-company.com")
        assert "m3" in [m["id"] for m in out]

    def test_whitespace_around_csv_entries(self):
        out = filter_by_excluded_domains(SAMPLE, "  example.com  ,  other.io  ")
        assert [m["id"] for m in out] == ["m3"]


# ── filter_by_emails ──────────────────────────────────────────────────────


class TestFilterEmails:
    def test_empty_filter_returns_all(self):
        assert filter_by_emails(SAMPLE, "") == SAMPLE
        assert filter_by_emails(SAMPLE, "   ") == SAMPLE

    def test_keeps_matching_attendee(self):
        out = filter_by_emails(SAMPLE, "alice@example.com")
        assert [m["id"] for m in out] == ["m1"]

    def test_keeps_any_match(self):
        out = filter_by_emails(SAMPLE, "alice@example.com,carol@other.io")
        ids = sorted(m["id"] for m in out)
        assert ids == ["m1", "m2"]

    def test_case_insensitive(self):
        out = filter_by_emails(SAMPLE, "ALICE@EXAMPLE.COM")
        assert [m["id"] for m in out] == ["m1"]

    def test_drops_empty_notes_sent_to(self):
        out = filter_by_emails(SAMPLE, "alice@example.com")
        # m4 has empty notes_sent_to — never matches
        assert "m4" not in [m["id"] for m in out]

    def test_exact_match_not_substring(self):
        # Regression: filter_by_emails used to do substring match, which
        # would incorrectly keep 'notalice@example.com' when filtering for
        # 'alice@example.com'. Now uses set-membership on parsed emails.
        attackers = [
            {"id": "spoofed1", "notes_sent_to": "notalice@example.com"},
            {"id": "spoofed2", "notes_sent_to": "alice@example.com.attacker.net"},
            {"id": "legit", "notes_sent_to": "alice@example.com, bob@x.com"},
        ]
        out = filter_by_emails(attackers, "alice@example.com")
        assert [m["id"] for m in out] == ["legit"]


# ── filter_by_team_ids ────────────────────────────────────────────────────


class TestFilterTeamIds:
    def test_empty_filter_returns_all(self):
        assert filter_by_team_ids(SAMPLE, "") == SAMPLE
        assert filter_by_team_ids(SAMPLE, "   ") == SAMPLE

    def test_keeps_meetings_with_matching_team(self):
        out = filter_by_team_ids(SAMPLE, "7")
        assert [m["id"] for m in out] == ["m2"]

    def test_keeps_any_intersection(self):
        # team 3 is in m1 (1,2,3) and m4 (5,3); not m2; not m3
        out = filter_by_team_ids(SAMPLE, "3")
        assert sorted(m["id"] for m in out) == ["m1", "m4"]

    def test_multiple_wanted_teams(self):
        out = filter_by_team_ids(SAMPLE, "2,7")
        assert sorted(m["id"] for m in out) == ["m1", "m2"]

    def test_drops_meetings_with_no_teams(self):
        # m3 has team_ids=""
        out = filter_by_team_ids(SAMPLE, "1,2,3,5,7")
        assert "m3" not in [m["id"] for m in out]

    def test_whitespace_around_csv_entries(self):
        out = filter_by_team_ids(SAMPLE, "  2 ,  7  ")
        assert sorted(m["id"] for m in out) == ["m1", "m2"]
