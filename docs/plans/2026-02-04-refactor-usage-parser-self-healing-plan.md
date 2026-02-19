---
title: "refactor: Robust Usage Parser with Functional Self-Healing"
type: refactor
date: 2026-02-04
---

# Robust Usage Parser with Functional Self-Healing

## Overview

Complete rewrite of the Claude Code Usage Analyzer to create a robust parser that handles variable output formats, plus a functional self-healing system that can automatically diagnose and fix parsing failures by testing against live output.

**Current state**: ~2,100 lines of complex code that frequently breaks
**Target state**: ~500 lines of focused, reliable code

## Problem Statement

The current `cc_usage.sh` continually breaks due to:
1. **Fragile capture mechanism** - expect timing races cause empty/partial output
2. **Brittle date parsing** - manual strptime with edge cases (midnight, year-wrap, timezones)
3. **Single-strategy extraction** - one regex failure = total failure
4. **Ineffective self-healing** - tests against stale fixtures, not live output

Evidence: 20+ commits fixing parsing logic in the last month.

## Proposed Solution

### Architecture

```
┌─────────────────────────────────────────────────────────────────────┐
│                         cc_usage.py (~200 lines)                    │
├─────────────────────────────────────────────────────────────────────┤
│  Capture Layer      │  Parse Layer         │  Display Layer        │
│  - expect/script    │  - Multi-strategy    │  - Progress bars      │
│  - Retry w/backoff  │  - dateutil parsing  │  - Pace calculation   │
│  - Validation       │  - Cross-validation  │  - Reset countdown    │
└─────────────────────────────────────────────────────────────────────┘
                              │ failure
                              ▼
┌─────────────────────────────────────────────────────────────────────┐
│                       self_heal.py (~300 lines)                     │
├─────────────────────────────────────────────────────────────────────┤
│  Capture Live  →  Diagnose  →  Apply Fix  →  Verify Live  →  Commit│
│                      │              │              │                │
│               Classification   Strategy       Rollback if           │
│               - capture_timeout  - timeout++    fails               │
│               - parse_date       - add_format                       │
│               - section_missing  - relax_regex                      │
└─────────────────────────────────────────────────────────────────────┘
```

### Key Design Decisions

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Date parsing | `python-dateutil` | Automatic format detection, handles edge cases |
| Extraction | Multi-strategy cascade | Graceful degradation, not single point of failure |
| Self-healing trigger | Live capture failure | Test against reality, not stale fixtures |
| Fix generation | Pattern-matched transforms | Deterministic, testable, no AI unpredictability |
| Fix verification | Live re-test (2/3 rule) | Proves fix works before commit |
| Concurrency | Lock file | Prevent race conditions between cron and manual runs |

## Technical Approach

### Phase 1: Rewrite Parser (`cc_usage.py`)

#### 1.1 Capture Layer

```python
# cc_usage.py - capture layer

LOCK_FILE = Path("~/.cc_usage.lock").expanduser()

def acquire_lock() -> bool:
    """Acquire lock file to prevent concurrent runs."""
    try:
        LOCK_FILE.touch(exist_ok=False)
        return True
    except FileExistsError:
        # Check if stale (older than 5 minutes)
        if LOCK_FILE.stat().st_mtime < time.time() - 300:
            LOCK_FILE.unlink()
            LOCK_FILE.touch()
            return True
        return False

def capture_usage_output(max_retries: int = 3) -> str:
    """Capture claude /usage output with retries."""
    for attempt in range(max_retries):
        output = run_expect_capture()

        if validate_capture(output):
            return output

        time.sleep(0.5 * (attempt + 1))  # Exponential backoff

    raise CaptureError(f"Failed after {max_retries} attempts")

def validate_capture(output: str) -> bool:
    """Validate captured output has required sections."""
    clean = strip_ansi(output)
    return all([
        "Current session" in clean,
        "Current week" in clean,
        "% used" in clean,
    ])
```

#### 1.2 Parse Layer (Multi-Strategy)

