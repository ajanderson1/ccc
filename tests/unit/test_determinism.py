"""
Unit tests for parsing determinism - same input should always produce same output.

These tests verify that the parsing logic is deterministic and does not produce
different results on repeated invocations with the same input.
"""

import pytest
import datetime
from tests.parser_extracted import parse_reset_time, cross_validate_reset


class TestParsingDeterminism:
    """Tests that verify parsing produces consistent results."""

    @pytest.fixture
    def base_time(self):
        """Base time for testing: Jan 30, 2026 at 11:47am."""
        return datetime.datetime(2026, 1, 30, 11, 47, 0)

    def test_time_only_determinism(self, base_time):
        """Time-only input should always produce same output."""
        test_input = "6:59pm"
        results = [
            parse_reset_time(test_input, window_hours=5, now=base_time)
            for _ in range(10)
        ]
        assert len(set(results)) == 1, f"Non-deterministic results: {set(results)}"

    def test_date_time_determinism(self, base_time):
        """Date+time input should always produce same output."""
        test_input = "Feb 5 at 7pm"
        results = [
            parse_reset_time(test_input, window_hours=168, now=base_time)
            for _ in range(10)
        ]
        assert len(set(results)) == 1, f"Non-deterministic results: {set(results)}"

    def test_date_time_with_timezone_determinism(self, base_time):
        """Date+time with timezone should always produce same output."""
        test_input = "Feb 5 at 7pm (Europe/Stockholm)"
        results = [
            parse_reset_time(test_input, window_hours=168, now=base_time)
            for _ in range(10)
        ]
        assert len(set(results)) == 1, f"Non-deterministic results: {set(results)}"

    def test_section_text_extraction_determinism(self, base_time):
        """Extraction from section text should be deterministic."""
        section_text = """
        Current week (all models)
        ████████████████░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░  10% used
        Resets Feb 5 at 7pm (Europe/Stockholm)
        """
        results = [
            parse_reset_time("corrupted", window_hours=168, section_text=section_text, now=base_time)
            for _ in range(10)
        ]
        assert len(set(results)) == 1, f"Non-deterministic results: {set(results)}"

    def test_weekly_tomorrow_logic_determinism(self, base_time):
        """Weekly 'tomorrow' logic should be deterministic."""
        # At 11:47am, 9:00am is in the past - should advance to tomorrow
        test_input = "9:00am"
        results = [
            parse_reset_time(test_input, window_hours=168, now=base_time)
            for _ in range(10)
        ]
        assert len(set(results)) == 1, f"Non-deterministic results: {set(results)}"

    def test_year_wrap_determinism(self):
        """Year wrap logic should be deterministic."""
        # In December, seeing January date should be next year
        dec_time = datetime.datetime(2025, 12, 20, 12, 0, 0)
        test_input = "Jan 5 at 6pm"
        results = [
            parse_reset_time(test_input, window_hours=168, now=dec_time)
            for _ in range(10)
        ]
        assert len(set(results)) == 1, f"Non-deterministic results: {set(results)}"
        # And should all be 2026
        assert all(r.year == 2026 for r in results if r is not None)


class TestCrossValidationDeterminism:
    """Tests that cross-validation is deterministic."""

    @pytest.fixture
    def base_time(self):
        return datetime.datetime(2026, 1, 30, 11, 47, 0)

    def test_cross_validation_consistent(self, base_time):
        """Cross-validation should produce same result repeatedly."""
        reset_dt = datetime.datetime(2026, 2, 5, 19, 0, 0)  # Feb 5 at 7pm
        results = [
            cross_validate_reset(reset_dt, 168, "Feb 5 at 7pm", now=base_time)
            for _ in range(10)
        ]
        # All should be None (valid)
        assert all(r is None for r in results)

    def test_cross_validation_invalid_consistent(self, base_time):
        """Cross-validation of invalid times should be consistent."""
        # Reset time in the past
        reset_dt = datetime.datetime(2026, 1, 29, 19, 0, 0)  # Yesterday
        results = [
            cross_validate_reset(reset_dt, 168, "Jan 29 at 7pm", now=base_time)
            for _ in range(10)
        ]
        # All should have same warning
        assert len(set(results)) == 1
        assert results[0] is not None  # Should have warning
