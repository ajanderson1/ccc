#!/usr/bin/env python3
"""
Claude Code Usage Analyzer - Robust Parser with Self-Healing Support

A visual, pace-aware CLI tool for monitoring Claude Code usage limits.
Extracts session and weekly usage data, calculates pace against time elapsed,
and displays progress bars with color-coded status.

Usage:
    ./cc_usage.py              # Single run
    ./cc_usage.py --loop       # Continuous monitoring
    ./cc_usage.py --raw        # Show raw captured output
    ./cc_usage.py --heal       # Trigger self-healing on failure
"""

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import List, Optional, Tuple

try:
    from dateutil import parser as dateutil_parser
    from dateutil.parser import ParserError
    DATEUTIL_AVAILABLE = True
except ImportError:
    DATEUTIL_AVAILABLE = False
    ParserError = ValueError

# --- Constants ---
LOCK_FILE = Path("~/.cc_usage.lock").expanduser()
WIDTH = 40
BLOCK_FULL = "\u2588"
BLOCK_EMPTY = "\u2591"

# ANSI colors
GREEN = "\033[92m"
RED = "\033[91m"
BOLD = "\033[1m"
RESET = "\033[0m"
DIM = "\033[2m"

# Fallback date formats (used when dateutil fails)
TIME_FORMATS = [
    "%I:%M%p",  # 4:30pm
    "%I%p",     # 4pm
]

DATE_FORMATS_NO_YEAR = [
    "%b %d at %I:%M%p",  # Jan 29 at 6:59pm
    "%b %d at %I%p",     # Jan 29 at 6pm
]

DATE_FORMATS_WITH_YEAR = [
    "%b %d %Y at %I:%M%p",  # Jan 29 2026 at 6:59pm
    "%b %d %Y at %I%p",     # Jan 29 2026 at 6pm
]


# --- Exceptions ---
class CaptureError(Exception):
    """Failed to capture output from Claude CLI."""
    pass


class ParseError(Exception):
    """Failed to parse usage data from captured output."""
    pass


class ValidationError(Exception):
    """Parsed data failed validation checks."""
    pass


# --- Data Classes ---
@dataclass
class ParseResult:
    """Result of parsing usage output."""
    session_percent: Optional[int] = None
    session_reset_str: Optional[str] = None
    session_reset_dt: Optional[datetime] = None
    week_percent: Optional[int] = None
    week_reset_str: Optional[str] = None
    week_reset_dt: Optional[datetime] = None
    error: Optional[str] = None
    warnings: List[str] = field(default_factory=list)


# --- Lock Management ---
def acquire_lock() -> bool:
    """Acquire lock file to prevent concurrent runs."""
    try:
        LOCK_FILE.touch(exist_ok=False)
        return True
    except FileExistsError:
        # Check if stale (older than 5 minutes)
        try:
            if LOCK_FILE.stat().st_mtime < time.time() - 300:
                LOCK_FILE.unlink()
                LOCK_FILE.touch()
                return True
        except (OSError, FileNotFoundError):
            pass
        return False


def release_lock():
    """Release lock file."""
    try:
        LOCK_FILE.unlink()
    except (OSError, FileNotFoundError):
        pass


# --- Capture Layer ---
def find_claude_command() -> str:
    """Find the Claude CLI command."""
    for cmd in ["claude", "cc"]:
        try:
            subprocess.run([cmd, "--version"], capture_output=True, check=True)
            return cmd
        except (subprocess.CalledProcessError, FileNotFoundError):
            continue
    raise CaptureError("Claude CLI not found. Install 'claude' or 'cc'.")


def run_expect_capture(cmd: str, timeout: int = 20) -> str:
    """Run expect to capture claude /usage output."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".exp", delete=False) as driver:
        with tempfile.NamedTemporaryFile(mode="w", suffix=".log", delete=False) as log:
            driver.write(f"""set timeout {timeout}
log_file -noappend "{log.name}"

set env(LINES) 50
set env(COLUMNS) 160

spawn {cmd} /usage

set pid [exp_pid]

