"""Tests run against a throw-away SQLite file so they never touch the app's real cache or saved plots.
This must run before any `app.*` import, which is why it lives in conftest.py."""
import os
import tempfile
from pathlib import Path

_tmp = Path(tempfile.mkdtemp(prefix="khetdrishti-tests-"))
os.environ["DATABASE_URL"] = f"sqlite:///{(_tmp / 'test.db').as_posix()}"
