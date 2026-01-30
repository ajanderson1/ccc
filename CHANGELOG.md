# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.2.0] - 2025-01-30

### Added
- **Sync verification system** - Detects when `parser_extracted.py` diverges from `cc_usage.sh` embedded Python code
  - `tests/self_heal/sync_verifier.py` - Core verification logic
  - `tests/unit/test_sync.py` - Fails fast when code diverges
  - CLI: `python tests/runner.py --check-sync`
- Integrated sync verification into self-healing runner (runs before tests)

### Fixed
- **Weekly tomorrow logic** - `parser_extracted.py` now matches `cc_usage.sh` behavior
  - Was: blindly adding 7 days for weekly resets
  - Now: finds next occurrence within 7 days (could be tomorrow)
- **Time percentage truncation** - Changed `int(elapsed_pct)` to `round(elapsed_pct)` in `cc_usage.sh`
  - Was: 0.6% displayed as "0% time"
  - Now: 0.6% displays as "1% time"
- Updated `test_tomorrow_logic_weekly` test expectation to match correct behavior

### Changed
- Self-healing runner now warns when test code has diverged from production code

## [0.1.0] - 2025-01-30

### Added
- Initial self-healing test system for `cc_usage.sh`
- Parser extraction module (`tests/parser_extracted.py`)
- Unit tests for:
  - ANSI stripping
  - Date/time parsing
  - Regex extraction
  - Validation logic
- Self-healing components:
  - Failure classifier
  - Root cause analyzer
  - Fix generator
  - Code modifier
  - Regression detector
- Test fixtures (golden, generated)
- CLI runner with multiple modes:
  - `--test-only` - Run tests without healing
  - `--dry-run` - Show proposed fixes
  - `--capture` - Capture live `/usage` output
  - `--generate-fixtures` - Create synthetic test cases
  - `--history` - View healing history
