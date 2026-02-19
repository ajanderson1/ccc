#!/usr/bin/env python3
"""
Self-Healing Module for Claude Code Usage Parser

Automatically diagnoses and fixes parsing failures by:
1. Capturing live output from Claude CLI
2. Classifying the type of failure
3. Applying pattern-matched fix strategies
4. Verifying fixes against live output (2/3 rule)
5. Committing successful fixes and capturing fixtures

Usage:
    python self_heal.py          # Run healing loop
    python self_heal.py --check  # Check if healing is needed
    python self_heal.py --auto   # Run without prompts (for cron)
"""

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

# Import from parser module
try:
    from cc_usage import (
        CaptureError,
        ParseError,
        ValidationError,
        ParseResult,
        capture_usage_output,
        parse_usage,
        validate_result,
        strip_ansi,
        TIME_FORMATS,
        DATE_FORMATS_NO_YEAR,
        DATE_FORMATS_WITH_YEAR,
    )
except ImportError as e:
    print(f"Error importing cc_usage: {e}", file=sys.stderr)
    sys.exit(1)


# --- Constants ---
PROJECT_ROOT = Path(__file__).parent
PARSER_FILE = PROJECT_ROOT / "cc_usage.py"
FIXTURE_DIR = PROJECT_ROOT / "tests" / "fixtures" / "healed"
HISTORY_FILE = PROJECT_ROOT / ".self_heal" / "history.json"
MAX_ITERATIONS = 3
VERIFY_ATTEMPTS = 3
VERIFY_THRESHOLD = 2  # 2/3 must pass


# --- Enums ---
class FailureType(Enum):
    """Types of failures that can occur."""
    CAPTURE_TIMEOUT = "capture_timeout"
    CAPTURE_INCOMPLETE = "capture_incomplete"
    PARSE_SECTION_MISSING = "section_missing"
    PARSE_PERCENTAGE = "percentage_parse"
    PARSE_DATE = "date_parse"
    VALIDATION = "validation"
    UNKNOWN = "unknown"


# --- Data Classes ---
@dataclass
class Fix:
    """A fix to apply."""
    name: str
    description: str
    apply: Callable[[], None]
    rollback: Callable[[], None]


@dataclass
class HealResult:
    """Result of healing attempt."""
    success: bool
    message: str = ""
    fix: Optional[Fix] = None
    iterations: int = 0


@dataclass
class HealingHistory:
    """Track healing history."""
    timestamp: str
    success: bool
    failure_type: str
    fix_applied: Optional[str]
    message: str


# --- Failure Classification ---
def classify_failure(
    error: Exception,
    output: str
) -> Tuple[FailureType, float]:
    """Classify failure type with confidence score."""
    error_str = str(error).lower()
    output_lower = output.lower()

    # Capture errors
    if isinstance(error, CaptureError):
        if "timeout" in error_str:
            return FailureType.CAPTURE_TIMEOUT, 0.95
        return FailureType.CAPTURE_INCOMPLETE, 0.85

    # Missing sections
    if "current session" not in output_lower or "current week" not in output_lower:
        return FailureType.PARSE_SECTION_MISSING, 0.90

    # Date parsing errors
    if "time data" in error_str or "does not match format" in error_str:
        return FailureType.PARSE_DATE, 0.95

    # Validation errors
    if "out of range" in error_str or "exceeds window" in error_str or "in past" in error_str:
        return FailureType.VALIDATION, 0.90

    # Percentage extraction errors
    if "% used" not in output_lower:
        return FailureType.PARSE_PERCENTAGE, 0.85

    if "percentage" in error_str:
        return FailureType.PARSE_PERCENTAGE, 0.80

    return FailureType.UNKNOWN, 0.50


