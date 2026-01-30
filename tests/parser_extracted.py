"""
Extracted parser from cc_usage.sh for testing.

This module contains the Python parsing logic extracted from the embedded
Python code in cc_usage.sh (lines 160-437). It provides testable functions
for ANSI stripping, date parsing, and usage extraction.
"""

import re
import datetime
from datetime import timedelta
from typing import Optional, Tuple, Dict, Any, NamedTuple
from dataclasses import dataclass


@dataclass
class ParseResult:
    """Result of parsing usage output."""
    session_percent: Optional[int] = None
    session_reset_str: Optional[str] = None
    session_reset_dt: Optional[datetime.datetime] = None
    week_percent: Optional[int] = None
    week_reset_str: Optional[str] = None
    week_reset_dt: Optional[datetime.datetime] = None
    error: Optional[str] = None
    warnings: list = None

    def __post_init__(self):
        if self.warnings is None:
            self.warnings = []


def strip_ansi(text: str) -> str:
    """
    Remove ANSI escape sequences from text.

    Handles various escape sequence formats:
    - CSI sequences: ESC [ ... final_byte
    - OSC sequences: ESC ] ... BEL/ST
    - Simple escapes: ESC followed by single char
    """
    return re.sub(r'\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])', '', text)


def clean_date_string(text: str) -> str:
    """
    Normalize a date/time string for parsing.

    Operations:
    - Remove timezone info in parentheses: "(Europe/Stockholm)"
    - Remove commas
    - Collapse multiple spaces
    - Strip non-printable characters
    - Normalize am/pm to uppercase
    """
    text = re.sub(r'\s*\(.*?\)', '', text)  # Remove (Europe/Stockholm)
    text = text.replace(',', '')             # Remove commas
    text = re.sub(r'\s+', ' ', text)         # Collapse spaces
    text = ''.join(c for c in text if c.isprintable())
    # Normalize am/pm to uppercase for consistent %p parsing
    text = re.sub(r'(?i)(am|pm)$', lambda m: m.group(1).upper(), text)
    return text.strip()


def parse_reset_time(
    time_str: str,
    window_hours: int = 5,
    section_text: Optional[str] = None,
    now: Optional[datetime.datetime] = None
) -> Optional[datetime.datetime]:
    """
    Parse a reset time string into a datetime object.

    Args:
        time_str: The time string to parse (e.g., "6:59pm", "Jan 29 at 6:59pm")
        window_hours: The window duration (5 for session, 168 for week)
        section_text: Full section text to search for time patterns (more reliable)
        now: Current datetime (for testing, defaults to datetime.now())

    Returns:
        Parsed datetime or None if parsing fails
    """
    if now is None:
        now = datetime.datetime.now()

    time_pattern = r'(\d{1,2})(:\d{2})?(am|pm)'
    time_match = None

    # PRIMARY: Search section_text for valid time patterns (most reliable).
    # Terminal rendering corrupts the captured time_str with cursor movement,
    # partial updates, and double-spaces. Search the full section instead.
    if section_text:
        # Match time with optional date prefix: "12:59am" or "Jan 29 at 6:59pm"
        full_time_pattern = r'(?:[A-Za-z]{3}\s+\d{1,2}\s+at\s+)?\d{1,2}:\d{2}(?:am|pm)'
        all_matches = re.findall(full_time_pattern, section_text, re.IGNORECASE)
        if all_matches:
            time_str = all_matches[-1]  # Use the LAST match
            time_match = re.search(time_pattern, time_str, re.IGNORECASE)

    # FALLBACK: Try the captured time_str directly
    if not time_match and time_str:
        time_match = re.search(time_pattern, time_str, re.IGNORECASE)

    # If we found a time match, reconstruct time_str properly
    if time_match:
        hour = time_match.group(1)
        minutes = time_match.group(2) or ''
        ampm = time_match.group(3)

        # Check for date prefix (e.g., "Jan 29 at") in original string
        date_match = re.search(r'([A-Za-z]{3})\s+(\d{1,2})\s+at', time_str, re.IGNORECASE)
        if date_match:
            time_str = f"{date_match.group(1)} {date_match.group(2)} at {hour}{minutes}{ampm}"
        else:
            time_str = f"{hour}{minutes}{ampm}"

    clean = clean_date_string(time_str)
    dt = None

    # Formats containing a specific Date
    formats_date_with_year = [
        '%b %d %Y at %I:%M%p',  # Jan 2 2026 at 9:59pm
        '%b %d %Y at %I%p'      # Jan 2 2026 at 9pm
    ]
    formats_date_no_year = [
        '%b %d at %I:%M%p',     # Dec 8 at 4:30pm
        '%b %d at %I%p'         # Dec 8 at 4pm
    ]

    # Formats containing only Time (The Problem Makers)
    formats_time = [
        '%I:%M%p',              # 4:30pm
        '%I%p'                  # 4pm
    ]

    # 1. Try formats with explicit Dates first
    if 'at' in clean:
        # First try formats that already have year embedded
        for fmt in formats_date_with_year:
            try:
                dt = datetime.datetime.strptime(clean, fmt)
                break
            except ValueError:
                continue

        # Then try formats without year (append current year)
        if dt is None:
            for fmt in formats_date_no_year:
                try:
                    dt = datetime.datetime.strptime(f"{clean} {now.year}", f"{fmt} %Y")
                    # Year Wrap Logic: If date is way in past, it's next year
                    if dt < now - timedelta(days=300):
                        dt = dt.replace(year=now.year + 1)
                    elif dt > now + timedelta(days=300):
                        dt = dt.replace(year=now.year - 1)
                    break
                except ValueError:
                    continue

    # 2. Try Time-Only formats
    else:
        for fmt in formats_time:
            try:
                t = datetime.datetime.strptime(clean, fmt).time()
                dt = datetime.datetime.combine(now.date(), t)

                # --- FIX: THE TOMORROW LOGIC ---
                # If we parsed "1:59am" but it is currently "11:58pm",
                # the parsed date (Today 1:59am) is in the past.
                # "Resets" always implies the future.
                if dt < now - timedelta(minutes=15):
                    if window_hours == 168:
                        # WEEKLY RESET: Find next occurrence within 7 days
                        # The reset happens on a specific weekday at this time.
                        # We need to find the NEXT occurrence, not blindly add 7 days.
                        # Try each day until we find a future time ≤168h away.
                        for days in range(1, 8):
                            candidate = dt + timedelta(days=days)
                            remaining_hours = (candidate - now).total_seconds() / 3600
                            if candidate > now and remaining_hours <= 168:
                                dt = candidate
                                break
                    else:
                        # SESSION RESET: Add 1 day for daily/session windows
                        dt += timedelta(days=1)

                break
            except ValueError:
                continue

    return dt


