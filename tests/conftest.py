"""Make the V2M app root importable and keep pytest away from the `data`
symlink (which points at a deploy path that is not readable in dev and breaks
pytest's directory scan)."""
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# Point the app's data dir at a throwaway temp dir so importing video2mp3 does
# not touch the deploy `data` symlink (dangling in dev).
os.environ.setdefault("V2M_DATA_DIR", tempfile.mkdtemp(prefix="v2m-test-data-"))

# Keep user accounts + Yoto state out of the real .yoto/ during tests. Tests that
# need isolation still override YOTO_STATE_DIR via monkeypatch.setenv.
os.environ.setdefault("YOTO_STATE_DIR", tempfile.mkdtemp(prefix="v2m-test-state-"))