```python
# cc_usage.py - parse layer

def extract_percentage(text: str, section: str) -> Optional[int]:
    """Extract percentage using cascade of strategies."""
    strategies = [
        extract_via_regex,      # "42% used" pattern
        extract_via_structure,  # Line positions relative to header
        extract_via_numbers,    # Find all numbers, filter by context
    ]

    for strategy in strategies:
        result = strategy(text, section)
        if result is not None and 0 <= result <= 100:
            return result
    return None

def parse_reset_time(time_str: str, window_hours: int, now: datetime = None) -> Optional[datetime]:
    """Parse reset time with dateutil + fallback."""
    now = now or datetime.now()
    clean = clean_time_string(time_str)

    # Strategy 1: dateutil with fuzzy parsing
    try:
        dt = dateutil_parser.parse(clean, fuzzy=True)
        dt = adjust_to_future(dt, now, window_hours)
        return dt
    except (ValueError, ParserError):
        pass

    # Strategy 2: Explicit format list (fallback)
    for fmt in TIME_FORMATS:
        try:
            dt = datetime.strptime(clean, fmt)
            dt = adjust_to_future(dt, now, window_hours)
            return dt
        except ValueError:
            continue

    return None

def adjust_to_future(dt: datetime, now: datetime, window_hours: int) -> datetime:
    """Ensure reset time is in future and within window."""
    # If time-only was parsed, combine with today
    if dt.year == 1900:  # dateutil default year
        dt = datetime.combine(now.date(), dt.time())

    # If in past, adjust forward
    if dt < now:
        if window_hours <= 24:
            dt += timedelta(days=1)  # Tomorrow for session
        else:
            # Weekly: find next occurrence within 7 days
            for days in range(1, 8):
                candidate = dt + timedelta(days=days)
                if candidate > now and (candidate - now).total_seconds() / 3600 <= window_hours:
                    return candidate

    return dt
```

#### 1.3 Display Layer

```python
# cc_usage.py - display layer (preserve existing visual format)

def display_usage(result: ParseResult, duration: float):
    """Display usage analysis with pace calculation."""
    now = datetime.now()

    print(f"\n{BOLD}Usage Analysis - {now.strftime('%A %B %d at %H:%M')} (took {duration:.2f}s){RESET}\n")

    display_section("Weekly Usage (168h)", result.week_percent, result.week_reset_dt, 168)
    print(f"\n  {DIM}---{RESET}\n")
    display_section("Session Usage (5h)", result.session_percent, result.session_reset_dt, 5)

    print(f"\n  {DIM}Raw: week='{result.week_reset_str}' session='{result.session_reset_str}'{RESET}")
```

#### 1.4 Validation

```python
# cc_usage.py - validation

def validate_result(result: ParseResult, now: datetime = None) -> Tuple[bool, Optional[str]]:
    """Validate parsed result is sensible."""
    now = now or datetime.now()

    # Check percentages
    if not (0 <= result.session_percent <= 100):
        return False, f"Session percent {result.session_percent} out of range"
    if not (0 <= result.week_percent <= 100):
        return False, f"Week percent {result.week_percent} out of range"

    # Check reset times are in future
    if result.session_reset_dt and result.session_reset_dt < now:
        return False, f"Session reset {result.session_reset_dt} is in past"
    if result.week_reset_dt and result.week_reset_dt < now:
        return False, f"Week reset {result.week_reset_dt} is in past"

    # Check reset times are within window
    if result.session_reset_dt:
        hours = (result.session_reset_dt - now).total_seconds() / 3600
        if hours > 6:  # 5h window + 1h buffer
            return False, f"Session reset {hours:.1f}h away exceeds window"

    if result.week_reset_dt:
        hours = (result.week_reset_dt - now).total_seconds() / 3600
        if hours > 169:  # 168h window + 1h buffer
            return False, f"Week reset {hours:.1f}h away exceeds window"

    # Cross-validate: session reset should be before week reset
    if result.session_reset_dt and result.week_reset_dt:
        if result.session_reset_dt > result.week_reset_dt:
            return False, "Session reset after week reset is impossible"

    return True, None
```

### Phase 2: Rebuild Self-Healing (`self_heal.py`)

#### 2.1 Failure Classification

```python
# self_heal.py - classification

class FailureType(Enum):
    CAPTURE_TIMEOUT = "capture_timeout"
    CAPTURE_INCOMPLETE = "capture_incomplete"
    PARSE_SECTION_MISSING = "section_missing"
    PARSE_PERCENTAGE = "percentage_parse"
    PARSE_DATE = "date_parse"
    VALIDATION = "validation"
    UNKNOWN = "unknown"

def classify_failure(error: Exception, output: str) -> Tuple[FailureType, float]:
    """Classify failure type with confidence score."""
    error_str = str(error).lower()

    if isinstance(error, CaptureError):
        if "timeout" in error_str:
            return FailureType.CAPTURE_TIMEOUT, 0.95
        return FailureType.CAPTURE_INCOMPLETE, 0.85

    if "current session" not in output.lower() or "current week" not in output.lower():
        return FailureType.PARSE_SECTION_MISSING, 0.90

    if "time data" in error_str or "does not match format" in error_str:
        return FailureType.PARSE_DATE, 0.95

    if "out of range" in error_str or "exceeds window" in error_str:
        return FailureType.VALIDATION, 0.90

    if "% used" not in output.lower():
        return FailureType.PARSE_PERCENTAGE, 0.85

    return FailureType.UNKNOWN, 0.50
```

