"""
Regression detector for the self-healing test system.

Ensures fixes don't break existing functionality and prevents infinite loops.
"""

import hashlib
import json
from dataclasses import dataclass, field
from typing import Dict, List, Set, Optional
from pathlib import Path
from datetime import datetime


@dataclass
class TestState:
    """State of all tests at a point in time."""

    results: Dict[str, bool]  # test_name -> passed
    timestamp: datetime = field(default_factory=datetime.now)

    @property
    def state_hash(self) -> str:
        """Generate a hash of the test state for comparison."""
        # Sort keys for consistent hashing
        sorted_results = sorted(self.results.items())
        state_str = json.dumps(sorted_results)
        return hashlib.md5(state_str.encode()).hexdigest()[:16]

    @property
    def passed_count(self) -> int:
        return sum(1 for v in self.results.values() if v)

    @property
    def failed_count(self) -> int:
        return sum(1 for v in self.results.values() if not v)

    @property
    def total_count(self) -> int:
        return len(self.results)

    def diff(self, other: 'TestState') -> Dict[str, tuple]:
        """
        Compare to another state and return differences.

        Returns:
            Dict of test_name -> (old_result, new_result)
        """
        changes = {}
        all_tests = set(self.results.keys()) | set(other.results.keys())

        for test in all_tests:
            old = self.results.get(test)
            new = other.results.get(test)
            if old != new:
                changes[test] = (old, new)

        return changes


@dataclass
class RegressionReport:
    """Report on regressions caused by a fix."""

    has_regression: bool
    newly_failing: List[str]  # Tests that were passing, now failing
    newly_passing: List[str]  # Tests that were failing, now passing
    net_change: int  # Positive = improvement, negative = regression
    message: str


class RegressionDetector:
    """
    Detects regressions and prevents oscillation in the healing loop.

    Features:
    - Compares test states before/after fixes
    - Detects oscillation (same state seen twice)
    - Tracks net progress
    - Provides detailed regression reports
    """

    def __init__(
        self,
        oscillation_window: int = 10,
        require_net_progress: bool = True,
    ):
        """
        Initialize the regression detector.

        Args:
            oscillation_window: Number of states to track for oscillation
            require_net_progress: Stop if no net improvement
        """
        self.oscillation_window = oscillation_window
        self.require_net_progress = require_net_progress
        self._state_history: List[TestState] = []
        self._hash_set: Set[str] = set()

    def record_state(self, results: Dict[str, bool]) -> TestState:
        """
        Record a test state.

        Args:
            results: Dict of test_name -> passed

        Returns:
            The recorded TestState
        """
        state = TestState(results=results.copy())
        self._state_history.append(state)

        # Keep only the window
        if len(self._state_history) > self.oscillation_window:
            old_state = self._state_history.pop(0)
            self._hash_set.discard(old_state.state_hash)

        self._hash_set.add(state.state_hash)

        return state

    def check_regression(
        self,
        before: TestState,
        after: TestState,
    ) -> RegressionReport:
        """
        Check for regressions between two states.

        Args:
            before: State before fix
            after: State after fix

        Returns:
            RegressionReport with details
        """
        diff = before.diff(after)

        newly_failing = []
        newly_passing = []

        for test, (old, new) in diff.items():
            if old is True and new is False:
                newly_failing.append(test)
            elif old is False and new is True:
                newly_passing.append(test)

        net_change = len(newly_passing) - len(newly_failing)
        has_regression = len(newly_failing) > 0

        if has_regression:
            message = f"REGRESSION: {len(newly_failing)} tests now failing"
        elif net_change > 0:
            message = f"IMPROVEMENT: {net_change} more tests passing"
        elif net_change == 0:
            message = "NO CHANGE: Same number of tests passing/failing"
        else:
            message = f"NET REGRESSION: {abs(net_change)} fewer tests passing"

        return RegressionReport(
            has_regression=has_regression,
            newly_failing=newly_failing,
            newly_passing=newly_passing,
            net_change=net_change,
            message=message,
        )

    def detect_oscillation(self, state: Optional[TestState] = None) -> bool:
        """
        Detect if we're oscillating (same state seen twice).

        Args:
            state: Optional state to check (uses latest if not provided)

        Returns:
            True if oscillation detected
        """
        if state:
            target_hash = state.state_hash
        elif self._state_history:
            target_hash = self._state_history[-1].state_hash
        else:
            return False

        # Check if this hash has been seen before (excluding current)
        count = sum(
            1 for s in self._state_history[:-1]
            if s.state_hash == target_hash
        )

        return count > 0

    def should_stop(
        self,
        current_state: TestState,
        initial_state: TestState,
    ) -> tuple:
        """
        Determine if the healing loop should stop.

        Args:
            current_state: Current test state
            initial_state: State at start of healing

        Returns:
            Tuple of (should_stop, reason)
        """
        # Check for oscillation
        if self.detect_oscillation(current_state):
            return True, "Oscillation detected - same state seen twice"

        # Check for net progress
        if self.require_net_progress:
            regression_report = self.check_regression(initial_state, current_state)
            if regression_report.net_change < 0:
                return True, f"No net progress - {abs(regression_report.net_change)} more failures than start"

        # Check if all tests pass
        if current_state.failed_count == 0:
            return True, "All tests passing - healing complete"

        return False, ""

    def get_history_summary(self) -> str:
        """Get a summary of the state history."""
        if not self._state_history:
            return "No history recorded"

        lines = ["State History:"]
        for i, state in enumerate(self._state_history):
            lines.append(
                f"  {i+1}. [{state.state_hash}] "
                f"{state.passed_count}/{state.total_count} passed "
                f"({state.timestamp.strftime('%H:%M:%S')})"
            )

        return '\n'.join(lines)

    def reset(self) -> None:
        """Reset the detector state."""
        self._state_history.clear()
        self._hash_set.clear()


@dataclass
class HealingProgress:
    """Tracks overall progress of a healing session."""

    iterations: int = 0
    fixes_applied: int = 0
    fixes_rolled_back: int = 0
    initial_failures: int = 0
    current_failures: int = 0

    @property
    def improvement(self) -> int:
        """Net improvement in test count."""
        return self.initial_failures - self.current_failures

    def summary(self) -> str:
        """Generate a progress summary."""
        return (
            f"Healing Progress:\n"
            f"  Iterations: {self.iterations}\n"
            f"  Fixes Applied: {self.fixes_applied}\n"
            f"  Fixes Rolled Back: {self.fixes_rolled_back}\n"
            f"  Initial Failures: {self.initial_failures}\n"
            f"  Current Failures: {self.current_failures}\n"
            f"  Net Improvement: {self.improvement} tests fixed"
        )