# --- Fix Strategies ---
class FixStrategies:
    """Pattern-matched fix strategies by failure type."""

    STRATEGIES: Dict[FailureType, List[str]] = {
        FailureType.CAPTURE_TIMEOUT: [
            "increment_timeout",
            "add_retry_attempt",
        ],
        FailureType.CAPTURE_INCOMPLETE: [
            "extend_wait_time",
            "add_section_wait",
        ],
        FailureType.PARSE_DATE: [
            "add_date_format",
        ],
        FailureType.PARSE_SECTION_MISSING: [
            "relax_section_regex",
        ],
        FailureType.PARSE_PERCENTAGE: [
            "broaden_percentage_regex",
        ],
        FailureType.VALIDATION: [
            "increase_validation_buffer",
        ],
    }

    @staticmethod
    def get_strategies(failure_type: FailureType) -> List[str]:
        """Get available strategies for a failure type."""
        return FixStrategies.STRATEGIES.get(failure_type, [])

    @staticmethod
    def create_fix(
        strategy_name: str,
        output: str,
        error: Exception
    ) -> Optional[Fix]:
        """Create a fix for the given strategy."""
        creators = {
            "increment_timeout": FixStrategies._fix_increment_timeout,
            "add_retry_attempt": FixStrategies._fix_add_retry,
            "extend_wait_time": FixStrategies._fix_extend_wait,
            "add_section_wait": FixStrategies._fix_add_section_wait,
            "add_date_format": lambda o, e: FixStrategies._fix_add_date_format(o, e),
            "relax_section_regex": FixStrategies._fix_relax_section_regex,
            "broaden_percentage_regex": FixStrategies._fix_broaden_percentage_regex,
            "increase_validation_buffer": FixStrategies._fix_increase_validation_buffer,
        }

        creator = creators.get(strategy_name)
        if creator:
            return creator(output, error)
        return None

    @staticmethod
    def _fix_increment_timeout(output: str, error: Exception) -> Fix:
        """Increment the expect timeout."""
        parser_source = PARSER_FILE.read_text()
        original_timeout = 20

        # Find current timeout
        match = re.search(r"timeout: int = (\d+)", parser_source)
        if match:
            original_timeout = int(match.group(1))

        new_timeout = original_timeout + 5

        def apply():
            source = PARSER_FILE.read_text()
            modified = re.sub(
                r"timeout: int = \d+",
                f"timeout: int = {new_timeout}",
                source
            )
            PARSER_FILE.write_text(modified)

        def rollback():
            source = PARSER_FILE.read_text()
            modified = re.sub(
                r"timeout: int = \d+",
                f"timeout: int = {original_timeout}",
                source
            )
            PARSER_FILE.write_text(modified)

        return Fix(
            name="increment_timeout",
            description=f"Increase timeout from {original_timeout}s to {new_timeout}s",
            apply=apply,
            rollback=rollback,
        )

    @staticmethod
    def _fix_add_retry(output: str, error: Exception) -> Fix:
        """Add an extra retry attempt."""
        parser_source = PARSER_FILE.read_text()
        original_retries = 3

        match = re.search(r"max_retries: int = (\d+)", parser_source)
        if match:
            original_retries = int(match.group(1))

        new_retries = original_retries + 1

        def apply():
            source = PARSER_FILE.read_text()
            modified = re.sub(
                r"max_retries: int = \d+",
                f"max_retries: int = {new_retries}",
                source
            )
            PARSER_FILE.write_text(modified)

        def rollback():
            source = PARSER_FILE.read_text()
            modified = re.sub(
                r"max_retries: int = \d+",
                f"max_retries: int = {original_retries}",
                source
            )
            PARSER_FILE.write_text(modified)

        return Fix(
            name="add_retry_attempt",
            description=f"Increase retries from {original_retries} to {new_retries}",
            apply=apply,
            rollback=rollback,
        )

    @staticmethod
    def _fix_extend_wait(output: str, error: Exception) -> Fix:
        """Extend wait time in expect script."""
        original_wait = "0.5"
        new_wait = "1.0"

        def apply():
            source = PARSER_FILE.read_text()
            modified = source.replace('sleep 0.5', 'sleep 1.0')
            PARSER_FILE.write_text(modified)

        def rollback():
            source = PARSER_FILE.read_text()
            modified = source.replace('sleep 1.0', 'sleep 0.5')
            PARSER_FILE.write_text(modified)

        return Fix(
            name="extend_wait_time",
            description="Extend wait time from 0.5s to 1.0s",
            apply=apply,
            rollback=rollback,
        )

    @staticmethod
    def _fix_add_section_wait(output: str, error: Exception) -> Fix:
        """Add longer wait for section rendering."""
        def apply():
            source = PARSER_FILE.read_text()
            modified = source.replace('sleep 2.0', 'sleep 3.0')
            PARSER_FILE.write_text(modified)

        def rollback():
            source = PARSER_FILE.read_text()
            modified = source.replace('sleep 3.0', 'sleep 2.0')
            PARSER_FILE.write_text(modified)

        return Fix(
            name="add_section_wait",
            description="Increase section wait from 2.0s to 3.0s",
            apply=apply,
            rollback=rollback,
        )

    @staticmethod
    def _fix_add_date_format(output: str, error: Exception) -> Optional[Fix]:
        """Add a new date format based on the failing example."""
        clean_output = strip_ansi(output)

        # Find reset time strings
        matches = re.findall(r"Rese[ts]*\s+(.+?)(?:\s{2,}|\n|$)", clean_output, re.IGNORECASE)
        if not matches:
            return None

        time_str = matches[0].strip()

        # Try to infer the format
        inferred = infer_date_format(time_str)
        if not inferred:
            return None

        format_str, list_name = inferred

        def apply():
            source = PARSER_FILE.read_text()

            # Find the format list and add to it
            if list_name == "TIME_FORMATS":
                pattern = r"(TIME_FORMATS = \[)"
                replacement = f'\\1\n    "{format_str}",  # Added by self-heal: {time_str}'
            elif list_name == "DATE_FORMATS_NO_YEAR":
                pattern = r"(DATE_FORMATS_NO_YEAR = \[)"
                replacement = f'\\1\n    "{format_str}",  # Added by self-heal: {time_str}'
            else:
                pattern = r"(DATE_FORMATS_WITH_YEAR = \[)"
                replacement = f'\\1\n    "{format_str}",  # Added by self-heal: {time_str}'

            modified = re.sub(pattern, replacement, source)
            PARSER_FILE.write_text(modified)

        def rollback():
            source = PARSER_FILE.read_text()
            # Remove the added format line
            modified = re.sub(
                rf'\n    "{re.escape(format_str)}",  # Added by self-heal:.*',
                "",
                source
            )
            PARSER_FILE.write_text(modified)

        return Fix(
            name="add_date_format",
            description=f"Add format '{format_str}' for '{time_str}'",
            apply=apply,
            rollback=rollback,
        )

    @staticmethod
    def _fix_relax_section_regex(output: str, error: Exception) -> Fix:
        """Relax the section regex patterns."""
        def apply():
            source = PARSER_FILE.read_text()
            # Make the "Resets" pattern more tolerant
            modified = source.replace(
                'Rese[ts]*',
                'Rese[tst]*'  # Also allow "Resett" or other corruptions
            )
            PARSER_FILE.write_text(modified)

        def rollback():
            source = PARSER_FILE.read_text()
            modified = source.replace(
                'Rese[tst]*',
                'Rese[ts]*'
            )
            PARSER_FILE.write_text(modified)

        return Fix(
            name="relax_section_regex",
            description="Relax section regex to handle more corruption",
            apply=apply,
            rollback=rollback,
        )

    @staticmethod
    def _fix_broaden_percentage_regex(output: str, error: Exception) -> Fix:
        """Broaden the percentage extraction regex."""
        def apply():
            source = PARSER_FILE.read_text()
            # Make percentage pattern more flexible
            modified = source.replace(
                r'(\d+)%\s*used',
                r'(\d+)\s*%\s*used'  # Allow space before %
            )
            PARSER_FILE.write_text(modified)

        def rollback():
            source = PARSER_FILE.read_text()
            modified = source.replace(
                r'(\d+)\s*%\s*used',
                r'(\d+)%\s*used'
            )
            PARSER_FILE.write_text(modified)

        return Fix(
            name="broaden_percentage_regex",
            description="Allow whitespace before percentage sign",
            apply=apply,
            rollback=rollback,
        )

    @staticmethod
    def _fix_increase_validation_buffer(output: str, error: Exception) -> Fix:
        """Increase validation buffer times."""
        def apply():
            source = PARSER_FILE.read_text()
            # Increase buffers
            modified = source.replace(
                "hours > 6:",  # 5h + 1h buffer
                "hours > 7:"   # 5h + 2h buffer
            ).replace(
                "hours > 169:",  # 168h + 1h buffer
                "hours > 170:"   # 168h + 2h buffer
            )
            PARSER_FILE.write_text(modified)

        def rollback():
            source = PARSER_FILE.read_text()
            modified = source.replace(
                "hours > 7:",
                "hours > 6:"
            ).replace(
                "hours > 170:",
                "hours > 169:"
            )
            PARSER_FILE.write_text(modified)

        return Fix(
            name="increase_validation_buffer",
            description="Increase validation time buffers",
            apply=apply,
            rollback=rollback,
        )


