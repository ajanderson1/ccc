# Brainstorm: Robust Usage Parser with Functional Self-Healing

**Date**: 2026-02-04
**Status**: Approved for implementation

## Goals

1. **Robust parser**: Handle whatever output format Claude Code returns
2. **Functional self-healing**: When parsing fails, automatically diagnose and fix the code
3. **Simple as possible**: Complexity is acceptable only where it serves reliability

## Current State Analysis

### What's Broken

**Parser (`cc_usage.sh`):**
- Output capture is unreliable (expect timing races)
- Date parsing has edge cases (midnight, year-wrap, timezones)
- Regex patterns are brittle to format changes

**Self-Healing (`tests/runner.py`):**
- Doesn't run against live output (tests fixtures only)
- Fix generator produces untested diffs
- No feedback loop to verify fixes work in practice
- AI integration exists but isn't effective

### What Works

- Basic architecture (capture → parse → display) is sound
- Test fixtures capture known-good formats
- Pace calculation logic is correct once parsing succeeds

## Design: Robust Parser

### Principle: Parse What Matters, Ignore What Doesn't

The parser needs to extract exactly 4 values:
1. Session usage percentage (integer 0-100)
2. Session reset time (datetime)
3. Week usage percentage (integer 0-100)
4. Week reset time (datetime)

Everything else (progress bars, styling, "Sonnet only" section) is noise.

### Parser Architecture

```
┌──────────────┐     ┌──────────────┐     ┌──────────────┐
│   Capture    │────▶│    Parse     │────▶│   Display    │
│   Layer      │     │    Layer     │     │    Layer     │
└──────────────┘     └──────────────┘     └──────────────┘
      │                    │                    │
      ▼                    ▼                    ▼
  expect/script      Multi-strategy       Visual output
  with retries       extraction          with pace calc
```

### Multi-Strategy Extraction

Instead of one fragile regex, use a cascade of extraction strategies:

```python
def extract_percentage(text: str, section_name: str) -> Optional[int]:
    """Try multiple strategies to find percentage."""
    strategies = [
        extract_via_regex,      # "42% used"
        extract_via_structure,  # Line after section header
        extract_via_numbers,    # Find all numbers, filter by context
    ]
    for strategy in strategies:
        result = strategy(text, section_name)
        if result is not None and 0 <= result <= 100:
            return result
    return None
```

### Robust Date Parsing with `dateutil`

Replace manual strptime with `dateutil.parser`:

```python
from dateutil import parser as dateutil_parser
from dateutil.relativedelta import relativedelta

def parse_reset_time(time_str: str, now: datetime = None) -> Optional[datetime]:
    """Parse reset time with automatic format detection."""
    now = now or datetime.now()

    # Clean input
    time_str = clean_input(time_str)

    # Let dateutil figure out the format
    try:
        dt = dateutil_parser.parse(time_str, fuzzy=True)
    except:
        return None

    # Add year if missing (dateutil defaults to current year)
    # Add date if time-only (combine with today/tomorrow)
    # Ensure result is in the future (it's a "reset" time)

    return adjust_to_future(dt, now)
```

### Capture Layer Improvements

```python
def capture_usage_output(max_retries: int = 3) -> str:
    """Capture claude /usage output reliably."""
    for attempt in range(max_retries):
        output = run_expect_capture()

        # Validate we got usable content
        if validate_output(output):
            return output

        # Log attempt for debugging
        log_capture_attempt(attempt, output)

        # Exponential backoff
        time.sleep(0.5 * (attempt + 1))

    raise CaptureError(f"Failed after {max_retries} attempts")
```

## Design: Functional Self-Healing

### Principle: Test Against Reality, Fix Against Reality

The current system tests against fixtures. When Claude's output format changes, fixtures are stale. Self-healing should:

1. Detect failures using **live output** (not just fixtures)
2. Diagnose using actual captured content
3. Apply fixes and verify against live output
4. Commit only when live tests pass

### Self-Healing Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                    SELF-HEALING LOOP                            │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│  ┌──────────┐    ┌──────────┐    ┌──────────┐    ┌──────────┐ │
│  │ Capture  │───▶│ Diagnose │───▶│ Generate │───▶│ Verify   │ │
│  │ Live     │    │ Failure  │    │ Fix      │    │ Fix      │ │
│  └──────────┘    └──────────┘    └──────────┘    └──────────┘ │
│       │               │               │               │        │
│       │               ▼               │               │        │
│       │         Classification        │               │        │
│       │         - Parse failure       │               │        │
│       │         - Capture failure     │               │        │
│       │         - Validation failure  │               │        │
│       │                               ▼               │        │
│       │                         Fix Strategy          │        │
│       │                         - Add date format     │        │
│       │                         - Adjust regex        │        │
│       │                         - Extend timeout      │        │
│       │                                               ▼        │
│       │                                        Re-test Live   │
│       │                                        └───────┐      │
│       │                                                │      │
│       └────────────────────────────────────────────────┘      │
│                          Loop until fixed                      │
└─────────────────────────────────────────────────────────────────┘
```

### Failure Classification

```python
class FailureType(Enum):
    CAPTURE_TIMEOUT = "capture_timeout"      # expect didn't get output
    CAPTURE_INCOMPLETE = "capture_incomplete" # Got partial output
    PARSE_SECTION_MISSING = "section_missing" # Can't find "Current session"
    PARSE_PERCENTAGE = "percentage_parse"    # Can't extract X%
    PARSE_DATE = "date_parse"               # Can't parse reset time
    VALIDATION = "validation"               # Parsed value nonsensical

