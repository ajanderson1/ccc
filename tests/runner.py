#!/usr/bin/env python3
"""
Self-Healing Test System Runner for cc_usage.sh

Main orchestrator that:
1. Runs tests and collects failures
2. Classifies and analyzes failures
3. Generates and applies fixes
4. Validates fixes don't cause regressions
5. Commits successful fixes

Usage:
    python tests/runner.py              # Full self-healing cycle
    python tests/runner.py --test-only  # Run tests only, no healing
    python tests/runner.py --dry-run    # Show proposed fixes without applying
    python tests/runner.py --capture    # Capture new fixture from live /usage
    python tests/runner.py --generate-fixtures  # Generate synthetic fixtures
    python tests/runner.py --history    # View healing history
"""

import argparse
import json
import subprocess
import sys
import yaml
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Any, Optional

# Add project root to path
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from tests.self_heal.classifier import FailureClassifier, FailureType
from tests.self_heal.analyzer import RootCauseAnalyzer
from tests.self_heal.fix_generator import FixGenerator, FixCandidate
from tests.self_heal.code_modifier import CodeModifier, ModificationResult
from tests.self_heal.regression_detector import (
    RegressionDetector, TestState, HealingProgress
)
from tests.self_heal.sync_verifier import SyncVerifier, SyncStatus


