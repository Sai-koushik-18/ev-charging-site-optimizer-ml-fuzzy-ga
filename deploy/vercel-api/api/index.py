"""Vercel entry point for the live planner API (server/app.py)."""
import os
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RUN_DIR = Path("/tmp/ev-app")  # Vercel's code folder is read-only; /tmp is the only writable place
sys.path.insert(0, str(ROOT))

# The pipeline reads and writes paths relative to the project folder (config/, data/, outputs/),
# so run from a writable copy of the small inputs it reads.
for d in ("config", "data/external/vahan", "outputs/ml_results"):
    shutil.copytree(ROOT / d, RUN_DIR / d, dirs_exist_ok=True)
os.chdir(RUN_DIR)
os.environ.setdefault("EV_WORK_DIR", "/tmp/ev-planner")
os.environ.setdefault("EV_DEADLINE_S", "280")  # Vercel stops a request at 300 s

from server.app import app  # noqa: E402,F401
