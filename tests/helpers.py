"""Shared test setup: import path, example project copies, tool availability."""
import json
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SKILL = ROOT / ".claude" / "skills" / "stickman-film-production"
EXAMPLE = ROOT / "examples" / "train-journey"
sys.path.insert(0, str(SKILL / "scripts"))

from kagelib.util import find_engine  # noqa: E402

ENGINE = find_engine()
HAVE_FFMPEG = shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None
HAVE_SAY = sys.platform == "darwin" and shutil.which("say") is not None


def copy_example(with_build: bool = False) -> Path:
    """Copy the example project (canonical files only unless with_build) to a temp dir."""
    dst = Path(tempfile.mkdtemp(prefix="kage_test_")) / "proj"
    ignore = None if with_build else shutil.ignore_patterns("build", "output", "qa")
    shutil.copytree(EXAMPLE, dst, ignore=ignore)
    return dst


def edit_json(path: Path, fn):
    data = json.loads(path.read_text())
    fn(data)
    path.write_text(json.dumps(data, indent=2))