def classify_failure(error: Exception, output: str) -> FailureType:
    """Classify what type of failure occurred."""
    ...
```

### Fix Generation (Simplified)

Instead of AI-generated diffs, use **pattern-matched fixes**:

```python
FIX_STRATEGIES = {
    FailureType.CAPTURE_TIMEOUT: [
        IncrementTimeout(by_seconds=5),
        AddRetryAttempt(),
    ],
    FailureType.PARSE_DATE: [
        AddDateFormat(from_example=True),
        RelaxDateRegex(),
    ],
    FailureType.PARSE_SECTION_MISSING: [
        RelaxSectionRegex(),
        AddAlternateSection(),
    ],
}
```

Each strategy is a **deterministic code transformation**, not an AI guess.

### Live Verification

```python
def verify_fix(fix: Fix) -> bool:
    """Apply fix and verify against live output."""
    # 1. Apply fix to code
    apply_fix(fix)

    # 2. Capture fresh live output
    try:
        output = capture_usage_output()
    except CaptureError:
        rollback(fix)
        return False

    # 3. Parse with fixed code
    try:
        result = parse_usage(output)
    except ParseError:
        rollback(fix)
        return False

    # 4. Validate result makes sense
    if not validate_result(result):
        rollback(fix)
        return False

    return True
```

### Fixture Capture on Success

When self-healing succeeds, **capture the new format as a fixture**:

```python
def on_heal_success(output: str, result: ParseResult):
    """Save new format as golden fixture."""
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    # Save raw output
    fixture_path = f"tests/fixtures/captured/healed_{timestamp}.txt"
    save_fixture(fixture_path, output)

    # Save expected values
    expected_path = fixture_path.replace('.txt', '.expected.json')
    save_expected(expected_path, result)

    # Commit both
    git_commit(f"Add fixture from successful heal: {timestamp}")
```

## Key Decisions

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Date parsing | `dateutil.parser` | Handles format variations automatically |
| Extraction strategy | Multi-strategy cascade | Graceful degradation |
| Self-healing trigger | Live capture failure | Test against reality |
| Fix generation | Pattern-matched transforms | Deterministic, testable |
| Fix verification | Live re-test | Proves fix works |
| Fixture strategy | Capture on success | Build regression suite organically |

## Implementation Plan

### Phase 1: Rewrite Parser (~200 lines)

1. New `cc_usage.py` with:
   - Reliable capture with retries
   - Multi-strategy extraction
   - `dateutil`-based date parsing
   - Same visual output format

2. Verify parity with existing script

### Phase 2: Rebuild Self-Healing (~300 lines)

1. New `self_heal.py` with:
   - Live capture testing
   - Failure classification
   - Pattern-matched fix strategies
   - Live verification loop
   - Fixture capture on success

2. Remove old `tests/self_heal/` directory
3. Remove old `tests/runner.py`

### Phase 3: Integration

1. Wire self-healing to run on failure:
   ```bash
   ./cc_usage.py || python self_heal.py
   ```

2. Add cron job for periodic health check:
   ```bash
   0 */4 * * * ./cc_usage.py --quiet || python self_heal.py --auto
   ```

### Phase 4: Clean Up

1. Delete deprecated files
2. Update README
3. Keep minimal test suite (smoke tests only)

## Success Criteria

1. **Parser**: Works reliably for at least 30 days without manual fixes
2. **Self-healing**: Successfully auto-fixes at least one real format change
3. **Simplicity**: Total codebase under 600 lines (excluding fixtures)

## Risks

| Risk | Mitigation |
|------|------------|
| Claude output fundamentally changes structure | Multi-strategy extraction handles variations |
| dateutil can't parse a new format | Falls back to explicit strptime patterns |
| Self-healing makes bad fix | Rollback on verification failure |
| Live testing is flaky | Require 2/3 successful runs before committing |

## Open Questions

1. Should self-healing run automatically (cron) or on-demand only?
2. How many fixture backlog should we keep?
3. Should we alert on repeated heal failures?
