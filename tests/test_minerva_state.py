"""
Unit tests for minerva_state
===========================
Run with: python -m pytest tests/test_minerva_state.py -v
"""
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from minerva_state import MinervaState


class TestMinervaStateLazySchema(unittest.TestCase):
    """The state database should tolerate being opened without .connect()."""

    def test_conn_applies_schema_when_used_directly(self):
        with TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir, "nested", "state.db")
            state = MinervaState(db_path, _connect=False)
            # Using conn() directly (e.g. from download_controller) must create
            # the tables if they do not exist.
            rows = state.list_queue()
            self.assertEqual(rows, [])


if __name__ == "__main__":
    unittest.main()
