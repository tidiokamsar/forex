import tempfile
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
TEST_TEMP_ROOT = PROJECT_ROOT / ".test-tmp"


def workspace_tempdir():
    TEST_TEMP_ROOT.mkdir(exist_ok=True)
    return tempfile.TemporaryDirectory(dir=TEST_TEMP_ROOT)


def accept_unverified_evidence(_db, _row, _evidence):
    """TEST ONLY: skip registry provenance checks to exercise the numeric gates in isolation."""

    return ()
