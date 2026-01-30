"""
Fix generator for the self-healing test system.

Generates fix candidates based on root cause analysis.
"""

import re
from dataclasses import dataclass, field
from typing import Optional, List, Dict, Any
from pathlib import Path

from .analyzer import RootCause
from .classifier import FailureType


@dataclass
class FixCandidate:
    """A proposed fix for a failure."""

    root_cause: RootCause
    description: str
    diff: str  # Unified diff format
    confidence: float  # 0.0 to 1.0
    strategy: str  # e.g., "add_format", "broaden_regex"
    metadata: Dict[str, Any] = field(default_factory=dict)

    def __str__(self) -> str:
        return f"Fix({self.strategy}, conf={self.confidence:.2f}): {self.description}"


class FixGenerator:
    """
    Generates fix candidates for analyzed failures.

    Strategies by failure type:
    - DATE_FORMAT_NEW: Add format to appropriate list
    - REGEX_MISMATCH: Broaden pattern with alternations
    - VALIDATION_ERROR: Adjust thresholds
    - ANSI_CORRUPTION: Enhance strip regex
    """

    def __init__(
        self,
        parser_path: Optional[Path] = None,
        ai_client: Optional[Any] = None,
    ):
        """
        Initialize the fix generator.

        Args:
            parser_path: Path to parser_extracted.py
            ai_client: Anthropic client for AI-assisted fixes
        """
        self.parser_path = parser_path
        self.ai_client = ai_client
        self._parser_source: Optional[str] = None

        if parser_path and parser_path.exists():
            self._parser_source = parser_path.read_text()

    def generate_fixes(
        self,
        root_cause: RootCause,
        max_candidates: int = 3
    ) -> List[FixCandidate]:
        """
        Generate fix candidates for a root cause.

        Args:
            root_cause: The analyzed root cause
            max_candidates: Maximum number of candidates to generate

        Returns:
            List of FixCandidate objects, ordered by confidence
        """
        generators = {
            "add_date_format_with_year": self._fix_add_date_format_with_year,
            "add_date_format_no_year": self._fix_add_date_format_no_year,
            "add_time_format": self._fix_add_time_format,
            "broaden_regex": self._fix_broaden_regex,
            "adjust_validation": self._fix_adjust_validation,
            "enhance_ansi_regex": self._fix_enhance_ansi_regex,
            "fix_midnight_logic": self._fix_midnight_logic,
            "fix_year_wrap": self._fix_year_wrap,
            "ai_diagnose": self._fix_with_ai,
        }

        generator = generators.get(
            root_cause.suggested_fix_type,
            self._fix_with_ai
        )

        candidates = generator(root_cause)

        # Sort by confidence and limit
        candidates.sort(key=lambda c: c.confidence, reverse=True)
        return candidates[:max_candidates]

    def _fix_add_date_format_with_year(
        self,
        root_cause: RootCause
    ) -> List[FixCandidate]:
        """Add a new date format with year to the parser."""
        date_string = root_cause.context.get("date_string", "")
        inferred_format = root_cause.context.get("inferred_format")

        if not inferred_format:
            # Cannot infer, need AI
            return self._fix_with_ai(root_cause)

        # Generate diff to add format
        old_line = "DATE_FORMATS_WITH_YEAR = ["
        new_format_line = f"    '{inferred_format}',  # {date_string}"

        diff = f"""--- a/tests/parser_extracted.py
+++ b/tests/parser_extracted.py
@@ -X,X +X,X @@ DATE_FORMATS_WITH_YEAR = [
+{new_format_line}
"""

        return [FixCandidate(
            root_cause=root_cause,
            description=f"Add date format '{inferred_format}' for '{date_string}'",
            diff=diff,
            confidence=0.8,
            strategy="add_format",
            metadata={
                "format": inferred_format,
                "example": date_string,
                "list_name": "DATE_FORMATS_WITH_YEAR",
            },
        )]

    def _fix_add_date_format_no_year(
        self,
        root_cause: RootCause
    ) -> List[FixCandidate]:
        """Add a new date format without year to the parser."""
        date_string = root_cause.context.get("date_string", "")
        inferred_format = root_cause.context.get("inferred_format")

        if not inferred_format:
            return self._fix_with_ai(root_cause)

        new_format_line = f"    '{inferred_format}',  # {date_string}"

        diff = f"""--- a/tests/parser_extracted.py
+++ b/tests/parser_extracted.py
@@ -X,X +X,X @@ DATE_FORMATS_NO_YEAR = [
+{new_format_line}
"""

        return [FixCandidate(
            root_cause=root_cause,
            description=f"Add date format '{inferred_format}' for '{date_string}'",
            diff=diff,
            confidence=0.8,
            strategy="add_format",
            metadata={
                "format": inferred_format,
                "example": date_string,
                "list_name": "DATE_FORMATS_NO_YEAR",
            },
        )]

    def _fix_add_time_format(
        self,
        root_cause: RootCause
    ) -> List[FixCandidate]:
        """Add a new time-only format to the parser."""
        date_string = root_cause.context.get("date_string", "")
        inferred_format = root_cause.context.get("inferred_format")

        if not inferred_format:
            return self._fix_with_ai(root_cause)

        new_format_line = f"    '{inferred_format}',  # {date_string}"

        diff = f"""--- a/tests/parser_extracted.py
+++ b/tests/parser_extracted.py
@@ -X,X +X,X @@ TIME_FORMATS = [
+{new_format_line}
"""

        return [FixCandidate(
            root_cause=root_cause,
            description=f"Add time format '{inferred_format}' for '{date_string}'",
            diff=diff,
            confidence=0.8,
            strategy="add_format",
            metadata={
                "format": inferred_format,
                "example": date_string,
                "list_name": "TIME_FORMATS",
            },
        )]

    def _fix_broaden_regex(self, root_cause: RootCause) -> List[FixCandidate]:
        r"""
        Broaden a regex pattern to match more cases.

        Common strategies:
        - Change \\s+ to \\s*
        - Add alternations for common variations
        - Make optional groups
        """
        candidates = []

        # Strategy 1: Make whitespace optional
        if root_cause.context.get("is_session"):
            # Session regex - make whitespace more flexible
            candidates.append(FixCandidate(
                root_cause=root_cause,
                description="Make session regex whitespace more flexible",
                diff="""--- a/tests/parser_extracted.py
+++ b/tests/parser_extracted.py
@@ session_match pattern @@
-    r'Current\\s+session.*?(\\d+)%\\s*used.*?Rese[ts]*\\s*(.*?)(?:\\s{2,}|\\n|$)',
+    r'Current\\s+session.*?(\\d+)%\\s*used.*?Rese[ts]*\\s*(.*?)(?:\\s+|\\n|$)',
""",
                confidence=0.6,
                strategy="broaden_regex",
            ))

        if root_cause.context.get("is_week"):
            # Week regex - similar treatment
            candidates.append(FixCandidate(
                root_cause=root_cause,
                description="Make week regex whitespace more flexible",
                diff="""--- a/tests/parser_extracted.py
+++ b/tests/parser_extracted.py
@@ week_match pattern @@
-    r'Current\\s+week\\s+\\(all\\s+models\\).*?(\\d+)%\\s*used.*?Resets\\s*(.*?)(?:\\s{2,}|\\n|$)',
+    r'Current\\s+week\\s*\\(all\\s+models\\).*?(\\d+)%\\s*used.*?Resets\\s*(.*?)(?:\\s+|\\n|$)',
""",
                confidence=0.6,
                strategy="broaden_regex",
            ))

        # If no specific strategy, use AI
        if not candidates:
            return self._fix_with_ai(root_cause)

        return candidates

    def _fix_adjust_validation(
        self,
        root_cause: RootCause
    ) -> List[FixCandidate]:
        """Adjust validation thresholds."""
        candidates = []

        if root_cause.context.get("is_window_exceeded"):
            # Increase the buffer for window validation
            candidates.append(FixCandidate(
                root_cause=root_cause,
                description="Increase validation buffer for window check",
                diff="""--- a/tests/parser_extracted.py
+++ b/tests/parser_extracted.py
@@ validate_reset_time @@
-    max_hours = window_hours + 1  # Small buffer for timing
+    max_hours = window_hours + 2  # Increased buffer for timing
""",
                confidence=0.7,
                strategy="adjust_threshold",
            ))

        if root_cause.context.get("is_in_past"):
            # Increase tolerance for past times
            candidates.append(FixCandidate(
                root_cause=root_cause,
                description="Increase tolerance for past times",
                diff="""--- a/tests/parser_extracted.py
+++ b/tests/parser_extracted.py
@@ validate_reset_time @@
-    if remain_hours < -window_hours:
+    if remain_hours < -window_hours * 1.5:
""",
                confidence=0.6,
                strategy="adjust_threshold",
            ))

        if not candidates:
            return self._fix_with_ai(root_cause)

        return candidates

    def _fix_enhance_ansi_regex(
        self,
        root_cause: RootCause
    ) -> List[FixCandidate]:
        """Enhance ANSI stripping regex."""
        sequence = root_cause.context.get("detected_sequence", "")

        # Common enhancements
        candidates = [
            FixCandidate(
                root_cause=root_cause,
                description="Enhance ANSI regex to handle more sequences",
                diff="""--- a/tests/parser_extracted.py
+++ b/tests/parser_extracted.py
@@ strip_ansi @@
-    return re.sub(r'\\x1B(?:[@-Z\\\\-_]|\\[[0-?]*[ -/]*[@-~])', '', text)
+    return re.sub(r'\\x1B(?:[@-Z\\\\-_]|\\[[0-?]*[ -/]*[@-~]|\\].*?(?:\\x07|\\x1B\\\\))', '', text)
""",
                confidence=0.7,
                strategy="enhance_regex",
                metadata={"detected_sequence": sequence},
            ),
        ]

        return candidates

    def _fix_midnight_logic(self, root_cause: RootCause) -> List[FixCandidate]:
        """Fix midnight crossing logic."""
        return [FixCandidate(
            root_cause=root_cause,
            description="Adjust tomorrow logic buffer for midnight edge cases",
            diff="""--- a/tests/parser_extracted.py
+++ b/tests/parser_extracted.py
@@ tomorrow logic @@
-                if dt < now - timedelta(minutes=15):
+                if dt < now - timedelta(minutes=30):
""",
            confidence=0.65,
            strategy="fix_edge_case",
        )]

    def _fix_year_wrap(self, root_cause: RootCause) -> List[FixCandidate]:
        """Fix year wrap logic."""
        return [FixCandidate(
            root_cause=root_cause,
            description="Adjust year wrap threshold",
            diff="""--- a/tests/parser_extracted.py
+++ b/tests/parser_extracted.py
@@ year wrap @@
-                    if dt < now - timedelta(days=300):
+                    if dt < now - timedelta(days=330):
""",
            confidence=0.65,
            strategy="fix_edge_case",
        )]

    def _fix_with_ai(self, root_cause: RootCause) -> List[FixCandidate]:
        """
        Use AI to diagnose and generate a fix.

        This is the fallback for complex or unknown failures.
        """
        if not self.ai_client:
            # Return placeholder that indicates AI is needed
            return [FixCandidate(
                root_cause=root_cause,
                description="AI assistance required but not available",
                diff="",
                confidence=0.0,
                strategy="ai_required",
                metadata={"requires_ai": True},
            )]

        # Build prompt for AI
        prompt = self._build_ai_prompt(root_cause)

        try:
            response = self.ai_client.messages.create(
                model="claude-sonnet-4-20250514",
                max_tokens=2000,
                messages=[{"role": "user", "content": prompt}]
            )

            # Parse AI response
            return self._parse_ai_response(response.content[0].text, root_cause)

        except Exception as e:
            return [FixCandidate(
                root_cause=root_cause,
                description=f"AI fix generation failed: {e}",
                diff="",
                confidence=0.0,
                strategy="ai_failed",
                metadata={"error": str(e)},
            )]

    def _build_ai_prompt(self, root_cause: RootCause) -> str:
        """Build a prompt for AI-assisted fix generation."""
        failure = root_cause.failure

        # Get relevant source code
        source_context = ""
        if self._parser_source and root_cause.affected_lines:
            lines = self._parser_source.split('\n')
            start = max(0, min(root_cause.affected_lines) - 10)
            end = min(len(lines), max(root_cause.affected_lines) + 10)
            source_context = '\n'.join(
                f"{i+1:4d}: {lines[i]}" for i in range(start, end)
            )

        return f"""Analyze this parsing failure and provide a minimal fix.

## Failure Details
- Test: {failure.test_name}
- Type: {failure.failure_type.name}
- Exception: {failure.exception_type}: {failure.exception_message}

## Root Cause Analysis
- Description: {root_cause.description}
- Affected Function: {root_cause.affected_function}
- Suggested Fix Type: {root_cause.suggested_fix_type}

## Context
{root_cause.context}

## Relevant Source Code
```python
{source_context}
```

## Traceback
{failure.traceback[:1000]}

Please provide:
1. Brief root cause explanation (1-2 sentences)
2. A minimal unified diff patch to fix this issue

Format your response as:
CAUSE: <explanation>

DIFF:
```diff
<unified diff here>
```
"""

    def _parse_ai_response(
        self,
        response: str,
        root_cause: RootCause
    ) -> List[FixCandidate]:
        """Parse AI response to extract fix candidate."""
        # Extract cause
        cause_match = re.search(r'CAUSE:\s*(.+?)(?=\n\n|DIFF:)', response, re.DOTALL)
        cause = cause_match.group(1).strip() if cause_match else "AI-generated fix"

        # Extract diff
        diff_match = re.search(r'```diff\s*\n(.+?)```', response, re.DOTALL)
        diff = diff_match.group(1).strip() if diff_match else ""

        if diff:
            return [FixCandidate(
                root_cause=root_cause,
                description=cause,
                diff=diff,
                confidence=0.75,  # AI fixes get moderate confidence
                strategy="ai_generated",
                metadata={"ai_response": response[:500]},
            )]
        else:
            return [FixCandidate(
                root_cause=root_cause,
                description="AI could not generate a valid diff",
                diff="",
                confidence=0.0,
                strategy="ai_failed",
                metadata={"ai_response": response[:500]},
            )]