expect {{
    "Yes, proceed" {{
        sleep 0.3
        send "\\r"
        exp_continue
    }}
    "Sonnet only" {{
        sleep 0.5
    }}
    "% used" {{
        sleep 2.0
    }}
    timeout {{
    }}
}}

sleep 0.2
log_file

catch {{close}}
catch {{exec kill -9 $pid}}
catch {{wait -nowait}}
""")
            driver.flush()

            driver_path = driver.name
            log_path = log.name

    try:
        # Kill any pre-existing /usage processes
        subprocess.run(["pkill", "-f", f"{cmd} /usage"], capture_output=True)

        # Run expect
        subprocess.run(
            ["expect", driver_path],
            capture_output=True,
            timeout=timeout + 5,
        )

        # Extra cleanup
        time.sleep(0.2)
        subprocess.run(["pkill", "-f", f"{cmd} /usage"], capture_output=True)

        # Read captured output
        with open(log_path, "r", encoding="utf-8", errors="ignore") as f:
            return f.read()

    except subprocess.TimeoutExpired:
        raise CaptureError(f"Capture timed out after {timeout}s")
    finally:
        # Cleanup temp files
        for path in [driver_path, log_path]:
            try:
                os.unlink(path)
            except OSError:
                pass


def validate_capture(output: str) -> bool:
    """Validate captured output has required sections."""
    clean = strip_ansi(output)
    return all([
        "Current session" in clean,
        "Current week" in clean,
        "% used" in clean,
    ])


def capture_usage_output(max_retries: int = 3) -> str:
    """Capture claude /usage output with retries."""
    cmd = find_claude_command()

    for attempt in range(max_retries):
        try:
            output = run_expect_capture(cmd)

            if validate_capture(output):
                return output

        except CaptureError:
            pass

        # Exponential backoff
        if attempt < max_retries - 1:
            time.sleep(0.5 * (attempt + 1))

    raise CaptureError(f"Failed to capture output after {max_retries} attempts")


# --- Parse Layer ---
def strip_ansi(text: str) -> str:
    """Remove ANSI escape sequences from text, preserving spacing."""
    # Replace cursor movement codes with spaces (e.g., [1C = move right 1)
    text = re.sub(r"\x1B\[(\d+)C", lambda m: " " * int(m.group(1)), text)
    # Remove remaining ANSI escape sequences
    text = re.sub(r"\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])", "", text)
    # Also remove OSC sequences (like ]0;title)
    text = re.sub(r"\x1B\][^\x07]*(?:\x07|\x1B\\)", "", text)
    text = re.sub(r"\][\d;]*[^\n]*", "", text)
    return text


def clean_time_string(text: str) -> str:
    """Normalize a time string for parsing."""
    text = re.sub(r"\s*\(.*?\)", "", text)  # Remove timezone in parens
    text = text.replace(",", "")  # Remove commas
    text = re.sub(r"\s+", " ", text)  # Collapse spaces
    text = "".join(c for c in text if c.isprintable())
    # Normalize am/pm to uppercase
    text = re.sub(r"(?i)(am|pm)$", lambda m: m.group(1).upper(), text)
    return text.strip()


def extract_via_regex(text: str, section: str) -> Optional[int]:
    """Extract percentage using regex pattern."""
    # Find the section
    pattern = rf"{re.escape(section)}.*?(\d+)%\s*used"
    match = re.search(pattern, text, re.DOTALL | re.IGNORECASE)
    if match:
        return int(match.group(1))
    return None


def extract_via_structure(text: str, section: str) -> Optional[int]:
    """Extract percentage by finding it near section header."""
    lines = text.split("\n")
    in_section = False

    for line in lines:
        if section.lower() in line.lower():
            in_section = True
            continue

        if in_section:
            # Look for percentage on this or next few lines
            match = re.search(r"(\d+)%", line)
            if match:
                val = int(match.group(1))
                if 0 <= val <= 100:
                    return val

            # Stop if we hit another section
            if "current" in line.lower() and section.lower() not in line.lower():
                break

    return None


def extract_via_numbers(text: str, section: str) -> Optional[int]:
    """Extract percentage by finding all numbers and filtering."""
    # Split into sections
    parts = re.split(r"(?=Current\s+)", text, flags=re.IGNORECASE)

    for part in parts:
        if section.lower() in part.lower():
            # Find all numbers followed by %
            matches = re.findall(r"(\d+)%", part)
            for match in matches:
                val = int(match)
                if 0 <= val <= 100:
                    return val

    return None


def extract_percentage(text: str, section: str) -> Optional[int]:
    """Extract percentage using cascade of strategies."""
    strategies = [
        extract_via_regex,
        extract_via_structure,
        extract_via_numbers,
    ]

    for strategy in strategies:
        result = strategy(text, section)
        if result is not None and 0 <= result <= 100:
            return result
    return None


def extract_reset_string(text: str, section: str) -> Optional[str]:
    """Extract reset time string from section."""
    # Find section and look for "Resets" or "Reses" (handles corruption)
    pattern = rf"{re.escape(section)}.*?Rese[ts]*\s+(.+?)(?:\s{{2,}}|\n|$)"
    match = re.search(pattern, text, re.DOTALL | re.IGNORECASE)
    if match:
        return match.group(1).strip()
    return None


def adjust_to_future(
    dt: datetime, now: datetime, window_hours: int
) -> Optional[datetime]:
    """Ensure reset time is in future and within window.

    Returns None if the time is stale (in the past and cannot be
    adjusted to a valid future time within the window).
    """
    # If time-only was parsed (year is 1900), combine with today
    if dt.year == 1900:
        dt = datetime.combine(now.date(), dt.time())

    # If in past, adjust forward
    if dt < now - timedelta(minutes=15):
        if window_hours <= 24:
            # Session: try adding 1 day
            candidate = dt + timedelta(days=1)
            hours_away = (candidate - now).total_seconds() / 3600
            if hours_away <= window_hours + 1:
                return candidate
            # Stale data — can't infer a valid future reset
            return None
        else:
            # Weekly: find next occurrence within 7 days
            for days in range(1, 8):
                candidate = dt + timedelta(days=days)
                if candidate > now:
                    hours_away = (candidate - now).total_seconds() / 3600
                    if hours_away <= window_hours:
                        return candidate
            # No valid candidate found
            return None

    return dt


def parse_reset_time(
    time_str: str,
    window_hours: int,
    now: Optional[datetime] = None
) -> Optional[datetime]:
    """Parse reset time with dateutil + fallback."""
    if not time_str:
        return None

    now = now or datetime.now()
    clean = clean_time_string(time_str)

    # Strategy 1: dateutil with fuzzy parsing
    if DATEUTIL_AVAILABLE:
        try:
            default_date = now.replace(hour=0, minute=0, second=0, microsecond=0)
            dt = dateutil_parser.parse(clean, fuzzy=True, default=default_date)
            return adjust_to_future(dt, now, window_hours)
        except (ValueError, ParserError):
            pass

    # Strategy 2: Explicit format lists
    # Try formats with "at" first (date + time)
    if "at" in clean.lower():
        for fmt in DATE_FORMATS_WITH_YEAR:
            try:
                dt = datetime.strptime(clean, fmt)
                return adjust_to_future(dt, now, window_hours)
            except ValueError:
                continue

        for fmt in DATE_FORMATS_NO_YEAR:
            try:
                dt = datetime.strptime(f"{clean} {now.year}", f"{fmt} %Y")
                return adjust_to_future(dt, now, window_hours)
            except ValueError:
                continue

    # Try time-only formats
    for fmt in TIME_FORMATS:
        try:
            t = datetime.strptime(clean, fmt).time()
            dt = datetime.combine(now.date(), t)
            return adjust_to_future(dt, now, window_hours)
        except ValueError:
            continue

    return None


def parse_usage(content: str, now: Optional[datetime] = None) -> ParseResult:
    """Parse usage data from captured output."""
    now = now or datetime.now()
    result = ParseResult()

    # Clean content
    content = content.replace("\r\n", "\n").replace("\r", "\n")
    clean_text = strip_ansi(content)

    # Split into session section (before "Current week")
    session_section = clean_text.split("Current week")[0] if "Current week" in clean_text else clean_text

    # Extract session data
    result.session_percent = extract_percentage(session_section, "Current session")
    result.session_reset_str = extract_reset_string(session_section, "Current session")
    result.session_reset_dt = parse_reset_time(
        result.session_reset_str, window_hours=5, now=now
    )

    # Extract week data (must match "all models" to avoid "Sonnet only")
    result.week_percent = extract_percentage(clean_text, "Current week (all models)")
    result.week_reset_str = extract_reset_string(clean_text, "Current week (all models)")
    result.week_reset_dt = parse_reset_time(
        result.week_reset_str, window_hours=168, now=now
    )

    # Check for missing data
    if result.session_percent is None:
        result.error = "Failed to extract session percentage"
    elif result.week_percent is None:
        result.error = "Failed to extract week percentage"

    return result


# --- Validation Layer ---
def validate_result(
    result: ParseResult,
    now: Optional[datetime] = None
) -> Tuple[bool, Optional[str]]:
    """Validate parsed result is sensible."""
    if result.error:
        return False, result.error

    now = now or datetime.now()

    # Check percentages are present
    if result.session_percent is None:
        return False, "Session percentage missing"
    if result.week_percent is None:
        return False, "Week percentage missing"

    # Check percentages in range
    if not (0 <= result.session_percent <= 100):
        return False, f"Session percent {result.session_percent} out of range"
    if not (0 <= result.week_percent <= 100):
        return False, f"Week percent {result.week_percent} out of range"

    # Check reset times are in future (with tolerance)
    if result.session_reset_dt:
        if result.session_reset_dt < now - timedelta(minutes=5):
            return False, f"Session reset {result.session_reset_dt} is in past"

        hours = (result.session_reset_dt - now).total_seconds() / 3600
        if hours > 6:  # 5h window + 1h buffer
            return False, f"Session reset {hours:.1f}h away exceeds window"

    if result.week_reset_dt:
        if result.week_reset_dt < now - timedelta(minutes=5):
            return False, f"Week reset {result.week_reset_dt} is in past"

        hours = (result.week_reset_dt - now).total_seconds() / 3600
        if hours > 169:  # 168h window + 1h buffer
            return False, f"Week reset {hours:.1f}h away exceeds window"

    return True, None


# --- Display Layer ---
def create_bar(percent: int) -> str:
    """Create progress bar string."""
    p = max(0, min(100, percent))
    fill = int((p / 100) * WIDTH)
    return (BLOCK_FULL * fill) + (BLOCK_EMPTY * (WIDTH - fill))


def display_section(
    title: str,
    used_percent: int,
    reset_dt: Optional[datetime],
    window_hours: int
):
    """Display a single usage section with pace analysis."""
    now = datetime.now()

    print(f"  {BOLD}{title}{RESET}")

    if reset_dt is None:
        print(f"  Usage:  {create_bar(used_percent)}  {used_percent}% used")
        print(f"  {RED}Warning: Could not parse reset time{RESET}")
        return

    # Calculate time elapsed
    if window_hours == 168:
        start_dt = reset_dt - timedelta(days=7)
    else:
        start_dt = reset_dt - timedelta(hours=window_hours)

    elapsed_pct = ((now - start_dt).total_seconds() / (window_hours * 3600)) * 100
    elapsed_pct = max(0, min(100, elapsed_pct))
    pace = used_percent - elapsed_pct

    # Display bars
    print(f"  Time:   {create_bar(int(elapsed_pct))}  {round(elapsed_pct)}% time")

    color = RED if used_percent > elapsed_pct else GREEN
    print(f"  Usage:  {color}{create_bar(used_percent)}{RESET}  {used_percent}% used")

    # Status message
    pace_int = round(pace)
    if pace_int == 0:
        msg = "On pace"
    else:
        p_str = f"{abs(pace_int)}pp"
        msg = f"Above pace ({p_str})" if pace_int > 0 else f"Below pace ({p_str})"

    # Time remaining
    remain = reset_dt - now
    days = remain.days
    hours = remain.seconds // 3600
    mins = (remain.seconds // 60) % 60

    if days > 0:
        remain_str = f"{days}d {hours}h"
    else:
        remain_str = f"{hours}h {mins}m"

    print(f"  Status: {color}{msg}{RESET} | Resets in {remain_str}")


def display_usage(result: ParseResult, duration: float, debug: bool = False):
    """Display full usage analysis."""
    now = datetime.now()

    print(f"\n{BOLD}Usage Analysis - {now.strftime('%A %B %d at %H:%M')} (took {duration:.2f}s){RESET}\n")

    display_section("Weekly Usage (168h)", result.week_percent, result.week_reset_dt, 168)
    print(f"\n  {DIM}---{RESET}\n")
    display_section("Session Usage (5h)", result.session_percent, result.session_reset_dt, 5)

    if debug:
        print(f"\n  {DIM}Raw: week='{result.week_reset_str}' session='{result.session_reset_str}'{RESET}")
    print("")


# --- CLI ---
def run_once(raw: bool, quiet: bool, heal_on_fail: bool, debug: bool = False) -> int:
    """Single run with optional healing."""
    start = time.time()

    try:
        output = capture_usage_output()

        if raw:
            print(output)
            return 0

        result = parse_usage(output)
        valid, msg = validate_result(result)

        if not valid:
            raise ValidationError(msg)

        if not quiet:
            display_usage(result, time.time() - start, debug=debug)

        return 0

    except CaptureError as e:
        if not quiet:
            print(f"Capture Error: {e}", file=sys.stderr)
        exit_code = 3

    except (ParseError, ValidationError) as e:
        if not quiet:
            print(f"Parse Error: {e}", file=sys.stderr)
        exit_code = 1

    # Attempt healing if requested
    if heal_on_fail:
        try:
            from self_heal import heal
            heal_result = heal()
            if heal_result.success:
                if not quiet:
                    print(f"Self-healing succeeded: {heal_result.message}")
                return 0
            else:
                if not quiet:
                    print(f"Self-healing failed: {heal_result.message}")
                return 4
        except ImportError:
            if not quiet:
                print("Self-healing module not available", file=sys.stderr)
        except Exception as e:
            if not quiet:
                print(f"Self-healing error: {e}", file=sys.stderr)
            return 4

    return exit_code


def run_loop(interval: int, quiet: bool, debug: bool = False):
    """Run continuously with countdown."""
    import shutil

    try:
        while True:
            # Clear screen
            os.system("clear" if os.name == "posix" else "cls")

            # Run once
            run_once(raw=False, quiet=quiet, heal_on_fail=False, debug=debug)

            # Countdown
            term_width = shutil.get_terminal_size().columns
            for i in range(interval, 0, -1):
                print(f"\rNext refresh in {i:3d} seconds... (Ctrl+C to exit)", end="")
                sys.stdout.flush()
                time.sleep(1)

    except KeyboardInterrupt:
        print("\nExiting...")


def main():
    parser = argparse.ArgumentParser(
        description="Claude Code Usage Analyzer",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Exit codes:
  0 - Success
  1 - Parse/validation failure
  2 - Lock acquisition failed (another instance running)
  3 - Capture failure (Claude not responding)
  4 - Self-healing failed
"""
    )
    parser.add_argument("--loop", action="store_true", help="Run continuously")
    parser.add_argument("--interval", type=int, default=300, help="Loop interval seconds")
    parser.add_argument("--raw", action="store_true", help="Output raw captured content")
    parser.add_argument("--quiet", action="store_true", help="Suppress output, exit code only")
    parser.add_argument("--heal", action="store_true", help="Trigger self-healing on failure")
    parser.add_argument("--debug", action="store_true", help="Show debug info (raw reset strings)")

    args = parser.parse_args()

    # Acquire lock
    if not acquire_lock():
        print("Another instance is running", file=sys.stderr)
        sys.exit(2)

    try:
        if args.loop:
            run_loop(args.interval, args.quiet, args.debug)
        else:
            exit_code = run_once(args.raw, args.quiet, args.heal, args.debug)
            sys.exit(exit_code)
    finally:
        release_lock()


if __name__ == "__main__":
    main()