def validate_reset_time(
    reset_dt: Optional[datetime.datetime],
    window_hours: int,
    reset_str: str,
    now: Optional[datetime.datetime] = None
) -> Tuple[Optional[datetime.datetime], Optional[str]]:
    """
    Validate parsed reset time makes sense.

    Args:
        reset_dt: Parsed datetime (may be None)
        window_hours: Expected window (5 or 168)
        reset_str: Original string (for error messages)
        now: Current datetime (for testing)

    Returns:
        Tuple of (validated_dt, warning_message)
        - dt may be None if invalid
        - warning is None if valid
    """
    if not reset_dt:
        return None, f"Failed to parse: '{reset_str}'"

    if now is None:
        now = datetime.datetime.now()

    remain = reset_dt - now
    remain_hours = remain.total_seconds() / 3600
    max_hours = window_hours + 1  # Small buffer for timing

    # Sanity check: time remaining can't exceed window
    if remain_hours > max_hours:
        return None, f"Invalid: {remain_hours:.1f}h remaining exceeds {window_hours}h window"

    # Sanity check: time remaining shouldn't be very negative
    if remain_hours < -window_hours:
        return None, f"Invalid: reset time {remain_hours:.1f}h in past"

    return reset_dt, None


def extract_usage_data(content: str) -> ParseResult:
    """
    Extract session and week usage data from raw /usage output.

    Args:
        content: Raw output from claude /usage command

    Returns:
        ParseResult with extracted data or error
    """
    result = ParseResult()

    # Clean the content
    content = content.replace('\r\n', '\n').replace('\r', '\n')
    clean_text = strip_ansi(content)

    # Split content at "Current week" to isolate session section
    session_section = clean_text.split('Current week')[0] if 'Current week' in clean_text else clean_text

    # Session: "Current session" ... "X% used" ... "Rese(t)s <time>"
    # Handle potential character corruption (Reses vs Resets)
    session_match = re.search(
        r'Current\s+session.*?(\d+)%\s*used.*?Rese[ts]*\s*(.*?)(?:\s{2,}|\n|$)',
        session_section, re.DOTALL | re.IGNORECASE
    )

    # Week: "Current week (all models)" - must explicitly match "(all models)"
    # to avoid matching "Sonnet only" section
    week_match = re.search(
        r'Current\s+week\s+\(all\s+models\).*?(\d+)%\s*used.*?Resets\s*(.*?)(?:\s{2,}|\n|$)',
        clean_text, re.DOTALL | re.IGNORECASE
    )

    if not session_match:
        result.error = "Session data not found"
        return result

    if not week_match:
        result.error = "Week data not found"
        return result

    # Extract percentages
    result.session_percent = int(session_match.group(1))
    result.session_reset_str = session_match.group(2).strip()
    result.week_percent = int(week_match.group(1))
    result.week_reset_str = week_match.group(2).strip()

    # Parse reset times
    result.session_reset_dt = parse_reset_time(
        result.session_reset_str,
        window_hours=5,
        section_text=session_section
    )
    result.week_reset_dt = parse_reset_time(
        result.week_reset_str,
        window_hours=168,
        section_text=clean_text
    )

    # Validate reset times
    validated_session, session_warning = validate_reset_time(
        result.session_reset_dt, 5, result.session_reset_str
    )
    validated_week, week_warning = validate_reset_time(
        result.week_reset_dt, 168, result.week_reset_str
    )

    if session_warning:
        result.warnings.append(f"Session: {session_warning}")
        result.session_reset_dt = validated_session

    if week_warning:
        result.warnings.append(f"Week: {week_warning}")
        result.week_reset_dt = validated_week

    return result


# Format lists exported for fix_generator to modify
DATE_FORMATS_WITH_YEAR = [
    '%b %d %Y at %I:%M%p',  # Jan 2 2026 at 9:59pm
    '%b %d %Y at %I%p'      # Jan 2 2026 at 9pm
]

DATE_FORMATS_NO_YEAR = [
    '%b %d at %I:%M%p',     # Dec 8 at 4:30pm
    '%b %d at %I%p'         # Dec 8 at 4pm
]

TIME_FORMATS = [
    '%I:%M%p',              # 4:30pm
    '%I%p'                  # 4pm
]
