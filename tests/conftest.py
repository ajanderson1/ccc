"""
Pytest configuration and fixtures for cc_usage.sh self-healing tests.
"""

import json
import pytest
from pathlib import Path
from typing import Dict, Any, List, Optional
from dataclasses import dataclass
import datetime


# Project paths
PROJECT_ROOT = Path(__file__).parent.parent
FIXTURES_DIR = PROJECT_ROOT / "tests" / "fixtures"
GOLDEN_DIR = FIXTURES_DIR / "golden"
CAPTURED_DIR = FIXTURES_DIR / "captured"
GENERATED_DIR = FIXTURES_DIR / "generated"
QUARANTINE_DIR = FIXTURES_DIR / "quarantine"


@dataclass
class TestFixture:
    """A test fixture with input and expected output."""
    name: str
    content: str
    expected: Dict[str, Any]
    source: str  # "golden", "captured", "generated", "quarantine"
    path: Path

    @property
    def expected_session_percent(self) -> Optional[int]:
        return self.expected.get("session_percent")

    @property
    def expected_week_percent(self) -> Optional[int]:
        return self.expected.get("week_percent")

    @property
    def expected_session_reset(self) -> Optional[str]:
        return self.expected.get("session_reset_str")

    @property
    def expected_week_reset(self) -> Optional[str]:
        return self.expected.get("week_reset_str")

    @property
    def should_fail(self) -> bool:
        """Whether this fixture is expected to fail parsing."""
        return self.expected.get("should_fail", False)


def load_fixture(txt_path: Path) -> Optional[TestFixture]:
    """Load a fixture from a .txt file and its corresponding .expected.json."""
    if not txt_path.exists():
        return None

    json_path = txt_path.with_suffix('.expected.json')
    if not json_path.exists():
        # No expected output defined
        return None

    with open(txt_path, 'r', encoding='utf-8', errors='ignore') as f:
        content = f.read()

    with open(json_path, 'r') as f:
        expected = json.load(f)

    # Determine source from path
    source = "unknown"
    if GOLDEN_DIR in txt_path.parents or txt_path.parent == GOLDEN_DIR:
        source = "golden"
    elif CAPTURED_DIR in txt_path.parents or txt_path.parent == CAPTURED_DIR:
        source = "captured"
    elif GENERATED_DIR in txt_path.parents or txt_path.parent == GENERATED_DIR:
        source = "generated"
    elif QUARANTINE_DIR in txt_path.parents or txt_path.parent == QUARANTINE_DIR:
        source = "quarantine"

    return TestFixture(
        name=txt_path.stem,
        content=content,
        expected=expected,
        source=source,
        path=txt_path
    )


def discover_fixtures(include_quarantine: bool = False) -> List[TestFixture]:
    """Discover all test fixtures."""
    fixtures = []

    dirs = [GOLDEN_DIR, CAPTURED_DIR, GENERATED_DIR]
    if include_quarantine:
        dirs.append(QUARANTINE_DIR)

    for fixtures_dir in dirs:
        if not fixtures_dir.exists():
            continue
        for txt_path in fixtures_dir.glob("*.txt"):
            fixture = load_fixture(txt_path)
            if fixture:
                fixtures.append(fixture)

    return fixtures


@pytest.fixture
def all_fixtures() -> List[TestFixture]:
    """All discovered test fixtures (excluding quarantine)."""
    return discover_fixtures(include_quarantine=False)


@pytest.fixture
def golden_fixtures() -> List[TestFixture]:
    """Only golden (known-good baseline) fixtures."""
    return [f for f in discover_fixtures() if f.source == "golden"]


@pytest.fixture(params=discover_fixtures())
def fixture(request) -> TestFixture:
    """Parameterized fixture for running tests against all fixtures."""
    return request.param


@pytest.fixture
def mock_now():
    """
    Factory fixture to create a mock datetime.now() for testing.

    Usage:
        def test_something(mock_now):
            now = mock_now(2026, 1, 28, 14, 30)  # Jan 28, 2026 at 2:30pm
    """
    def _mock_now(year=2026, month=1, day=28, hour=12, minute=0, second=0):
        return datetime.datetime(year, month, day, hour, minute, second)
    return _mock_now


@pytest.fixture
def sample_usage_output():
    """Sample usage output for testing."""
    return """
Current session
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
██████████████████████████████░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░  42% used

Resets 6:59pm

Current week (all models)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
████████████████░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░  23% used

Resets Jan 29 at 6:59pm

Sonnet only
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
██████████████████████░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░  30% used
"""


# Custom pytest hooks for JSON reporting
def pytest_configure(config):
    """Configure custom markers."""
    config.addinivalue_line(
        "markers", "self_heal: mark test as part of self-healing system"
    )


def pytest_collection_modifyitems(config, items):
    """Skip quarantine fixtures unless explicitly requested."""
    if not config.getoption("--include-quarantine", default=False):
        skip_quarantine = pytest.mark.skip(reason="Fixture in quarantine")
        for item in items:
            if "quarantine" in str(item.fspath):
                item.add_marker(skip_quarantine)


def pytest_addoption(parser):
    """Add custom command line options."""
    parser.addoption(
        "--include-quarantine",
        action="store_true",
        default=False,
        help="Include quarantined fixtures in test run"
    )
    parser.addoption(
        "--self-heal",
        action="store_true",
        default=False,
        help="Enable self-healing mode (auto-fix failures)"
    )
