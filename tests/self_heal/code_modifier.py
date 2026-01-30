"""
Code modifier for the self-healing test system.

Safely applies fixes with git branching, syntax validation, and rollback.
"""

import re
import subprocess
import tempfile
import datetime
from dataclasses import dataclass
from typing import Optional, List, Tuple
from pathlib import Path

from .fix_generator import FixCandidate


@dataclass
class ModificationResult:
    """Result of a code modification attempt."""

    success: bool
    message: str
    branch_name: Optional[str] = None
    commit_hash: Optional[str] = None
    rollback_patch: Optional[str] = None
    lines_changed: int = 0


class CodeModifier:
    """
    Safely applies code modifications with full rollback capability.

    Safety mechanisms:
    1. Create git branch for each fix attempt
    2. Validate Python syntax before committing
    3. Maximum lines changed per fix (configurable)
    4. Save rollback patches
    """

    def __init__(
        self,
        project_root: Path,
        max_lines_changed: int = 50,
        auto_commit: bool = True,
        rollback_dir: Optional[Path] = None,
    ):
        """
        Initialize the code modifier.

        Args:
            project_root: Root directory of the project
            max_lines_changed: Maximum lines a single fix can change
            auto_commit: Whether to automatically commit changes
            rollback_dir: Directory to store rollback patches
        """
        self.project_root = project_root
        self.max_lines_changed = max_lines_changed
        self.auto_commit = auto_commit
        self.rollback_dir = rollback_dir or project_root / ".self_heal" / "rollback"
        self.rollback_dir.mkdir(parents=True, exist_ok=True)

    def apply_fix(
        self,
        fix: FixCandidate,
        target_file: Path,
    ) -> ModificationResult:
        """
        Apply a fix candidate to the target file.

        Args:
            fix: The fix candidate to apply
            target_file: Path to the file to modify

        Returns:
            ModificationResult with success/failure info
        """
        # Validate fix has a diff
        if not fix.diff or fix.diff.strip() == "":
            return ModificationResult(
                success=False,
                message="Fix has no diff to apply",
            )

        # Check lines changed
        lines_changed = self._count_diff_lines(fix.diff)
        if lines_changed > self.max_lines_changed:
            return ModificationResult(
                success=False,
                message=f"Fix changes {lines_changed} lines, exceeds max {self.max_lines_changed}",
                lines_changed=lines_changed,
            )

        # Create branch
        branch_name = self._create_branch(fix)
        if not branch_name:
            return ModificationResult(
                success=False,
                message="Failed to create git branch",
            )

        try:
            # Read current file content
            if not target_file.exists():
                return ModificationResult(
                    success=False,
                    message=f"Target file not found: {target_file}",
                    branch_name=branch_name,
                )

            original_content = target_file.read_text()

            # Apply the diff
            new_content = self._apply_diff(original_content, fix)
            if new_content is None:
                self._abort_branch(branch_name)
                return ModificationResult(
                    success=False,
                    message="Failed to apply diff - pattern not found",
                    branch_name=branch_name,
                )

            # Validate Python syntax
            syntax_valid, syntax_error = self._validate_python_syntax(new_content)
            if not syntax_valid:
                self._abort_branch(branch_name)
                return ModificationResult(
                    success=False,
                    message=f"Syntax validation failed: {syntax_error}",
                    branch_name=branch_name,
                )

            # Save rollback patch
            rollback_patch = self._save_rollback(
                target_file,
                original_content,
                branch_name,
            )

            # Write the new content
            target_file.write_text(new_content)

            # Commit if auto_commit is enabled
            commit_hash = None
            if self.auto_commit:
                commit_hash = self._commit_changes(target_file, fix)

            return ModificationResult(
                success=True,
                message=f"Fix applied successfully",
                branch_name=branch_name,
                commit_hash=commit_hash,
                rollback_patch=rollback_patch,
                lines_changed=lines_changed,
            )

        except Exception as e:
            self._abort_branch(branch_name)
            return ModificationResult(
                success=False,
                message=f"Exception during fix application: {e}",
                branch_name=branch_name,
            )

    def rollback(self, branch_name: str) -> bool:
        """
        Rollback changes made by a fix.

        Args:
            branch_name: Name of the branch to rollback

        Returns:
            True if rollback succeeded
        """
        try:
            # Get the original branch
            result = subprocess.run(
                ["git", "rev-parse", "--abbrev-ref", "HEAD"],
                capture_output=True,
                text=True,
                cwd=self.project_root,
            )
            current_branch = result.stdout.strip()

            if current_branch == branch_name:
                # We're on the branch to rollback, go back to main
                subprocess.run(
                    ["git", "checkout", "main"],
                    capture_output=True,
                    cwd=self.project_root,
                )

            # Delete the branch
            subprocess.run(
                ["git", "branch", "-D", branch_name],
                capture_output=True,
                cwd=self.project_root,
            )

            return True

        except Exception:
            return False

    def _count_diff_lines(self, diff: str) -> int:
        """Count the number of lines changed in a diff."""
        added = len(re.findall(r'^\+[^+]', diff, re.MULTILINE))
        removed = len(re.findall(r'^-[^-]', diff, re.MULTILINE))
        return added + removed

    def _create_branch(self, fix: FixCandidate) -> Optional[str]:
        """Create a git branch for the fix."""
        timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        branch_name = f"self-heal/{fix.strategy}/{timestamp}"

        try:
            # First ensure we're on a clean state
            result = subprocess.run(
                ["git", "checkout", "-b", branch_name],
                capture_output=True,
                text=True,
                cwd=self.project_root,
            )

            if result.returncode != 0:
                return None

            return branch_name

        except Exception:
            return None

    def _abort_branch(self, branch_name: str) -> None:
        """Abort and delete a branch."""
        try:
            # Go back to main
            subprocess.run(
                ["git", "checkout", "main"],
                capture_output=True,
                cwd=self.project_root,
            )
            # Delete the branch
            subprocess.run(
                ["git", "branch", "-D", branch_name],
                capture_output=True,
                cwd=self.project_root,
            )
        except Exception:
            pass

    def _apply_diff(
        self,
        original: str,
        fix: FixCandidate
    ) -> Optional[str]:
        """
        Apply a diff to the original content.

        For simple diffs in the fix generator's format, we use
        pattern-based replacement rather than full diff application.
        """
        diff = fix.diff

        # Handle metadata-based fixes (format additions)
        if fix.metadata.get("list_name"):
            return self._apply_format_addition(original, fix)

        # Try to extract old/new patterns from diff
        old_pattern = None
        new_pattern = None

        # Look for -/+ lines in the diff
        for line in diff.split('\n'):
            if line.startswith('-') and not line.startswith('---'):
                old_pattern = line[1:].strip()
            elif line.startswith('+') and not line.startswith('+++'):
                new_pattern = line[1:].strip()

        if old_pattern and new_pattern:
            if old_pattern in original:
                return original.replace(old_pattern, new_pattern, 1)

        # If we can't parse the diff, return None
        return None

    def _apply_format_addition(
        self,
        original: str,
        fix: FixCandidate
    ) -> Optional[str]:
        """Apply a format list addition fix."""
        list_name = fix.metadata.get("list_name")
        new_format = fix.metadata.get("format")
        example = fix.metadata.get("example", "")

        if not list_name or not new_format:
            return None

        # Find the list and add the new format
        pattern = rf"({list_name}\s*=\s*\[)"
        match = re.search(pattern, original)

        if not match:
            return None

        # Add the new format after the opening bracket
        insert_pos = match.end()
        new_line = f"\n    '{new_format}',  # {example}"

        return original[:insert_pos] + new_line + original[insert_pos:]

    def _validate_python_syntax(self, content: str) -> Tuple[bool, Optional[str]]:
        """
        Validate Python syntax of the content.

        Returns:
            Tuple of (is_valid, error_message)
        """
        try:
            compile(content, "<string>", "exec")
            return True, None
        except SyntaxError as e:
            return False, f"Line {e.lineno}: {e.msg}"

    def _save_rollback(
        self,
        target_file: Path,
        original_content: str,
        branch_name: str,
    ) -> str:
        """Save a rollback patch."""
        timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        patch_name = f"rollback_{timestamp}.patch"
        patch_path = self.rollback_dir / patch_name

        # Save the original content
        patch_path.write_text(original_content)

        return str(patch_path)

    def _commit_changes(
        self,
        target_file: Path,
        fix: FixCandidate,
    ) -> Optional[str]:
        """Commit the changes with a descriptive message."""
        try:
            # Stage the file
            subprocess.run(
                ["git", "add", str(target_file)],
                capture_output=True,
                cwd=self.project_root,
            )

            # Create commit message
            message = f"""[self-heal] {fix.strategy}: {fix.description}

Failure: {fix.root_cause.failure.test_name}
Type: {fix.root_cause.failure.failure_type.name}
Confidence: {fix.confidence:.2f}

Auto-generated by self-healing test system.
"""

            # Commit
            result = subprocess.run(
                ["git", "commit", "-m", message],
                capture_output=True,
                text=True,
                cwd=self.project_root,
            )

            if result.returncode != 0:
                return None

            # Get commit hash
            result = subprocess.run(
                ["git", "rev-parse", "HEAD"],
                capture_output=True,
                text=True,
                cwd=self.project_root,
            )

            return result.stdout.strip()[:8]

        except Exception:
            return None

    def merge_to_main(self, branch_name: str) -> bool:
        """
        Merge a fix branch back to main.

        Args:
            branch_name: Name of the branch to merge

        Returns:
            True if merge succeeded
        """
        try:
            # Checkout main
            subprocess.run(
                ["git", "checkout", "main"],
                capture_output=True,
                cwd=self.project_root,
            )

            # Merge the branch
            result = subprocess.run(
                ["git", "merge", "--no-ff", branch_name, "-m",
                 f"Merge self-heal fix: {branch_name}"],
                capture_output=True,
                cwd=self.project_root,
            )

            return result.returncode == 0

        except Exception:
            return False