def infer_date_format(time_str: str) -> Optional[Tuple[str, str]]:
    """
    Attempt to infer the strptime format from a time string.

    Returns (format_string, list_name) or None.
    """
    time_str = time_str.strip()

    # Common patterns
    patterns = [
        # Full date + time with year
        (r"^([A-Z][a-z]{2})\s+(\d{1,2})\s+(\d{4})\s+at\s+(\d{1,2}):(\d{2})(am|pm)$",
         "%b %d %Y at %I:%M%p", "DATE_FORMATS_WITH_YEAR"),
        (r"^([A-Z][a-z]{2})\s+(\d{1,2})\s+(\d{4})\s+at\s+(\d{1,2})(am|pm)$",
         "%b %d %Y at %I%p", "DATE_FORMATS_WITH_YEAR"),

        # Date without year + time
        (r"^([A-Z][a-z]{2})\s+(\d{1,2})\s+at\s+(\d{1,2}):(\d{2})(am|pm)$",
         "%b %d at %I:%M%p", "DATE_FORMATS_NO_YEAR"),
        (r"^([A-Z][a-z]{2})\s+(\d{1,2})\s+at\s+(\d{1,2})(am|pm)$",
         "%b %d at %I%p", "DATE_FORMATS_NO_YEAR"),

        # Day-first format
        (r"^(\d{1,2})\s+([A-Z][a-z]{2})\s+at\s+(\d{1,2}):(\d{2})(am|pm)$",
         "%d %b at %I:%M%p", "DATE_FORMATS_NO_YEAR"),

        # Time only
        (r"^(\d{1,2}):(\d{2})(am|pm)$", "%I:%M%p", "TIME_FORMATS"),
        (r"^(\d{1,2})(am|pm)$", "%I%p", "TIME_FORMATS"),
    ]

    for pattern, fmt, list_name in patterns:
        if re.match(pattern, time_str, re.IGNORECASE):
            return fmt, list_name

    return None


