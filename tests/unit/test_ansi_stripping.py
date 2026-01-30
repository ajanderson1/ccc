"""
Unit tests for ANSI escape sequence stripping.
"""

import pytest
from tests.parser_extracted import strip_ansi


class TestStripAnsi:
    """Tests for the strip_ansi function."""

    def test_no_ansi_passthrough(self):
        """Plain text should pass through unchanged."""
        text = "Hello, World!"
        assert strip_ansi(text) == "Hello, World!"

    def test_simple_color_codes(self):
        """Basic color codes should be removed."""
        # Red text
        text = "\033[31mRed Text\033[0m"
        assert strip_ansi(text) == "Red Text"

        # Green text
        text = "\033[32mGreen\033[0m"
        assert strip_ansi(text) == "Green"

        # Bold
        text = "\033[1mBold\033[0m"
        assert strip_ansi(text) == "Bold"

    def test_complex_escape_sequences(self):
        """Complex CSI sequences should be removed."""
        # Cursor movement
        text = "\033[5;10HAt position"
        assert strip_ansi(text) == "At position"

        # Clear screen
        text = "\033[2JCleared"
        assert strip_ansi(text) == "Cleared"

        # Scroll region
        text = "\033[3;10rScrollable"
        assert strip_ansi(text) == "Scrollable"

    def test_multiple_sequences(self):
        """Multiple sequences in one string."""
        text = "\033[1m\033[92mBold Green\033[0m Normal \033[91mRed\033[0m"
        assert strip_ansi(text) == "Bold Green Normal Red"

    def test_256_color_codes(self):
        """256-color codes should be removed."""
        text = "\033[38;5;196mRed256\033[0m"
        assert strip_ansi(text) == "Red256"

    def test_rgb_color_codes(self):
        """24-bit RGB color codes should be removed."""
        text = "\033[38;2;255;0;0mTrueRed\033[0m"
        assert strip_ansi(text) == "TrueRed"

    def test_dim_text(self):
        """Dim/faint text escape sequence."""
        text = "\033[2mDim text\033[0m"
        assert strip_ansi(text) == "Dim text"

    def test_usage_output_patterns(self):
        """Patterns commonly seen in /usage output."""
        # Progress bar with colors
        text = "\033[92m████████\033[0m\033[2m░░░░░░░░\033[0m  50% used"
        result = strip_ansi(text)
        assert "████████" in result
        assert "░░░░░░░░" in result
        assert "50% used" in result

    def test_cursor_position_sequences(self):
        """Cursor positioning that might corrupt time strings."""
        # This is a common source of parsing issues
        text = "Resets \033[K6:59pm\033[0m"
        assert strip_ansi(text) == "Resets 6:59pm"

    def test_erase_line_sequences(self):
        """Erase line sequences."""
        text = "Text\033[KMore text"
        assert strip_ansi(text) == "TextMore text"

    def test_save_restore_cursor(self):
        """Save and restore cursor sequences."""
        text = "\033[sText\033[u"
        assert strip_ansi(text) == "Text"

    def test_empty_string(self):
        """Empty string should return empty."""
        assert strip_ansi("") == ""

    def test_only_escape_sequences(self):
        """String with only escape sequences should return empty."""
        text = "\033[0m\033[1m\033[2m"
        assert strip_ansi(text) == ""

    def test_unicode_preservation(self):
        """Unicode characters should be preserved."""
        text = "\033[1m████░░░░\033[0m"
        result = strip_ansi(text)
        assert "████" in result
        assert "░░░░" in result

    def test_newlines_preserved(self):
        """Newlines should be preserved."""
        text = "\033[1mLine1\033[0m\nLine2"
        assert strip_ansi(text) == "Line1\nLine2"

    def test_tabs_preserved(self):
        """Tabs should be preserved."""
        text = "\033[1mCol1\033[0m\tCol2"
        assert strip_ansi(text) == "Col1\tCol2"

    def test_realistic_usage_output(self):
        """Test with realistic /usage output fragment."""
        text = """
\033[1mCurrent session\033[0m
\033[2m━━━━━━━━━━━━━━━━━━\033[0m
\033[92m██████████\033[0m\033[2m░░░░░░░░░░\033[0m  42% used

Resets 6:59pm
"""
        result = strip_ansi(text)
        assert "Current session" in result
        assert "42% used" in result
        assert "Resets 6:59pm" in result
        # No escape sequences should remain
        assert "\033" not in result
        assert "\x1b" not in result
