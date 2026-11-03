import os
import sys
import tempfile
from pathlib import Path

# Run Qt without a screen, and keep Remedy's settings/log files out of the real user profile.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ["LOCALAPPDATA"] = tempfile.mkdtemp(prefix="remedy-tests-")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