#### 2.2 Fix Strategies

```python
# self_heal.py - fix strategies

@dataclass
class Fix:
    name: str
    description: str
    apply: Callable[[], None]
    rollback: Callable[[], None]

class FixStrategies:
    """Pattern-matched fix strategies by failure type."""

    STRATEGIES = {
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
            "relax_date_regex",
        ],
        FailureType.PARSE_SECTION_MISSING: [
            "relax_section_regex",
            "add_alternate_section",
        ],
        FailureType.PARSE_PERCENTAGE: [
            "broaden_percentage_regex",
        ],
    }

    @staticmethod
    def get_fix(failure_type: FailureType, output: str, strategy_name: str) -> Optional[Fix]:
        """Generate a fix for the given failure type and strategy."""
        if strategy_name == "add_date_format":
            return FixStrategies._fix_add_date_format(output)
        elif strategy_name == "increment_timeout":
            return FixStrategies._fix_increment_timeout()
        # ... other strategies
        return None

    @staticmethod
    def _fix_add_date_format(output: str) -> Optional[Fix]:
        """Extract unrecognized date format and add to format list."""
        # Find the reset time string that failed
        match = re.search(r'Resets?\s+(.+?)(?:\s{2,}|\n|$)', output, re.IGNORECASE)
        if not match:
            return None

        time_str = match.group(1).strip()
        inferred_format = infer_date_format(time_str)

        if not inferred_format:
            return None

        def apply():
            add_format_to_list(inferred_format, time_str)

        def rollback():
            remove_format_from_list(inferred_format)

        return Fix(
            name="add_date_format",
            description=f"Add format '{inferred_format}' for '{time_str}'",
            apply=apply,
            rollback=rollback,
        )
```

#### 2.3 Live Verification Loop

```python
# self_heal.py - verification

def heal(max_iterations: int = 3) -> HealResult:
    """Main self-healing loop."""

    for iteration in range(max_iterations):
        # Step 1: Capture live output
        try:
            output = capture_usage_output()
        except CaptureError as e:
            failure_type, confidence = classify_failure(e, "")
            output = ""
        else:
            # Step 2: Try to parse
            try:
                result = parse_usage(output)
                valid, msg = validate_result(result)
                if valid:
                    return HealResult(success=True, message="Parser working")
                failure_type, confidence = FailureType.VALIDATION, 0.90
            except Exception as e:
                failure_type, confidence = classify_failure(e, output)

        # Step 3: Get fix strategies for this failure type
        strategies = FixStrategies.STRATEGIES.get(failure_type, [])

        for strategy_name in strategies:
            fix = FixStrategies.get_fix(failure_type, output, strategy_name)
            if not fix:
                continue

            # Step 4: Apply fix
            fix.apply()

            # Step 5: Verify fix with 2/3 rule
            successes = 0
            for _ in range(3):
                try:
                    test_output = capture_usage_output()
                    test_result = parse_usage(test_output)
                    valid, _ = validate_result(test_result)
                    if valid:
                        successes += 1
                except:
                    pass

            if successes >= 2:
                # Fix worked! Commit and capture fixture
                git_commit_fix(fix)
                capture_fixture(test_output, test_result)
                return HealResult(success=True, fix=fix)
            else:
                # Fix didn't work, rollback
                fix.rollback()

        # No strategy worked for this iteration
        continue

    return HealResult(success=False, message="All strategies exhausted")
```

#### 2.4 Fixture Capture

```python
# self_heal.py - fixture capture

def capture_fixture(output: str, result: ParseResult):
    """Save successful parse as fixture for regression testing."""
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    fixture_dir = Path("tests/fixtures/healed")
    fixture_dir.mkdir(parents=True, exist_ok=True)

    # Save raw output
    txt_path = fixture_dir / f"{timestamp}.txt"
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
    json_path = fixture_dir / f"{timestamp}.expected.json"
    json_path.write_text(json.dumps(expected, indent=2))
```

### Phase 3: Integration

#### 3.1 CLI Interface

```python
# cc_usage.py - CLI

def main():
    parser = argparse.ArgumentParser(description="Claude Code Usage Analyzer")
    parser.add_argument("--loop", action="store_true", help="Run continuously")
    parser.add_argument("--interval", type=int, default=300, help="Loop interval seconds")
    parser.add_argument("--raw", action="store_true", help="Output raw captured content")
    parser.add_argument("--quiet", action="store_true", help="Suppress output, exit code only")
    parser.add_argument("--heal", action="store_true", help="Trigger self-healing on failure")
    args = parser.parse_args()

    try:
        if not acquire_lock():
            print("Another instance is running", file=sys.stderr)
            sys.exit(2)

        if args.loop:
            run_loop(args.interval, args.quiet)
        else:
            run_once(args.raw, args.quiet, args.heal)
    finally:
        release_lock()

def run_once(raw: bool, quiet: bool, heal_on_fail: bool) -> int:
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
            display_usage(result, time.time() - start)

        return 0

    except (CaptureError, ParseError, ValidationError) as e:
        if not quiet:
            print(f"Error: {e}", file=sys.stderr)

        if heal_on_fail:
            from self_heal import heal
            result = heal()
            return 0 if result.success else 1

        return 1
```

