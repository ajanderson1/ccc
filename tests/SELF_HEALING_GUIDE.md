# Self-Healing Test System: Quick Start Guide

A fully autonomous test system that detects parsing failures in `cc_usage.sh`, diagnoses root causes, generates fixes, and applies them automatically.

## Quick Start

```bash
# Install dependencies
pip install pytest pyyaml anthropic

# Run tests only (no healing)
python tests/runner.py --test-only

# Full self-healing cycle (test → detect → fix → commit)
python tests/runner.py

# Preview fixes without applying
python tests/runner.py --dry-run
```

## Commands

| Command | Description |
|---------|-------------|
| `python tests/runner.py` | Full autonomous healing cycle |
| `python tests/runner.py --test-only` | Run tests, report failures |
| `python tests/runner.py --dry-run` | Show proposed fixes without applying |
| `python tests/runner.py --capture` | Capture live `/usage` output as fixture |
| `python tests/runner.py --generate-fixtures` | Create synthetic edge cases |
| `python tests/runner.py --history` | View past healing sessions |

## How It Works

```
┌─────────────┐    ┌─────────────┐    ┌─────────────┐    ┌─────────────┐
│  Run Tests  │───▶│  Classify   │───▶│  Generate   │───▶│   Apply     │
│             │    │  Failures   │    │    Fixes    │    │   & Verify  │
└─────────────┘    └─────────────┘    └─────────────┘    └─────────────┘
       │                                                        │
       └────────────────── Loop until fixed ◄───────────────────┘
```

1. **Run Tests** - Execute pytest suite against parser
2. **Classify** - Categorize failures (date format, regex, validation, etc.)
3. **Generate Fixes** - Create fix candidates based on failure type
4. **Apply & Verify** - Apply fix, re-run ALL tests, rollback if regression

## Failure Types

| Type | Trigger | Auto-Fix Strategy |
|------|---------|-------------------|
| `DATE_FORMAT_NEW` | Unknown date format | Add format to parser |
| `REGEX_MISMATCH` | Pattern doesn't match | Broaden regex |
| `VALIDATION_ERROR` | Parsed value invalid | Adjust thresholds |
| `ANSI_CORRUPTION` | Escape sequences remain | Enhance strip regex |
| `UNKNOWN` | Can't classify | AI-assisted diagnosis |

## Adding Test Fixtures

### Capture Real Output
```bash
python tests/runner.py --capture
# Creates: tests/fixtures/captured/live_TIMESTAMP.txt
# Edit the .expected.json to set expected values
```

### Manual Fixture
Create two files in `tests/fixtures/golden/`:

**my_fixture.txt** - Raw `/usage` output
```
Current session
██████████░░░░░░░░░░  25% used
Resets 6:59pm

Current week (all models)
████████████████░░░░  40% used
Resets Jan 29 at 6:59pm
```

**my_fixture.expected.json** - Expected parse results
```json
{
  "session_percent": 25,
  "session_reset_str": "6:59pm",
  "week_percent": 40,
  "week_reset_str": "Jan 29 at 6:59pm"
}
```

## Configuration

Edit `.self_heal/config.yaml`:

```yaml
# Healing limits
max_iterations: 3        # Max fix attempts per run
max_fixes_per_run: 5     # Safety limit
max_lines_changed: 50    # Reject large fixes

# Autonomy (all enabled by default)
auto_apply: true         # Apply fixes automatically
auto_commit: true        # Git commit each fix

# AI assistance (requires ANTHROPIC_API_KEY)
use_ai_for_unknown: true
ai_model: "claude-sonnet-4-20250514"
```

## Safety Features

- **Git branching**: Each fix attempt creates a branch
- **Syntax validation**: Python syntax checked before commit
- **Regression detection**: Re-runs ALL tests after fix
- **Oscillation prevention**: Stops if same state repeats
- **Rollback patches**: Saved in `.self_heal/rollback/`

## Project Structure

```
tests/
├── runner.py              # Main entry point
├── parser_extracted.py    # Testable parser module
├── conftest.py            # Pytest config
├── fixtures/
│   ├── golden/            # Baseline fixtures
│   ├── captured/          # Live captures
│   ├── generated/         # Synthetic edge cases
│   └── quarantine/        # Failing fixtures
├── unit/
│   ├── test_ansi_stripping.py
│   ├── test_date_parsing.py
│   ├── test_regex_extraction.py
│   └── test_validation.py
└── self_heal/
    ├── classifier.py      # Failure classification
    ├── analyzer.py        # Root cause analysis
    ├── fix_generator.py   # Fix strategies
    ├── code_modifier.py   # Safe code changes
    └── regression_detector.py
```

## Troubleshooting

**Tests fail but no healing happens**
- Check `--dry-run` output for proposed fixes
- Verify fix confidence > 0.5 (configurable)

**AI fixes not working**
- Set `ANTHROPIC_API_KEY` environment variable
- Or set `use_ai_for_unknown: false` in config

**Oscillation detected**
- System is flipping between states
- Check history: `python tests/runner.py --history`
- Manually review the conflicting tests

**Fix causes regression**
- System auto-rollbacks regression-causing fixes
- Check `.self_heal/rollback/` for saved states

## Example Session

```
$ python tests/runner.py

============================================================
SELF-HEALING TEST SYSTEM
============================================================

[1/4] Running initial test suite...
Initial state: 82/84 passing
Failures to heal: 2

[Iteration 1/3]
  Classifying failures...

  Processing: test_new_date_format
    Type: DATE_FORMAT_NEW (confidence: 0.95)
    Analyzing root cause...
    Cause: New date format not recognized: '29 Jan at 6pm'
    Generating fix candidates...
    Candidate: Fix(add_format, conf=0.80): Add date format '%d %b at %I%p'
    Applying fix...
    Applied successfully (branch: self-heal/add_format/20260128_143022)
    Verifying fix...
    IMPROVEMENT: 1 more tests passing

[Iteration 2/3]
  ...

============================================================
Healing Progress:
  Iterations: 2
  Fixes Applied: 2
  Fixes Rolled Back: 0
  Initial Failures: 2
  Current Failures: 0
  Net Improvement: 2 tests fixed
============================================================
```
