#!/usr/bin/env python3
"""
Minimal test suite for cc_usage.py parser.

Tests parsing against golden fixtures to ensure the parser correctly extracts
session and weekly usage data from various output formats.
"""

import json
import pytest
import sys
from datetime import datetime
from pathlib import Path

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from cc_usage import (
    parse_usage,
    strip_ansi,
    extract_percentage,
    extract_reset_string,
    parse_reset_time,
    validate_result,
    ParseResult,
)


# --- Fixtures Directory ---
FIXTURES_DIR = Path(__file__).parent / "fixtures" / "golden"


# --- Helper Functions ---
def load_fixture(name: str) -> tuple[str, dict]:
    """Load a fixture file and its expected values."""
    txt_path = FIXTURES_DIR / f"{name}.txt"
    json_path = FIXTURES_DIR / f"{name}.expected.json"

    content = txt_path.read_text()
    expected = json.loads(json_path.read_text())

    return content, expected


def get_all_fixtures() -> list[str]:
    """Get list of all fixture names (without extensions)."""
    fixtures = []
    for txt_file in FIXTURES_DIR.glob("*.txt"):
        name = txt_file.stem
        json_file = FIXTURES_DIR / f"{name}.expected.json"
        if json_file.exists():
            fixtures.append(name)
    return fixtures


# --- Unit Tests ---
class TestStripAnsi:
    """Test ANSI escape sequence removal."""

    def test_strips_color_codes(self):
        text = "\033[92mGreen\033[0m Normal"
        assert strip_ansi(text) == "Green Normal"

    def test_strips_bold(self):
        text = "\033[1mBold\033[0m"
        assert strip_ansi(text) == "Bold"

    def test_strips_complex_sequences(self):
        text = "\033[38;5;196mRed\033[0m"
        assert strip_ansi(text) == "Red"

    def test_preserves_normal_text(self):
        text = "Normal text with no codes"
        assert strip_ansi(text) == text


class TestExtractPercentage:
    """Test percentage extraction strategies."""

    def test_extracts_from_simple_text(self):
        text = "Current session\n42% used"
        assert extract_percentage(text, "Current session") == 42

    def test_extracts_from_progress_bar_line(self):
        text = "Current session\n████████░░░░  42% used"
        assert extract_percentage(text, "Current session") == 42

    def test_returns_none_for_missing_section(self):
        text = "Some other content"
        assert extract_percentage(text, "Current session") is None

    def test_handles_zero_percent(self):
        text = "Current session\n0% used"
        assert extract_percentage(text, "Current session") == 0

    def test_handles_100_percent(self):
        text = "Current session\n100% used"
        assert extract_percentage(text, "Current session") == 100

    def test_ignores_out_of_range(self):
        text = "Current session\n150% used"
        assert extract_percentage(text, "Current session") is None


class TestExtractResetString:
    """Test reset time string extraction."""

    def test_extracts_time_only(self):
        text = "Current session\n42% used\nResets 6:59pm"
        result = extract_reset_string(text, "Current session")
        assert result == "6:59pm"

    def test_extracts_date_and_time(self):
        text = "Current week (all models)\n23% used\nResets Jan 29 at 6:59pm"
        result = extract_reset_string(text, "Current week (all models)")
        assert result == "Jan 29 at 6:59pm"

    def test_returns_none_for_missing(self):
        text = "Current session\n42% used"
        result = extract_reset_string(text, "Current session")
        assert result is None


