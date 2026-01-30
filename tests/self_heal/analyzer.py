"""
Root cause analyzer for the self-healing test system.

Analyzes classified failures to determine the specific root cause
and gather context for fix generation.
"""

import re
from dataclasses import dataclass, field
from typing import Optional, Dict, Any, List
from pathlib import Path

from .classifier import ClassifiedFailure, FailureType


@dataclass
class RootCause:
    """Detailed root cause analysis of a failure."""

    failure: ClassifiedFailure
    description: str
    affected_function: str
    affected_lines: List[int]
    suggested_fix_type: str  # e.g., "add_date_format", "broaden_regex"
    context: Dict[str, Any] = field(default_factory=dict)
    requires_ai: bool = False


class RootCauseAnalyzer:
    """
    Analyzes failures to determine specific root causes.

    Uses pattern matching, code analysis, and heuristics to understand
    exactly what went wrong and what needs to be fixed.
    """

    # Known functions in the parser and their line ranges (approximate)
    FUNCTION_MAP = {
        "strip_ansi": (181, 182),
        "clean_date_string": (184, 191),
        "parse_reset_time": (193, 290),
        "validate_reset_time": (292, 313),
        "extract_usage_data": (320, 361),
        "session_regex": (333, 336),
        "week_regex": (340, 343),
    }

    # Regex for extracting date strings from error messages
    DATE_STRING_PATTERN = re.compile(
        r"(?:time data |parse[: ]+)['\"]([^'\"]+)['\"]"
    )

    def __init__(self, parser_source: Optional[str] = None):
        """
        Initialize the analyzer.

        Args:
            parser_source: Source code of the parser (for context extraction)
        """
        self.parser_source = parser_source
        self._source_lines: Optional[List[str]] = None
        if parser_source:
            self._source_lines = parser_source.split('\n')

    def analyze(self, failure: ClassifiedFailure) -> RootCause:
        """
        Analyze a classified failure to determine root cause.

        Args:
            failure: The classified failure to analyze

        Returns:
            RootCause with detailed analysis
        """
        # Dispatch based on failure type
        analyzers = {
            FailureType.DATE_FORMAT_NEW: self._analyze_date_format,
            FailureType.REGEX_MISMATCH: self._analyze_regex_mismatch,
            FailureType.VALIDATION_ERROR: self._analyze_validation_error,
            FailureType.ANSI_CORRUPTION: self._analyze_ansi_corruption,
            FailureType.EDGE_CASE: self._analyze_edge_case,
            FailureType.UNKNOWN: self._analyze_unknown,
        }

        analyzer = analyzers.get(failure.failure_type, self._analyze_unknown)
        return analyzer(failure)

    def _analyze_date_format(self, failure: ClassifiedFailure) -> RootCause:
        """Analyze a DATE_FORMAT_NEW failure."""
        # Extract the problematic date string
        date_string = None
        match = self.DATE_STRING_PATTERN.search(failure.exception_message)
        if match:
            date_string = match.group(1)

        # Infer what format might be needed
        inferred_format = None
        if date_string:
            inferred_format = self._infer_date_format(date_string)

        context = {
            "date_string": date_string,
            "inferred_format": inferred_format,
        }

        # Determine which format list to modify
        if date_string and 'at' in date_string.lower():
            if any(c.isdigit() and len(date_string.split()) > 4 for c in date_string):
                suggested_fix = "add_date_format_with_year"
            else:
                suggested_fix = "add_date_format_no_year"
        else:
            suggested_fix = "add_time_format"

        return RootCause(
            failure=failure,
            description=f"New date format not recognized: '{date_string}'",
            affected_function="parse_reset_time",
            affected_lines=list(range(230, 244)),  # Format lists area
            suggested_fix_type=suggested_fix,
            context=context,
            requires_ai=inferred_format is None,
        )

    def _analyze_regex_mismatch(self, failure: ClassifiedFailure) -> RootCause:
        """Analyze a REGEX_MISMATCH failure."""
        # Determine which regex failed
        is_session = "session" in failure.traceback.lower()
        is_week = "week" in failure.traceback.lower()

        if is_session and not is_week:
            affected = "session_regex"
            lines = list(range(333, 337))
        elif is_week:
            affected = "week_regex"
            lines = list(range(340, 344))
        else:
            affected = "extract_usage_data"
            lines = list(range(320, 361))

        context = {
            "is_session": is_session,
            "is_week": is_week,
        }

        return RootCause(
            failure=failure,
            description=f"Regex pattern failed to match: {affected}",
            affected_function=affected,
            affected_lines=lines,
            suggested_fix_type="broaden_regex",
            context=context,
            requires_ai=True,  # Regex changes often need AI
        )

    def _analyze_validation_error(self, failure: ClassifiedFailure) -> RootCause:
        """Analyze a VALIDATION_ERROR failure."""
        # Check if it's a window exceeded or past time issue
        is_exceeds = "exceeds" in failure.exception_message.lower()
        is_past = "past" in failure.exception_message.lower()

        context = {
            "is_window_exceeded": is_exceeds,
            "is_in_past": is_past,
        }

        return RootCause(
            failure=failure,
            description="Validation rejected parsed value",
            affected_function="validate_reset_time",
            affected_lines=list(range(292, 314)),
            suggested_fix_type="adjust_validation",
            context=context,
            requires_ai=False,
        )

    def _analyze_ansi_corruption(self, failure: ClassifiedFailure) -> RootCause:
        """Analyze an ANSI_CORRUPTION failure."""
        # Try to extract the problematic sequence
        ansi_match = re.search(r'\\x1[bB]\[([^m]*m?)', failure.exception_message)
        sequence = ansi_match.group(0) if ansi_match else None

        context = {
            "detected_sequence": sequence,
        }

        return RootCause(
            failure=failure,
            description="ANSI escape sequence not properly stripped",
            affected_function="strip_ansi",
            affected_lines=[181, 182],
            suggested_fix_type="enhance_ansi_regex",
            context=context,
            requires_ai=True,  # ANSI regex can be tricky
        )

    def _analyze_edge_case(self, failure: ClassifiedFailure) -> RootCause:
        """Analyze an EDGE_CASE failure."""
        # Try to determine the specific edge case
        is_midnight = "midnight" in failure.exception_message.lower() or \
                      "12:00am" in failure.exception_message.lower()
        is_year_wrap = "year" in failure.exception_message.lower() or \
                       ("dec" in failure.exception_message.lower() and
                        "jan" in failure.exception_message.lower())

        context = {
            "is_midnight_crossing": is_midnight,
            "is_year_wrap": is_year_wrap,
        }

        if is_midnight:
            suggested = "fix_midnight_logic"
            lines = list(range(276, 285))  # Tomorrow logic
        elif is_year_wrap:
            suggested = "fix_year_wrap"
            lines = list(range(260, 268))  # Year wrap logic
        else:
            suggested = "fix_edge_case"
            lines = list(range(193, 290))  # Entire parse_reset_time

        return RootCause(
            failure=failure,
            description="Edge case in date/time handling",
            affected_function="parse_reset_time",
            affected_lines=lines,
            suggested_fix_type=suggested,
            context=context,
            requires_ai=True,
        )

    def _analyze_unknown(self, failure: ClassifiedFailure) -> RootCause:
        """Analyze an UNKNOWN failure - always requires AI."""
        return RootCause(
            failure=failure,
            description="Unknown failure type - requires AI analysis",
            affected_function="unknown",
            affected_lines=[],
            suggested_fix_type="ai_diagnose",
            context={},
            requires_ai=True,
        )

    def _infer_date_format(self, date_string: str) -> Optional[str]:
        """
        Attempt to infer the strptime format from a date string.

        Args:
            date_string: The date/time string to analyze

        Returns:
            Inferred format string or None
        """
        # Common patterns and their formats
        patterns = [
            # Full date + time
            (r'^([A-Z][a-z]{2})\s+(\d{1,2})\s+(\d{4})\s+at\s+(\d{1,2}):(\d{2})(am|pm)$',
             '%b %d %Y at %I:%M%p'),
            (r'^([A-Z][a-z]{2})\s+(\d{1,2})\s+(\d{4})\s+at\s+(\d{1,2})(am|pm)$',
             '%b %d %Y at %I%p'),

            # Date without year + time
            (r'^([A-Z][a-z]{2})\s+(\d{1,2})\s+at\s+(\d{1,2}):(\d{2})(am|pm)$',
             '%b %d at %I:%M%p'),
            (r'^([A-Z][a-z]{2})\s+(\d{1,2})\s+at\s+(\d{1,2})(am|pm)$',
             '%b %d at %I%p'),

            # Day-first formats
            (r'^(\d{1,2})\s+([A-Z][a-z]{2})\s+at\s+(\d{1,2}):(\d{2})(am|pm)$',
             '%d %b at %I:%M%p'),

            # Time only
            (r'^(\d{1,2}):(\d{2})(am|pm)$', '%I:%M%p'),
            (r'^(\d{1,2})(am|pm)$', '%I%p'),
        ]

        for pattern, fmt in patterns:
            if re.match(pattern, date_string, re.IGNORECASE):
                return fmt

        return None

    def get_source_context(
        self,
        start_line: int,
        end_line: int,
        context_lines: int = 5
    ) -> str:
        """
        Get source code context around specified lines.

        Args:
            start_line: Starting line number (1-indexed)
            end_line: Ending line number (1-indexed)
            context_lines: Number of extra lines to include

        Returns:
            Source code snippet with line numbers
        """
        if not self._source_lines:
            return ""

        # Adjust to 0-indexed
        start = max(0, start_line - 1 - context_lines)
        end = min(len(self._source_lines), end_line + context_lines)

        lines = []
        for i in range(start, end):
            prefix = ">>> " if start_line - 1 <= i < end_line else "    "
            lines.append(f"{prefix}{i + 1:4d}: {self._source_lines[i]}")

        return '\n'.join(lines)