class SelfHealingRunner:
    """Main orchestrator for the self-healing test system."""

    def __init__(self, config_path: Optional[Path] = None):
        """Initialize the runner with configuration."""
        self.project_root = PROJECT_ROOT
        self.config = self._load_config(config_path)

        # Initialize components
        self.classifier = FailureClassifier()
        self.analyzer = RootCauseAnalyzer(
            parser_source=self._load_parser_source()
        )

        # AI client (optional)
        ai_client = None
        if self.config.get("use_ai_for_unknown", True):
            ai_client = self._init_ai_client()

        self.fix_generator = FixGenerator(
            parser_path=self.project_root / self.config.get(
                "parser_module", "tests/parser_extracted.py"
            ),
            ai_client=ai_client,
        )

        self.code_modifier = CodeModifier(
            project_root=self.project_root,
            max_lines_changed=self.config.get("max_lines_changed", 50),
            auto_commit=self.config.get("auto_commit", True),
            rollback_dir=self.project_root / self.config.get(
                "rollback_dir", ".self_heal/rollback"
            ),
        )

        self.regression_detector = RegressionDetector(
            oscillation_window=self.config.get("oscillation_window", 10),
            require_net_progress=self.config.get("require_net_progress", True),
        )

        # Sync verifier
        self.sync_verifier = SyncVerifier(
            script_path=self.project_root / "cc_usage.sh",
            module_path=self.project_root / "tests" / "parser_extracted.py",
        )

        # History tracking
        self.history_file = self.project_root / self.config.get(
            "history_file", ".self_heal/history.json"
        )
        self.history_file.parent.mkdir(parents=True, exist_ok=True)

    def _load_config(self, config_path: Optional[Path] = None) -> Dict[str, Any]:
        """Load configuration from YAML file."""
        if config_path is None:
            config_path = self.project_root / ".self_heal" / "config.yaml"

        if config_path.exists():
            with open(config_path) as f:
                return yaml.safe_load(f)

        # Default config
        return {
            "max_iterations": 3,
            "max_fixes_per_run": 5,
            "max_lines_changed": 50,
            "auto_apply": True,
            "auto_commit": True,
            "use_ai_for_unknown": True,
            "ai_model": "claude-sonnet-4-20250514",
            "oscillation_window": 10,
            "require_net_progress": True,
        }

    def _load_parser_source(self) -> str:
        """Load the parser source code."""
        parser_path = self.project_root / "tests" / "parser_extracted.py"
        if parser_path.exists():
            return parser_path.read_text()
        return ""

    def _init_ai_client(self) -> Optional[Any]:
        """Initialize the Anthropic client for AI-assisted healing."""
        try:
            import anthropic
            return anthropic.Anthropic()
        except Exception as e:
            print(f"Warning: Could not initialize AI client: {e}")
            return None

    def verify_sync(self) -> SyncStatus:
        """
        Verify parser_extracted.py is in sync with cc_usage.sh.

        This is a critical check - if the files are out of sync, tests
        may pass but the actual script could have different behavior.

        Returns:
            SyncStatus with verification results
        """
        return self.sync_verifier.verify()

    def run_tests(self) -> Dict[str, Any]:
        """
        Run the test suite and return structured results.

        Returns:
            Dict with test results and failure details
        """
        # Run pytest with JSON report
        result = subprocess.run(
            [
                sys.executable, "-m", "pytest",
                str(self.project_root / "tests"),
                "--tb=short",
                "-v",
                "--ignore=tests/runner.py",
                "-q",
            ],
            capture_output=True,
            text=True,
            cwd=self.project_root,
        )

        # Parse output for failures
        failures = []
        passed = []

        # Simple parsing of pytest output
        for line in result.stdout.split('\n'):
            if ' PASSED' in line:
                test_name = line.split(' PASSED')[0].strip()
                passed.append(test_name)
            elif ' FAILED' in line:
                test_name = line.split(' FAILED')[0].strip()
                failures.append({
                    "test_name": test_name,
                    "exception_type": "AssertionError",
                    "exception_message": "Test failed",
                    "traceback": result.stdout,
                })
            elif ' ERROR' in line:
                test_name = line.split(' ERROR')[0].strip()
                failures.append({
                    "test_name": test_name,
                    "exception_type": "Error",
                    "exception_message": "Test error",
                    "traceback": result.stdout,
                })

        return {
            "passed": passed,
            "failures": failures,
            "return_code": result.returncode,
            "stdout": result.stdout,
            "stderr": result.stderr,
        }

    def heal(self, dry_run: bool = False) -> HealingProgress:
        """
        Run the full self-healing loop.

        Args:
            dry_run: If True, show proposed fixes without applying

        Returns:
            HealingProgress with results
        """
        progress = HealingProgress()
        max_iterations = self.config.get("max_iterations", 3)
        max_fixes = self.config.get("max_fixes_per_run", 5)

        print("=" * 60)
        print("SELF-HEALING TEST SYSTEM")
        print("=" * 60)

        # Sync verification (critical first step)
        print("\n[0/4] Verifying sync between cc_usage.sh and parser_extracted.py...")
        sync_status = self.verify_sync()
        if sync_status.in_sync:
            print(f"  {sync_status}")
        else:
            print(f"\n  {sync_status}")
            print("\n  ⚠️  WARNING: Test code has diverged from production code!")
            print("  ⚠️  Tests may pass but cc_usage.sh could have different behavior.")
            print("  ⚠️  Fix the divergence before relying on test results.\n")

        # Initial test run
        print("\n[1/4] Running initial test suite...")
        results = self.run_tests()

        if not results["failures"]:
            print("All tests passing. Nothing to heal.")
            return progress

        # Record initial state
        test_results = {t: True for t in results["passed"]}
        for f in results["failures"]:
            test_results[f["test_name"]] = False

        initial_state = self.regression_detector.record_state(test_results)
        progress.initial_failures = initial_state.failed_count
        progress.current_failures = initial_state.failed_count

        print(f"Initial state: {initial_state.passed_count}/{initial_state.total_count} passing")
        print(f"Failures to heal: {initial_state.failed_count}")

        # Main healing loop
        for iteration in range(max_iterations):
            progress.iterations += 1
            print(f"\n[Iteration {iteration + 1}/{max_iterations}]")

            if progress.fixes_applied >= max_fixes:
                print(f"Max fixes ({max_fixes}) reached. Stopping.")
                break

            # Classify failures
            print("  Classifying failures...")
            classified = self.classifier.classify_batch(results["failures"])

            for failure in classified[:3]:  # Process up to 3 failures per iteration
                print(f"\n  Processing: {failure.test_name}")
                print(f"    Type: {failure.failure_type.name} "
                      f"(confidence: {failure.confidence:.2f})")

                # Analyze root cause
                print("    Analyzing root cause...")
                root_cause = self.analyzer.analyze(failure)
                print(f"    Cause: {root_cause.description}")

                # Generate fixes
                print("    Generating fix candidates...")
                fixes = self.fix_generator.generate_fixes(root_cause)

                if not fixes:
                    print("    No fix candidates generated.")
                    continue

                for fix in fixes:
                    print(f"    Candidate: {fix}")

                    if dry_run:
                        print(f"    [DRY RUN] Would apply: {fix.description}")
                        if fix.diff:
                            print("    Diff:")
                            for line in fix.diff.split('\n')[:10]:
                                print(f"      {line}")
                        continue

                    if fix.confidence < 0.5:
                        print(f"    Skipping low-confidence fix ({fix.confidence:.2f})")
                        continue

                    # Apply fix
                    print("    Applying fix...")
                    target_file = self.project_root / "tests" / "parser_extracted.py"
                    mod_result = self.code_modifier.apply_fix(fix, target_file)

                    if not mod_result.success:
                        print(f"    Failed: {mod_result.message}")
                        continue

                    progress.fixes_applied += 1
                    print(f"    Applied successfully (branch: {mod_result.branch_name})")

                    # Verify fix
                    print("    Verifying fix...")
                    new_results = self.run_tests()

                    new_test_results = {t: True for t in new_results["passed"]}
                    for f in new_results["failures"]:
                        new_test_results[f["test_name"]] = False

                    new_state = self.regression_detector.record_state(new_test_results)

                    # Check for regression
                    regression = self.regression_detector.check_regression(
                        initial_state, new_state
                    )
                    print(f"    {regression.message}")

                    if regression.has_regression:
                        print("    Rolling back due to regression...")
                        self.code_modifier.rollback(mod_result.branch_name)
                        progress.fixes_rolled_back += 1
                        continue

                    # Check if we should stop
                    should_stop, reason = self.regression_detector.should_stop(
                        new_state, initial_state
                    )

                    if should_stop:
                        print(f"    Stopping: {reason}")
                        progress.current_failures = new_state.failed_count
                        self._record_history(progress, fixes)
                        return progress

                    # Merge successful fix
                    if mod_result.branch_name:
                        self.code_modifier.merge_to_main(mod_result.branch_name)

                    # Update for next iteration
                    results = new_results
                    progress.current_failures = new_state.failed_count

                    break  # Move to next failure

        self._record_history(progress, [])
        return progress

    def _record_history(
        self,
        progress: HealingProgress,
        fixes: List[FixCandidate]
    ) -> None:
        """Record healing session to history."""
        history = []
        if self.history_file.exists():
            with open(self.history_file) as f:
                history = json.load(f)

        entry = {
            "timestamp": datetime.now().isoformat(),
            "iterations": progress.iterations,
            "fixes_applied": progress.fixes_applied,
            "fixes_rolled_back": progress.fixes_rolled_back,
            "initial_failures": progress.initial_failures,
            "final_failures": progress.current_failures,
            "improvement": progress.improvement,
        }

        history.append(entry)

        # Keep last 100 entries
        history = history[-100:]

        with open(self.history_file, 'w') as f:
            json.dump(history, f, indent=2)

    def show_history(self) -> None:
        """Display healing history."""
        if not self.history_file.exists():
            print("No healing history found.")
            return

        with open(self.history_file) as f:
            history = json.load(f)

        print("=" * 60)
        print("HEALING HISTORY")
        print("=" * 60)

        for entry in history[-10:]:  # Show last 10
            print(f"\n{entry['timestamp']}")
            print(f"  Iterations: {entry['iterations']}")
            print(f"  Fixes: {entry['fixes_applied']} applied, "
                  f"{entry['fixes_rolled_back']} rolled back")
            print(f"  Failures: {entry['initial_failures']} -> "
                  f"{entry['final_failures']} "
                  f"({entry['improvement']:+d})")

    def capture_fixture(self) -> None:
        """Capture a new fixture from live /usage output."""
        print("Capturing live /usage output...")

        # Run cc_usage.sh to generate output
        result = subprocess.run(
            ["bash", str(self.project_root / "cc_usage.sh")],
            capture_output=True,
            text=True,
            timeout=60,
        )

        if result.returncode != 0:
            print(f"Failed to capture: {result.stderr}")
            return

        # Save to captured directory
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        fixture_path = self.project_root / "tests" / "fixtures" / "captured" / f"live_{timestamp}.txt"
        fixture_path.parent.mkdir(parents=True, exist_ok=True)

        fixture_path.write_text(result.stdout)
        print(f"Captured to: {fixture_path}")

        # Create placeholder expected.json
        expected_path = fixture_path.with_suffix('.expected.json')
        expected = {
            "session_percent": None,
            "week_percent": None,
            "session_reset_str": None,
            "week_reset_str": None,
            "note": "Fill in expected values after verification",
        }
        expected_path.write_text(json.dumps(expected, indent=2))
        print(f"Created expected template: {expected_path}")

    def generate_fixtures(self) -> None:
        """Generate synthetic edge case fixtures."""
        fixtures_dir = self.project_root / "tests" / "fixtures" / "generated"
        fixtures_dir.mkdir(parents=True, exist_ok=True)

        # Edge case templates
        templates = [
            {
                "name": "midnight_crossing",
                "content": """
Current session
██████████████████████████████░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░  42% used

Resets 12:59am

Current week (all models)
████████████████░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░  23% used

Resets Jan 29 at 12:59am
""",
                "expected": {
                    "session_percent": 42,
                    "week_percent": 23,
                    "session_reset_str": "12:59am",
                    "week_reset_str": "Jan 29 at 12:59am",
                },
            },
            {
                "name": "single_digit_time",
                "content": """
Current session
██████████████████████████████░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░  42% used

Resets 1pm

Current week (all models)
████████████████░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░  23% used

Resets Jan 29 at 1pm
""",
                "expected": {
                    "session_percent": 42,
                    "week_percent": 23,
                    "session_reset_str": "1pm",
                    "week_reset_str": "Jan 29 at 1pm",
                },
            },
            {
                "name": "100_percent",
                "content": """
Current session
█████████████████████████████████████████████████████████████████████  100% used

Resets 6:59pm

Current week (all models)
█████████████████████████████████████████████████████████████████████  100% used

Resets Jan 29 at 6:59pm
""",
                "expected": {
                    "session_percent": 100,
                    "week_percent": 100,
                    "session_reset_str": "6:59pm",
                    "week_reset_str": "Jan 29 at 6:59pm",
                },
            },
            {
                "name": "0_percent",
                "content": """
Current session
░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░  0% used

Resets 6:59pm

Current week (all models)
░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░  0% used

Resets Jan 29 at 6:59pm
""",
                "expected": {
                    "session_percent": 0,
                    "week_percent": 0,
                    "session_reset_str": "6:59pm",
                    "week_reset_str": "Jan 29 at 6:59pm",
                },
            },
        ]

        for template in templates:
            txt_path = fixtures_dir / f"{template['name']}.txt"
            json_path = fixtures_dir / f"{template['name']}.expected.json"

            txt_path.write_text(template["content"])
            json_path.write_text(json.dumps(template["expected"], indent=2))

            print(f"Generated: {template['name']}")

        print(f"\nGenerated {len(templates)} fixtures in {fixtures_dir}")


