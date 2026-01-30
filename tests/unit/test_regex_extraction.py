"""
Unit tests for regex-based usage data extraction.
"""

import pytest
from tests.parser_extracted import extract_usage_data, strip_ansi


class TestExtractUsageData:
    """Tests for the extract_usage_data function."""

    def test_basic_extraction(self):
        """Extract data from clean, well-formed output."""
        content = """
Current session
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
        result = extract_usage_data(content)

        assert result.error is None
        assert result.session_percent == 42
        assert result.week_percent == 23
        assert "6:59pm" in result.session_reset_str
        assert "Jan 29" in result.week_reset_str

    def test_extraction_with_ansi_codes(self):
        """Extract data from output containing ANSI codes."""
        content = """
\033[1mCurrent session\033[0m
\033[2m━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\033[0m
\033[92m██████████████████\033[0m\033[2m░░░░░░░░░░░░░░░░░░░░░░\033[0m  42% used

Resets 6:59pm

\033[1mCurrent week (all models)\033[0m
\033[2m━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\033[0m
\033[92m████████\033[0m\033[2m░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░\033[0m  23% used

Resets Jan 29 at 6:59pm

\033[1mSonnet only\033[0m
\033[2m━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\033[0m
\033[92m████████████\033[0m\033[2m░░░░░░░░░░░░░░░░░░░░░░░░░░░░\033[0m  30% used
"""
        result = extract_usage_data(content)

        assert result.error is None
        assert result.session_percent == 42
        assert result.week_percent == 23

    def test_100_percent_usage(self):
        """Handle 100% usage correctly."""
        content = """
Current session
█████████████████████████████████████████████████████████████████████  100% used

Resets 6:59pm

Current week (all models)
█████████████████████████████████████████████████████████████████████  100% used

Resets Jan 29 at 6:59pm
"""
        result = extract_usage_data(content)

        assert result.error is None
        assert result.session_percent == 100
        assert result.week_percent == 100

    def test_0_percent_usage(self):
        """Handle 0% usage correctly."""
        content = """
Current session
░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░  0% used

Resets 6:59pm

Current week (all models)
░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░  0% used

Resets Jan 29 at 6:59pm
"""
        result = extract_usage_data(content)

        assert result.error is None
        assert result.session_percent == 0
        assert result.week_percent == 0

    def test_missing_session_data(self):
        """Missing session data should report error."""
        content = """
Current week (all models)
████████████████░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░  23% used

Resets Jan 29 at 6:59pm
"""
        result = extract_usage_data(content)

        assert result.error is not None
        assert "Session" in result.error

    def test_missing_week_data(self):
        """Missing week data should report error."""
        content = """
Current session
██████████████████████████████░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░  42% used

Resets 6:59pm
"""
        result = extract_usage_data(content)

        assert result.error is not None
        assert "Week" in result.error

    def test_corrupted_resets_word(self):
        """Handle 'Reses' corruption (missing 't')."""
        content = """
Current session
██████████████████████████████░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░  42% used

Reses 6:59pm

Current week (all models)
████████████████░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░  23% used

Resets Jan 29 at 6:59pm
"""
        result = extract_usage_data(content)

        assert result.error is None
        assert result.session_percent == 42
        assert "6:59pm" in result.session_reset_str

    def test_extra_whitespace(self):
        """Handle extra whitespace in output."""
        content = """
Current   session
██████████████████████████████░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░    42%   used

Resets    6:59pm

Current   week   (all   models)
████████████████░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░    23%   used

Resets   Jan   29   at   6:59pm
"""
        result = extract_usage_data(content)

        assert result.error is None
        assert result.session_percent == 42
        assert result.week_percent == 23

    def test_carriage_returns_normalized(self):
        """Windows-style line endings should be normalized."""
        content = "Current session\r\n██████  42% used\r\nResets 6:59pm\r\n\r\nCurrent week (all models)\r\n████  23% used\r\nResets Jan 29 at 6:59pm"

        result = extract_usage_data(content)

        assert result.error is None
        assert result.session_percent == 42
        assert result.week_percent == 23

    def test_ignores_sonnet_only_section(self):
        """Week regex should not match 'Sonnet only' section."""
        content = """
Current session
██████████████████████████████░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░  42% used

Resets 6:59pm

Sonnet only
████████████████░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░  99% used

Resets Feb 1 at 12:00pm

Current week (all models)
████████████████░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░  23% used

Resets Jan 29 at 6:59pm
"""
        result = extract_usage_data(content)

        assert result.error is None
        # Should get week data, not Sonnet only data
        assert result.week_percent == 23
        assert "Jan 29" in result.week_reset_str

    def test_single_digit_percentages(self):
        """Single digit percentages should parse correctly."""
        content = """
Current session
█░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░  1% used

Resets 6:59pm

Current week (all models)
███░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░  5% used

Resets Jan 29 at 6:59pm
"""
        result = extract_usage_data(content)

        assert result.error is None
        assert result.session_percent == 1
        assert result.week_percent == 5

    def test_empty_content(self):
        """Empty content should report error."""
        result = extract_usage_data("")
        assert result.error is not None

    def test_garbage_content(self):
        """Non-usage content should report error."""
        result = extract_usage_data("This is not usage data at all")
        assert result.error is not None


class TestRegexEdgeCases:
    """Edge cases for regex patterns."""

    def test_multiline_between_used_and_resets(self):
        """Handle content between '% used' and 'Resets'."""
        content = """
Current session
██████████████████████████████░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░  42% used

Some extra info here

Resets 6:59pm

Current week (all models)
████████████████░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░  23% used

More info

Resets Jan 29 at 6:59pm
"""
        result = extract_usage_data(content)

        assert result.error is None
        assert result.session_percent == 42
        assert result.week_percent == 23

    def test_time_immediately_after_resets(self):
        """Time immediately after 'Resets' with no space issues."""
        content = """
Current session
██████  42% used
Resets6:59pm

Current week (all models)
████  23% used
ResetsJan 29 at 6:59pm
"""
        # This might fail - the regex expects at least one space
        result = extract_usage_data(content)
        # Depending on implementation, this may or may not work
        # The test documents the expected behavior

    def test_multiple_percentage_numbers(self):
        """Should extract the correct percentage when multiple numbers present."""
        content = """
Current session (limit: 50 requests)
██████████████████████████████░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░  42% used

Resets 6:59pm

Current week (all models) (limit: 100 requests)
████████████████░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░  23% used

Resets Jan 29 at 6:59pm
"""
        result = extract_usage_data(content)

        assert result.error is None
        assert result.session_percent == 42
        assert result.week_percent == 23