class TestParseResetTime:
    """Test reset time parsing."""

    def test_parses_time_only(self):
        now = datetime(2026, 1, 28, 14, 0)  # 2pm on Jan 28
        result = parse_reset_time("6:59pm", window_hours=5, now=now)

        assert result is not None
        assert result.hour == 18
        assert result.minute == 59

    def test_parses_date_and_time(self):
        now = datetime(2026, 1, 28, 14, 0)
        result = parse_reset_time("Jan 29 at 6:59pm", window_hours=168, now=now)

        assert result is not None
        assert result.month == 1
        assert result.day == 29
        assert result.hour == 18
        assert result.minute == 59

    def test_stale_session_time_returns_none(self):
        now = datetime(2026, 1, 28, 20, 0)  # 8pm on Jan 28
        # 6:59pm is over an hour in the past — adding a day would put it
        # ~23h away, far beyond the 5h session window.  Correct behaviour
        # is to return None (stale data).
        result = parse_reset_time("6:59pm", window_hours=5, now=now)
        assert result is None

    def test_adjusts_past_time_within_window(self):
        # Time just barely in the past — adding 1 day keeps it within
        # the session window (window_hours + 1h buffer = 6h, and the
        # adjusted time is ~23.7h away — only valid for larger windows).
        # For a 24h window, adding 1 day should work.
        now = datetime(2026, 1, 28, 20, 0)  # 8pm
        result = parse_reset_time("6:59pm", window_hours=24, now=now)
        assert result is not None
        assert result > now
        assert result.day == 29
        assert result.hour == 18

    def test_returns_none_for_garbage(self):
        result = parse_reset_time("not a time", window_hours=5)
        assert result is None


class TestValidateResult:
    """Test result validation."""

    def test_accepts_valid_result(self):
        now = datetime(2026, 1, 28, 14, 0)
        result = ParseResult(
            session_percent=42,
            session_reset_str="6:59pm",
            session_reset_dt=datetime(2026, 1, 28, 18, 59),
            week_percent=23,
            week_reset_str="Jan 29 at 6:59pm",
            week_reset_dt=datetime(2026, 1, 29, 18, 59),
        )

        valid, msg = validate_result(result, now=now)
        assert valid is True
        assert msg is None

    def test_rejects_missing_session_percent(self):
        result = ParseResult(
            week_percent=23,
        )

        valid, msg = validate_result(result)
        assert valid is False
        assert "session" in msg.lower()

    def test_rejects_missing_week_percent(self):
        result = ParseResult(
            session_percent=42,
        )

        valid, msg = validate_result(result)
        assert valid is False
        assert "week" in msg.lower()

    def test_rejects_with_error(self):
        result = ParseResult(
            session_percent=42,
            week_percent=23,
            error="Something went wrong",
        )

        valid, msg = validate_result(result)
        assert valid is False


# --- Golden Fixture Tests ---
class TestGoldenFixtures:
    """Test parsing against golden fixtures."""

    @pytest.mark.parametrize("fixture_name", get_all_fixtures())
    def test_fixture(self, fixture_name: str):
        """Parse fixture and verify extracted values match expected."""
        content, expected = load_fixture(fixture_name)
        result = parse_usage(content)

        # Check session percent
        assert result.session_percent == expected["session_percent"], \
            f"Session percent mismatch: got {result.session_percent}, expected {expected['session_percent']}"

        # Check week percent
        assert result.week_percent == expected["week_percent"], \
            f"Week percent mismatch: got {result.week_percent}, expected {expected['week_percent']}"

        # Check reset strings match
        assert result.session_reset_str == expected["session_reset_str"], \
            f"Session reset string mismatch: got {result.session_reset_str!r}, expected {expected['session_reset_str']!r}"

        assert result.week_reset_str == expected["week_reset_str"], \
            f"Week reset string mismatch: got {result.week_reset_str!r}, expected {expected['week_reset_str']!r}"


# --- Integration Test ---
class TestIntegration:
    """Integration tests for full parse flow."""

    def test_parses_standard_output(self):
        """Test parsing standard format without ANSI codes."""
        content = """Current session
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
██████████████████████████████░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░  42% used

Resets 6:59pm

Current week (all models)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
████████████████░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░  23% used

Resets Jan 29 at 6:59pm

Sonnet only
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
██████████████████████░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░  30% used
"""
        result = parse_usage(content)

        assert result.session_percent == 42
        assert result.week_percent == 23
        assert result.session_reset_str == "6:59pm"
        assert result.week_reset_str == "Jan 29 at 6:59pm"
        assert result.error is None


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
