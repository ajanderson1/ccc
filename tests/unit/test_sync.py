"""
Tests for sync verification between cc_usage.sh and parser_extracted.py.

This is a critical test that ensures the test infrastructure is actually
testing the same code that runs in production.
"""

import pytest
from tests.self_heal.sync_verifier import SyncVerifier, verify_sync


class TestSyncVerifier:
    """Tests for the sync verifier."""

    def test_sync_status(self):
        """
        CRITICAL: Verify parser_extracted.py matches cc_usage.sh.

        If this test fails, it means the test code has diverged from the
        production code. Tests may pass but the actual script could have
        different (broken) behavior.

        To fix: Update parser_extracted.py to match the embedded Python
        in cc_usage.sh, or vice versa.
        """
        status = verify_sync()

        if not status.in_sync:
            # Print detailed diff for debugging
            print("\n" + "=" * 60)
            print("SYNC VERIFICATION FAILED")
            print("=" * 60)
            print(f"\n{status.message}\n")
            if status.differences:
                print("Differences:")
                for line in status.differences[:50]:
                    print(f"  {line}")
            print("\n" + "=" * 60)

        assert status.in_sync, (
            f"parser_extracted.py is OUT OF SYNC with cc_usage.sh!\n"
            f"{status.message}\n"
            f"The self-healing tests are testing different code than what "
            f"actually runs. Fix the divergence before proceeding."
        )

    def test_extract_embedded_python(self):
        """Verify we can extract Python from cc_usage.sh."""
        verifier = SyncVerifier()
        embedded = verifier.extract_embedded_python()

        assert embedded is not None, "Failed to extract embedded Python"
        assert "def strip_ansi" in embedded
        assert "def parse_reset_time" in embedded
        assert "def validate_reset_time" in embedded

    def test_extract_function(self):
        """Test function extraction from source code."""
        verifier = SyncVerifier()

        source = '''
def foo(x):
    return x + 1

def bar(y):
    return y * 2
'''
        foo_func = verifier.extract_function(source, 'foo')
        assert foo_func is not None
        assert 'return x + 1' in foo_func
        assert 'bar' not in foo_func

    def test_normalize_code(self):
        """Test code normalization."""
        verifier = SyncVerifier()

        code = '''
def foo():
    # This is a comment
    x = 1  # inline comment

    return x
'''
        normalized = verifier.normalize_code(code)

        # Comments and blank lines should be removed
        assert '# This is a comment' not in normalized
        assert 'x = 1' in normalized