# --- Verification ---
def verify_fix(attempts: int = VERIFY_ATTEMPTS, threshold: int = VERIFY_THRESHOLD) -> bool:
    """
    Verify fix works by testing against live output.

    Uses 2/3 rule: at least 2 out of 3 attempts must succeed.
    """
    successes = 0

    for i in range(attempts):
        try:
            output = capture_usage_output()
            result = parse_usage(output)
            valid, msg = validate_result(result)
            if valid:
                successes += 1
        except Exception:
            pass

        # Short delay between attempts
        if i < attempts - 1:
            import time
            time.sleep(1)

    return successes >= threshold


# --- Fixture Capture ---
def capture_fixture(output: str, result: ParseResult):
    """Save successful parse as fixture for regression testing."""
    FIXTURE_DIR.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    # Save raw output
    txt_path = FIXTURE_DIR / f"{timestamp}.txt"
    txt_path.write_text(output)

    # Save expected values
    expected = {
        "session_percent": result.session_percent,
        "session_reset_str": result.session_reset_str,
        "week_percent": result.week_percent,
        "week_reset_str": result.week_reset_str,
        "captured_at": datetime.now().isoformat(),
        "auto_healed": True,
    }
    json_path = FIXTURE_DIR / f"{timestamp}.expected.json"
    json_path.write_text(json.dumps(expected, indent=2))

    print(f"  Captured fixture: {txt_path.name}")


# --- Git Operations ---
def git_commit_fix(fix: Fix):
    """Commit the fix with a descriptive message."""
    try:
        subprocess.run(
            ["git", "add", str(PARSER_FILE)],
            capture_output=True,
            check=True,
            cwd=PROJECT_ROOT,
        )

        message = f"fix(parser): {fix.description}\n\nAuto-generated by self-heal system"

        subprocess.run(
            ["git", "commit", "-m", message],
            capture_output=True,
            check=True,
            cwd=PROJECT_ROOT,
        )

        print(f"  Committed: {fix.description}")
    except subprocess.CalledProcessError as e:
        print(f"  Warning: Could not commit - {e}")


# --- History ---
def record_history(
    success: bool,
    failure_type: FailureType,
    fix: Optional[Fix],
    message: str
):
    """Record healing attempt to history."""
    HISTORY_FILE.parent.mkdir(parents=True, exist_ok=True)

    history = []
    if HISTORY_FILE.exists():
        try:
            history = json.loads(HISTORY_FILE.read_text())
        except json.JSONDecodeError:
            history = []

    entry = {
        "timestamp": datetime.now().isoformat(),
        "success": success,
        "failure_type": failure_type.value,
        "fix_applied": fix.name if fix else None,
        "message": message,
    }

    history.append(entry)

    # Keep last 100 entries
    history = history[-100:]

    HISTORY_FILE.write_text(json.dumps(history, indent=2))


