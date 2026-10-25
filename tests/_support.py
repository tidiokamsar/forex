import tempfile
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
TEST_TEMP_ROOT = PROJECT_ROOT / ".test-tmp"


def workspace_tempdir():
    TEST_TEMP_ROOT.mkdir(exist_ok=True)
    return tempfile.TemporaryDirectory(dir=TEST_TEMP_ROOT)