def main():
    parser = argparse.ArgumentParser(
        description="Self-Healing Test System for cc_usage.sh"
    )
    parser.add_argument(
        "--test-only",
        action="store_true",
        help="Run tests only, no healing",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show proposed fixes without applying",
    )
    parser.add_argument(
        "--capture",
        action="store_true",
        help="Capture new fixture from live /usage",
    )
    parser.add_argument(
        "--generate-fixtures",
        action="store_true",
        help="Generate synthetic edge case fixtures",
    )
    parser.add_argument(
        "--history",
        action="store_true",
        help="View healing history",
    )
    parser.add_argument(
        "--config",
        type=Path,
        help="Path to config file",
    )
    parser.add_argument(
        "--check-sync",
        action="store_true",
        help="Verify parser_extracted.py matches cc_usage.sh",
    )

    args = parser.parse_args()

    runner = SelfHealingRunner(config_path=args.config)

    if args.test_only:
        results = runner.run_tests()
        print(f"Tests: {len(results['passed'])} passed, "
              f"{len(results['failures'])} failed")
        if results["failures"]:
            print("\nFailures:")
            for f in results["failures"]:
                print(f"  - {f['test_name']}")
        sys.exit(results["return_code"])

    elif args.capture:
        runner.capture_fixture()

    elif args.generate_fixtures:
        runner.generate_fixtures()

    elif args.history:
        runner.show_history()

    elif args.check_sync:
        print("=" * 60)
        print("SYNC VERIFICATION")
        print("=" * 60)
        status = runner.verify_sync()
        print(f"\n{status}\n")
        if not status.in_sync:
            print("Functions that need syncing:")
            print("  parser_extracted.py must match the embedded Python in cc_usage.sh")
            sys.exit(1)
        sys.exit(0)

    else:
        # Full self-healing cycle
        progress = runner.heal(dry_run=args.dry_run)
        print("\n" + "=" * 60)
        print(progress.summary())
        print("=" * 60)

        if progress.current_failures > 0:
            sys.exit(1)


if __name__ == "__main__":
    main()