# --- Main Healing Loop ---
def heal(max_iterations: int = MAX_ITERATIONS, auto: bool = False) -> HealResult:
    """
    Main self-healing loop.

    1. Capture live output
    2. Try to parse
    3. If failure, classify and apply fix strategies
    4. Verify fix works (2/3 rule)
    5. Commit successful fix and capture fixture
    """
    print("Self-Healing System")
    print("=" * 40)

    # First check if healing is even needed
    print("\nChecking if healing is needed...")

    try:
        output = capture_usage_output()
        result = parse_usage(output)
        valid, msg = validate_result(result)

        if valid:
            print("Parser is working correctly. No healing needed.")
            return HealResult(success=True, message="Parser working")

        print(f"Validation failed: {msg}")
        failure_type, confidence = FailureType.VALIDATION, 0.90
        last_error = ValidationError(msg)

    except CaptureError as e:
        print(f"Capture failed: {e}")
        failure_type, confidence = classify_failure(e, "")
        last_error = e
        output = ""

    except Exception as e:
        print(f"Parse failed: {e}")
        failure_type, confidence = classify_failure(e, output if 'output' in dir() else "")
        last_error = e

    # Healing loop
    for iteration in range(max_iterations):
        print(f"\n--- Iteration {iteration + 1}/{max_iterations} ---")
        print(f"Failure type: {failure_type.value} (confidence: {confidence:.0%})")

        strategies = FixStrategies.get_strategies(failure_type)
        if not strategies:
            print(f"No strategies available for {failure_type.value}")
            continue

        for strategy_name in strategies:
            print(f"\nTrying strategy: {strategy_name}")

            fix = FixStrategies.create_fix(strategy_name, output, last_error)
            if not fix:
                print(f"  Could not create fix for {strategy_name}")
                continue

            print(f"  {fix.description}")

            # Apply fix
            print("  Applying fix...")
            try:
                fix.apply()
            except Exception as e:
                print(f"  Failed to apply: {e}")
                continue

            # Verify fix
            print("  Verifying fix (2/3 rule)...")
            if verify_fix():
                print("  Fix verified!")

                # Capture the working output as fixture
                try:
                    test_output = capture_usage_output()
                    test_result = parse_usage(test_output)
                    capture_fixture(test_output, test_result)
                except Exception:
                    pass

                # Commit if not in auto mode (or always in auto mode)
                if auto:
                    git_commit_fix(fix)

                record_history(True, failure_type, fix, "Fix applied and verified")

                return HealResult(
                    success=True,
                    message=f"Fixed with {strategy_name}",
                    fix=fix,
                    iterations=iteration + 1,
                )
            else:
                print("  Fix did not pass verification, rolling back...")
                try:
                    fix.rollback()
                except Exception as e:
                    print(f"  Warning: Rollback failed - {e}")

        # Re-check status after trying all strategies
        try:
            output = capture_usage_output()
            result = parse_usage(output)
            valid, msg = validate_result(result)

            if valid:
                print("\nParser now working (possibly due to transient issue)")
                return HealResult(success=True, message="Parser recovered")

            failure_type, confidence = classify_failure(
                ValidationError(msg) if msg else last_error,
                output
            )
        except Exception as e:
            failure_type, confidence = classify_failure(e, output if 'output' in dir() else "")
            last_error = e

    # All iterations exhausted
    record_history(False, failure_type, None, "All strategies exhausted")

    return HealResult(
        success=False,
        message="All strategies exhausted",
        iterations=max_iterations,
    )


def check_status() -> bool:
    """Check if the parser is working without healing."""
    try:
        output = capture_usage_output()
        result = parse_usage(output)
        valid, msg = validate_result(result)
        return valid
    except Exception:
        return False


def main():
    parser = argparse.ArgumentParser(description="Self-Healing System for Usage Parser")
    parser.add_argument("--check", action="store_true", help="Check if healing is needed")
    parser.add_argument("--auto", action="store_true", help="Run without prompts (for cron)")
    parser.add_argument("--history", action="store_true", help="Show healing history")

    args = parser.parse_args()

    if args.history:
        if HISTORY_FILE.exists():
            history = json.loads(HISTORY_FILE.read_text())
            print("Healing History (last 10):")
            print("-" * 60)
            for entry in history[-10:]:
                status = "OK" if entry["success"] else "FAIL"
                fix = entry.get("fix_applied", "none")
                print(f"{entry['timestamp']}: [{status}] {entry['failure_type']} - {fix}")
        else:
            print("No healing history found.")
        return

    if args.check:
        if check_status():
            print("Parser is working correctly.")
            sys.exit(0)
        else:
            print("Parser needs healing.")
            sys.exit(1)

    # Run healing
    result = heal(auto=args.auto)

    if result.success:
        print(f"\nSuccess: {result.message}")
        sys.exit(0)
    else:
        print(f"\nFailed: {result.message}")
        sys.exit(4)


if __name__ == "__main__":
    main()
