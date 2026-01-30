"""
Self-healing test system for cc_usage.sh.

This package provides autonomous failure detection, diagnosis, and repair
for the cc_usage.sh parser. It can detect parsing failures, classify them,
generate fixes, and apply them automatically.
"""

from .classifier import FailureClassifier, FailureType
from .analyzer import RootCauseAnalyzer
from .fix_generator import FixGenerator, FixCandidate
from .code_modifier import CodeModifier
from .regression_detector import RegressionDetector

__all__ = [
    'FailureClassifier',
    'FailureType',
    'RootCauseAnalyzer',
    'FixGenerator',
    'FixCandidate',
    'CodeModifier',
    'RegressionDetector',
]
