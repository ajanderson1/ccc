"""
Failure classifier for the self-healing test system.

Categorizes test failures to guide fix generation strategies.
"""

import re
from enum import Enum, auto
from dataclasses import dataclass
from typing import Optional, Dict, Any, List


class FailureType(Enum):
    """Categories of parsing failures."""

    REGEX_MISMATCH = auto()      # Pattern doesn't match expected content
    DATE_FORMAT_NEW = auto()     # Unknown date/time format encountered
    VALIDATION_ERROR = auto()    # Parsed value fails validation
    ANSI_CORRUPTION = auto()     # ANSI stripping failed
    EDGE_CASE = auto()           # Boundary condition (midnight, year wrap)
    UNKNOWN = auto()             # Cannot classify


@dataclass
class ClassifiedFailure:
    """A classified test failure with metadata."""

    failure_type: FailureType
    test_name: str
    fixture_name: Optional[str]
    exception_type: str
    exception_message: str
    traceback: str
    confidence: float  # 0.0 to 1.0
    evidence: Dict[str, Any]  # Supporting data for classification


class FailureClassifier:
    """
    Classifies test failures by analyzing exception details.

    Uses pattern matching on exception types and messages to determine
    the most likely failure category.
    """

    # Patterns for each failure type
    PATTERNS = {
        FailureType.REGEX_MISMATCH: [
            (r"AttributeError.*'NoneType'.*'group'", 0.95),
            (r"no match found", 0.90),
            (r"pattern.*not found", 0.85),
            (r"regex.*failed", 0.80),
            (r"session_match.*None", 0.90),
            (r"week_match.*None", 0.90),
        ],
        FailureType.DATE_FORMAT_NEW: [
            (r"ValueError.*time data.*does not match format", 0.95),
            (r"strptime.*ValueError", 0.90),
            (r"Failed to parse.*time", 0.85),
            (r"unconverted data remains", 0.90),
            (r"invalid date format", 0.85),
        ],
        FailureType.VALIDATION_ERROR: [
            (r"exceeds.*window", 0.95),
            (r"in past", 0.90),
            (r"Invalid.*reset time", 0.90),
            (r"validation.*failed", 0.85),
            (r"out of range", 0.80),
        ],
        FailureType.ANSI_CORRUPTION: [
            (r"\\x1[bB]", 0.95),
            (r"\[0-9;]*m", 0.85),
            (r"escape sequence", 0.80),
            (r"ANSI.*not stripped", 0.90),
            (r"control character", 0.80),
        ],
        FailureType.EDGE_CASE: [
            (r"midnight", 0.85),
            (r"year.*wrap", 0.90),
            (r"boundary", 0.80),
            (r"12:00am", 0.80),
            (r"Dec.*Jan|Jan.*Dec", 0.85),
        ],
    }

    def __init__(self):
        """Initialize the classifier."""
        self._compiled_patterns: Dict[FailureType, List[tuple]] = {}
        for ftype, patterns in self.PATTERNS.items():
            self._compiled_patterns[ftype] = [
                (re.compile(p, re.IGNORECASE), conf) for p, conf in patterns
            ]

    def classify(
        self,
        test_name: str,
        exception_type: str,
        exception_message: str,
        traceback: str,
        fixture_name: Optional[str] = None,
        fixture_content: Optional[str] = None,
    ) -> ClassifiedFailure:
        """
        Classify a test failure.

        Args:
            test_name: Name of the failing test
            exception_type: Type of exception (e.g., "ValueError")
            exception_message: Exception message
            traceback: Full traceback string
            fixture_name: Name of the test fixture (if applicable)
            fixture_content: Content of the fixture (for additional analysis)

        Returns:
            ClassifiedFailure with type and metadata
        """
        # Combine text for pattern matching
        full_text = f"{exception_type} {exception_message} {traceback}"

        best_match: Optional[FailureType] = None
        best_confidence = 0.0
        evidence: Dict[str, Any] = {
            "matched_patterns": [],
        }

        # Check patterns for each failure type
        for ftype, patterns in self._compiled_patterns.items():
            for pattern, confidence in patterns:
                if pattern.search(full_text):
                    evidence["matched_patterns"].append({
                        "type": ftype.name,
                        "pattern": pattern.pattern,
                        "confidence": confidence,
                    })
                    if confidence > best_confidence:
                        best_confidence = confidence
                        best_match = ftype

        # Additional heuristics based on exception type
        if not best_match or best_confidence < 0.7:
            type_confidence = self._classify_by_exception_type(exception_type)
            if type_confidence[1] > best_confidence:
                best_match = type_confidence[0]
                best_confidence = type_confidence[1]
                evidence["exception_type_match"] = exception_type

        # Check fixture content for ANSI corruption
        if fixture_content and best_confidence < 0.8:
            if '\x1b' in fixture_content or '\033' in fixture_content:
                # ANSI sequences in fixture - might be corruption
                evidence["ansi_in_fixture"] = True
                if best_confidence < 0.7:
                    best_match = FailureType.ANSI_CORRUPTION
                    best_confidence = 0.75

        # Default to UNKNOWN if no good match
        if not best_match or best_confidence < 0.5:
            best_match = FailureType.UNKNOWN
            best_confidence = 0.0

        return ClassifiedFailure(
            failure_type=best_match,
            test_name=test_name,
            fixture_name=fixture_name,
            exception_type=exception_type,
            exception_message=exception_message,
            traceback=traceback,
            confidence=best_confidence,
            evidence=evidence,
        )

    def _classify_by_exception_type(self, exception_type: str) -> tuple:
        """
        Fallback classification based on exception type alone.

        Returns:
            Tuple of (FailureType, confidence)
        """
        type_mapping = {
            "AttributeError": (FailureType.REGEX_MISMATCH, 0.6),
            "ValueError": (FailureType.DATE_FORMAT_NEW, 0.5),
            "KeyError": (FailureType.REGEX_MISMATCH, 0.4),
            "IndexError": (FailureType.REGEX_MISMATCH, 0.4),
            "TypeError": (FailureType.UNKNOWN, 0.3),
        }

        for exc_type, result in type_mapping.items():
            if exc_type in exception_type:
                return result

        return (FailureType.UNKNOWN, 0.0)

    def classify_batch(
        self,
        failures: List[Dict[str, Any]]
    ) -> List[ClassifiedFailure]:
        """
        Classify multiple failures at once.

        Args:
            failures: List of dicts with keys: test_name, exception_type,
                     exception_message, traceback, fixture_name (optional)

        Returns:
            List of ClassifiedFailure objects
        """
        return [
            self.classify(
                test_name=f["test_name"],
                exception_type=f["exception_type"],
                exception_message=f["exception_message"],
                traceback=f["traceback"],
                fixture_name=f.get("fixture_name"),
                fixture_content=f.get("fixture_content"),
            )
            for f in failures
        ]
