"""
Sync verifier for the self-healing test system.

Ensures parser_extracted.py stays in sync with the embedded Python
code in cc_usage.sh. This prevents the critical bug where tests pass
against the extracted code but the actual script has different behavior.
"""

import re
import difflib
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, List, Tuple


@dataclass
class SyncStatus:
    """Result of sync verification."""

    in_sync: bool
    differences: List[str]
    extracted_line_count: int
    module_line_count: int
    message: str

    def __str__(self) -> str:
        if self.in_sync:
            return f"✓ In sync ({self.extracted_line_count} lines)"
        return f"✗ OUT OF SYNC: {self.message}\n" + "\n".join(self.differences[:20])


class SyncVerifier:
    """
    Verifies that parser_extracted.py matches the embedded Python in cc_usage.sh.

    The embedded Python code lives in a heredoc between:
        python3 - "$LOG_FILE" "$START_TIME" "$DEBUG" <<'END_PYTHON'
    and:
        END_PYTHON

    This verifier extracts that code and compares the core parsing functions
    to their counterparts in parser_extracted.py.
    """

    # Functions that must stay in sync between the two files
    SYNC_FUNCTIONS = [
        'strip_ansi',
        'clean_date_string',
        'parse_reset_time',
        'validate_reset_time',
    ]

    def __init__(
        self,
        script_path: Optional[Path] = None,
        module_path: Optional[Path] = None,
    ):
        """
        Initialize the sync verifier.

        Args:
            script_path: Path to cc_usage.sh
            module_path: Path to parser_extracted.py
        """
        project_root = Path(__file__).parent.parent.parent
        self.script_path = script_path or project_root / "cc_usage.sh"
        self.module_path = module_path or project_root / "tests" / "parser_extracted.py"

    def extract_embedded_python(self) -> Optional[str]:
        """
        Extract the Python code embedded in cc_usage.sh.

        Returns:
            The embedded Python code, or None if extraction fails
        """
        if not self.script_path.exists():
            return None

        content = self.script_path.read_text()

        # Find the heredoc boundaries
        # Pattern: python3 - ... <<'END_PYTHON' ... END_PYTHON
        match = re.search(
            r"python3\s+-.*?<<'END_PYTHON'\s*\n(.*?)\nEND_PYTHON",
            content,
            re.DOTALL
        )

        if not match:
            return None

        return match.group(1)

    def extract_function(self, source: str, func_name: str) -> Optional[str]:
        """
        Extract a function definition from Python source code.

        Args:
            source: Python source code
            func_name: Name of function to extract

        Returns:
            Function source code, or None if not found
        """
        # Pattern to match function definition and its body
        # This handles the indentation-based scope of Python
        pattern = rf'^(def {func_name}\s*\([^)]*\).*?)(?=\n(?:def |class |[A-Za-z_]|\Z))'

        match = re.search(pattern, source, re.MULTILINE | re.DOTALL)
        if match:
            return match.group(1).rstrip()

        return None

    def normalize_code(self, code: str) -> str:
        """
        Normalize code for comparison.

        Removes:
        - Comments
        - Blank lines
        - Trailing whitespace
        - Type hints (since embedded code may not have them)
        - Docstrings
        - Function signature differences (type annotations, extra params with defaults)
        """
        lines = []
        in_docstring = False
        docstring_delimiter = None

        for line in code.split('\n'):
            # Strip trailing whitespace
            line = line.rstrip()
            stripped = line.lstrip()

            # Handle docstrings
            if not in_docstring:
                if stripped.startswith('"""') or stripped.startswith("'''"):
                    docstring_delimiter = stripped[:3]
                    # Check if it's a single-line docstring
                    if stripped.count(docstring_delimiter) >= 2:
                        continue  # Skip single-line docstring
                    in_docstring = True
                    continue
            else:
                if docstring_delimiter in stripped:
                    in_docstring = False
                    docstring_delimiter = None
                continue

            # Skip blank lines
            if not stripped:
                continue

            # Skip comment-only lines
            if stripped.startswith('#'):
                continue

            # Remove inline comments
            if '#' in line and not stripped.startswith('#'):
                parts = line.split('#')
                if len(parts) > 1:
                    before_hash = parts[0]
                    if before_hash.count('"') % 2 == 0 and before_hash.count("'") % 2 == 0:
                        line = before_hash.rstrip()

            # Skip lines that are just type hint imports or type definitions
            if stripped.startswith('from typing import'):
                continue

            lines.append(line)

        result = '\n'.join(lines)

        # Remove type hints from function signatures
        # e.g., "def foo(x: str) -> int:" becomes "def foo(x):"
        result = re.sub(r': [A-Za-z\[\], ]+(?=[,\)])', '', result)
        result = re.sub(r' -> [A-Za-z\[\], ]+:', ':', result)

        # Remove Optional wrapper
        result = re.sub(r'Optional\[([^\]]+)\]', r'\1', result)

        return result

    def extract_function_body(self, func_source: str) -> str:
        """
        Extract just the body of a function, skipping signature and initial setup.

        This allows the module to have extra testability features (like 'now' param)
        while still verifying the core logic matches.
        """
        lines = func_source.split('\n')
        body_lines = []
        past_signature = False
        paren_depth = 0

        for line in lines:
            stripped = line.lstrip()

            # Track when we're past the function signature
            if not past_signature:
                # Count parentheses to handle multi-line signatures
                if 'def ' in stripped:
                    paren_depth = stripped.count('(') - stripped.count(')')

                    # Single-line signature
                    if paren_depth == 0 and ':' in stripped:
                        past_signature = True
                    continue
                else:
                    # We're in a multi-line signature
                    paren_depth += stripped.count('(') - stripped.count(')')

                    # Check if signature ends on this line
                    if paren_depth <= 0 and '):' in stripped or ') ->' in stripped:
                        past_signature = True
                    continue

            # Skip the "if now is None: now = ..." setup that's added for testing
            if 'if now is None' in stripped:
                continue
            if stripped == 'now = datetime.datetime.now()':
                continue

            body_lines.append(line)

        return '\n'.join(body_lines)

    def compare_functions(
        self,
        embedded_source: str,
        module_source: str,
        func_name: str
    ) -> Tuple[bool, List[str]]:
        """
        Compare a function between embedded and module versions.

        The comparison focuses on CORE LOGIC, allowing the module to have:
        - Type hints
        - Docstrings
        - Extra parameters with defaults (like 'now' for testing)
        - Setup code for testability (like 'if now is None')

        Args:
            embedded_source: Python code from cc_usage.sh
            module_source: Python code from parser_extracted.py
            func_name: Function to compare

        Returns:
            Tuple of (is_same, diff_lines)
        """
        embedded_func = self.extract_function(embedded_source, func_name)
        module_func = self.extract_function(module_source, func_name)

        if embedded_func is None:
            return False, [f"Function '{func_name}' not found in cc_usage.sh"]

        if module_func is None:
            return False, [f"Function '{func_name}' not found in parser_extracted.py"]

        # Extract and normalize function bodies for comparison
        embedded_body = self.extract_function_body(embedded_func)
        module_body = self.extract_function_body(module_func)

        embedded_norm = self.normalize_code(embedded_body)
        module_norm = self.normalize_code(module_body)

        if embedded_norm == module_norm:
            return True, []

        # Generate diff
        diff = list(difflib.unified_diff(
            embedded_norm.split('\n'),
            module_norm.split('\n'),
            fromfile=f'cc_usage.sh:{func_name}',
            tofile=f'parser_extracted.py:{func_name}',
            lineterm=''
        ))

        return False, diff

    def verify(self) -> SyncStatus:
        """
        Verify that parser_extracted.py is in sync with cc_usage.sh.

        Returns:
            SyncStatus with detailed results
        """
        # Extract embedded Python
        embedded = self.extract_embedded_python()
        if embedded is None:
            return SyncStatus(
                in_sync=False,
                differences=[],
                extracted_line_count=0,
                module_line_count=0,
                message="Could not extract Python from cc_usage.sh"
            )

        # Read module
        if not self.module_path.exists():
            return SyncStatus(
                in_sync=False,
                differences=[],
                extracted_line_count=len(embedded.split('\n')),
                module_line_count=0,
                message="parser_extracted.py not found"
            )

        module_source = self.module_path.read_text()

        # Compare each function
        all_differences = []
        out_of_sync_funcs = []

        for func_name in self.SYNC_FUNCTIONS:
            is_same, diff = self.compare_functions(embedded, module_source, func_name)
            if not is_same:
                out_of_sync_funcs.append(func_name)
                all_differences.extend(diff)
                all_differences.append('')  # Blank line between diffs

        if out_of_sync_funcs:
            return SyncStatus(
                in_sync=False,
                differences=all_differences,
                extracted_line_count=len(embedded.split('\n')),
                module_line_count=len(module_source.split('\n')),
                message=f"Functions out of sync: {', '.join(out_of_sync_funcs)}"
            )

        return SyncStatus(
            in_sync=True,
            differences=[],
            extracted_line_count=len(embedded.split('\n')),
            module_line_count=len(module_source.split('\n')),
            message="All functions in sync"
        )

    def auto_sync(self, dry_run: bool = False) -> Tuple[bool, str]:
        """
        Automatically sync parser_extracted.py from cc_usage.sh.

        This extracts the embedded Python and updates the corresponding
        functions in parser_extracted.py while preserving the module's
        additional functionality (type hints, dataclasses, etc.).

        Args:
            dry_run: If True, show what would change without modifying

        Returns:
            Tuple of (success, message)
        """
        embedded = self.extract_embedded_python()
        if embedded is None:
            return False, "Could not extract Python from cc_usage.sh"

        if not self.module_path.exists():
            return False, "parser_extracted.py not found"

        module_source = self.module_path.read_text()
        updated_source = module_source

        changes = []
        for func_name in self.SYNC_FUNCTIONS:
            embedded_func = self.extract_function(embedded, func_name)
            if embedded_func is None:
                continue

            # For the module, we need to preserve type hints and docstrings
            # So we only sync the function BODY, not the signature
            # This is a simplified approach - in practice you might want
            # more sophisticated merging

            module_func = self.extract_function(module_source, func_name)
            if module_func is None:
                continue

            # Check if they differ (using normalized comparison)
            embedded_norm = self.normalize_code(embedded_func)
            module_norm = self.normalize_code(module_func)

            if embedded_norm != module_norm:
                changes.append(func_name)
                # For now, just flag it - full auto-sync would need
                # more careful implementation to preserve type hints

        if not changes:
            return True, "Already in sync"

        if dry_run:
            return True, f"Would sync functions: {', '.join(changes)}"

        # For actual sync, we'd need more sophisticated code merging
        # For now, return a message about what needs manual sync
        return False, f"Functions need manual sync: {', '.join(changes)}"


def verify_sync() -> SyncStatus:
    """Convenience function for quick sync verification."""
    return SyncVerifier().verify()
