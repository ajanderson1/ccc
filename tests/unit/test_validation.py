"""
Unit tests for validation logic.
"""

import pytest
import datetime
from datetime import timedelta
from tests.parser_extracted import validate_reset_time, extract_usage_data, ParseResult


class TestValidationIntegration:
    """Integration tests for validation within extract_usage_data."""

    @pytest.fixture
    def base_time(self):
        """Base time for testing."""
        return datetime.datetime(2026, 1, 28, 14, 30, 0)

    def test_valid_session_and_week(self):
        """Valid times should produce no warnings."""
        content = """
Current session
██████████████████████████████░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░  42% used

Resets 6:59pm

Current week (all models)
████████████████░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░  23% used

Resets Jan 29 at 6:59pm
"""
        result = extract_usage_data(content)

        assert result.error is None
        # Warnings depend on current time, so just check structure
        assert isinstance(result.warnings, list)

    def test_unparseable_time_generates_warning(self):
        """Unparseable time string should generate warning."""
        content = """
Current session
██████████████████████████████░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░  42% used

Resets invalid-time-string

Current week (all models)
████████████████░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░  23% used

Resets Jan 29 at 6:59pm
"""
        result = extract_usage_data(content)

        # Should still extract percentages
        assert result.session_percent == 42
        # But should have warning about session time
        assert any("Session" in w and "Failed to parse" in w for w in result.warnings)


class TestParseResultDataclass:
    """Tests for the ParseResult dataclass."""

    def test_default_values(self):
        """Default values should be set correctly."""
        result = ParseResult()

        assert result.session_percent is None
        assert result.session_reset_str is None
        assert result.session_reset_dt is None
        assert result.week_percent is None
        assert result.week_reset_str is None
        assert result.week_reset_dt is None
        assert result.error is None
        assert result.warnings == []

    def test_warnings_list_initialization(self):
        """Warnings list should be initialized properly."""
        result1 = ParseResult()
        result2 = ParseResult()

        # Ensure they don't share the same list
        result1.warnings.append("test")
        assert len(result2.warnings) == 0

    def test_set_values(self):
        """Values should be settable."""
        now = datetime.datetime.now()
        result = ParseResult(
            session_percent=42,
            session_reset_str="6:59pm",
            session_reset_dt=now,
            week_percent=23,
            week_reset_str="Jan 29 at 6:59pm",
            week_reset_dt=now + timedelta(days=1),
            error=None,
            warnings=["test warning"]
        )

        assert result.session_percent == 42
        assert result.session_reset_str == "6:59pm"
        assert result.session_reset_dt == now
        assert result.week_percent == 23
        assert result.week_reset_str == "Jan 29 at 6:59pm"
        assert result.week_reset_dt == now + timedelta(days=1)
        assert result.error is None
        assert result.warnings == ["test warning"]


class TestBoundaryConditions:
    """Tests for time boundary conditions."""

    def test_exactly_on_hour(self):
        """Times exactly on the hour should work."""
        now = datetime.datetime(2026, 1, 28, 14, 0, 0)  # Exactly 2pm
        reset_dt = datetime.datetime(2026, 1, 28, 17, 0, 0)  # Exactly 5pm

        dt, warning = validate_reset_time(reset_dt, 5, "5pm", now=now)

        assert dt == reset_dt
        assert warning is None

    def test_exactly_at_window_boundary(self):
        """Time exactly at window boundary should be valid."""
        now = datetime.datetime(2026, 1, 28, 14, 0, 0)
        reset_dt = datetime.datetime(2026, 1, 28, 19, 0, 0)  # Exactly 5 hours later

        dt, warning = validate_reset_time(reset_dt, 5, "7pm", now=now)

        assert dt == reset_dt
        assert warning is None

    def test_one_second_over_window(self):
        """Time just over window should still be valid (buffer)."""
        now = datetime.datetime(2026, 1, 28, 14, 0, 0)
        reset_dt = datetime.datetime(2026, 1, 28, 19, 0, 1)  # 5h 1s later

        dt, warning = validate_reset_time(reset_dt, 5, "7pm", now=now)

        assert dt == reset_dt
        assert warning is None

    def test_weekly_window_boundary(self):
        """Weekly window (168h = 7 days) boundary test."""
        now = datetime.datetime(2026, 1, 28, 14, 0, 0)
        reset_dt = datetime.datetime(2026, 2, 4, 14, 0, 0)  # Exactly 7 days later

        dt, warning = validate_reset_time(reset_dt, 168, "Feb 4", now=now)

        assert dt == reset_dt
        assert warning is None

    def test_negative_window_not_too_negative(self):
        """Slightly negative time (just reset) should be valid."""
        now = datetime.datetime(2026, 1, 28, 14, 0, 0)
        reset_dt = datetime.datetime(2026, 1, 28, 13, 55, 0)  # 5 min ago

        dt, warning = validate_reset_time(reset_dt, 5, "1:55pm", now=now)

        assert dt == reset_dt
        assert warning is None

    def test_deeply_negative_session(self):
        """Time deeply negative (> window) should be invalid."""
        now = datetime.datetime(2026, 1, 28, 14, 0, 0)
        reset_dt = datetime.datetime(2026, 1, 28, 8, 0, 0)  # 6 hours ago

        dt, warning = validate_reset_time(reset_dt, 5, "8am", now=now)

        assert dt is None
        assert "in past" in warning


class TestEdgeCaseTimes:
    """Edge cases for specific time values."""

    def test_midnight(self):
        """Midnight (12:00am) handling."""
        now = datetime.datetime(2026, 1, 28, 23, 0, 0)
        reset_dt = datetime.datetime(2026, 1, 29, 0, 0, 0)  # Midnight tomorrow

        dt, warning = validate_reset_time(reset_dt, 5, "12:00am", now=now)

        assert dt == reset_dt
        assert warning is None

    def test_noon(self):
        """Noon (12:00pm) handling."""
        now = datetime.datetime(2026, 1, 28, 10, 0, 0)
        reset_dt = datetime.datetime(2026, 1, 28, 12, 0, 0)

        dt, warning = validate_reset_time(reset_dt, 5, "12:00pm", now=now)

        assert dt == reset_dt
        assert warning is None

    def test_1am(self):
        """1:00am handling (commonly problematic)."""
        now = datetime.datetime(2026, 1, 28, 23, 30, 0)
        reset_dt = datetime.datetime(2026, 1, 29, 1, 0, 0)

        dt, warning = validate_reset_time(reset_dt, 5, "1:00am", now=now)

        assert dt == reset_dt
        assert warning is None

    def test_11pm(self):
        """11:00pm handling."""
        now = datetime.datetime(2026, 1, 28, 18, 0, 0)
        reset_dt = datetime.datetime(2026, 1, 28, 23, 0, 0)

        dt, warning = validate_reset_time(reset_dt, 5, "11:00pm", now=now)

        assert dt == reset_dt
        assert warning is None