#### 3.2 Exit Codes

| Code | Meaning |
|------|---------|
| 0 | Success |
| 1 | Parse/validation failure |
| 2 | Lock acquisition failed (another instance running) |
| 3 | Capture failure (Claude not responding) |
| 4 | Self-healing failed |

#### 3.3 Cron Integration

```bash
# Crontab entry for periodic health check
0 */4 * * * /path/to/cc_usage.py --quiet --heal >> /var/log/cc_usage.log 2>&1
```

### Phase 4: Cleanup

#### Files to Delete

```
tests/self_heal/classifier.py
tests/self_heal/analyzer.py
tests/self_heal/fix_generator.py
tests/self_heal/code_modifier.py
tests/self_heal/regression_detector.py
tests/self_heal/sync_verifier.py
tests/self_heal/__init__.py
tests/runner.py
```

#### Files to Keep (Modified)

```
cc_usage.py          # New rewrite (replaces cc_usage.sh)
self_heal.py         # New self-healing module
tests/conftest.py    # Simplified fixtures
tests/test_parser.py # Minimal smoke tests
tests/fixtures/      # Keep golden fixtures
```

#### Dependencies

```
# pyproject.toml additions
[project]
dependencies = [
    "python-dateutil>=2.8.0",
]
```

## Acceptance Criteria

### Functional Requirements

- [x] Parser extracts session percentage (0-100)
- [x] Parser extracts session reset time (datetime in future)
- [x] Parser extracts week percentage (0-100)
- [x] Parser extracts week reset time (datetime in future)
- [x] Multi-strategy extraction handles format variations
- [x] dateutil parses common date formats automatically
- [x] Cross-validation catches semantic errors
- [x] Lock file prevents concurrent runs
- [x] `--heal` flag triggers self-healing on failure
- [x] `--quiet` flag suppresses output (exit code only)
- [x] `--loop` mode works with countdown

### Non-Functional Requirements

- [x] Parser runs in <2 seconds
- [x] Total codebase under 600 lines (excluding fixtures)
- [x] Self-healing completes within 60 seconds
- [x] No external API dependencies (no AI)

### Quality Gates

- [x] All existing golden fixtures still pass
- [x] New fixtures captured on successful heals
- [x] Exit codes properly indicate failure modes
- [x] Lock file cleaned up on exit

## Success Metrics

1. **Reliability**: Parser works for 30 days without manual fixes
2. **Self-Healing**: At least one successful auto-fix of a real format change
3. **Simplicity**: Total codebase reduced from ~2,100 to ~500 lines

## Dependencies & Prerequisites

- Python 3.9+
- `python-dateutil` library
- `expect` command (for terminal capture)
- Claude Code CLI (`claude` or `cc`)

## Risk Analysis & Mitigation

| Risk | Probability | Impact | Mitigation |
|------|-------------|--------|------------|
| Claude output fundamentally changes | Medium | High | Multi-strategy extraction handles variations |
| dateutil can't parse new format | Low | Medium | Fallback to explicit strptime patterns |
| Self-healing makes bad fix | Medium | Medium | 2/3 rule + rollback on verification failure |
| Live testing is flaky | Medium | Low | Require 2/3 successful runs before committing |
| Concurrent execution race | Low | Medium | Lock file with stale detection |

## Future Considerations

1. **API access**: If Anthropic provides proper `/usage` API, this becomes trivial
2. **Notification system**: Alert on repeated heal failures
3. **Fixture rotation**: Auto-delete fixtures older than N days
4. **Web dashboard**: Visual history of usage patterns

## References

### Internal References

- Brainstorm: `docs/brainstorms/2026-02-04-simplify-usage-parser-brainstorm.md`
- Current parser: `tests/parser_extracted.py:65-195` (date parsing logic)
- Current self-heal: `tests/self_heal/fix_generator.py` (fix strategies)
- Fixtures: `tests/fixtures/golden/`

### External References

- python-dateutil: https://dateutil.readthedocs.io/
- Expect manual: https://linux.die.net/man/1/expect

### Related Work

- Changelog: `CHANGELOG.md` (documented edge cases)
- Previous fixes: commits `92f42a0`, `8951629`, `bf75f99`
