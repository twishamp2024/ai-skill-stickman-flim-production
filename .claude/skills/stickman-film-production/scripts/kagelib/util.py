"""Shared helpers: JSON I/O, hashing, tool discovery, subprocess execution."""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parents[2]
SCHEMA_DIR = SKILL_DIR / "schemas"
LIBRARY_DIR = SKILL_DIR / "library"
TEMPLATE_DIR = SKILL_DIR / "templates"
TOOL_VERSION = "1.0.0"


class KageError(Exception):
    """A deterministic, user-actionable failure (never retried)."""


class TransientError(Exception):
    """A failure that may succeed on retry (crash, timeout, injected fault)."""


def load_json(path: Path):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except json.JSONDecodeError as e:
        raise KageError(f"{path}: invalid JSON at line {e.lineno} column {e.colno}: {e.msg}") from e
    except FileNotFoundError as e:
        raise KageError(f"missing file: {path}") from e


def dump_json(path: Path, data, indent: int = 2) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=indent, ensure_ascii=False)
        f.write("\n")
    os.replace(tmp, path)  # atomic: readers never see a half-written file


def _json_default(o):
    if hasattr(o, "__json__"):
        return o.__json__()
    if isinstance(o, Path):
        return str(o)
    raise TypeError(f"not JSON serialisable: {type(o).__name__}")


def canonical(data) -> str:
    return json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=_json_default)


def sha256_bytes(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def sha256_json(data) -> str:
    return sha256_bytes(canonical(data).encode("utf-8"))


def log(msg: str) -> None:
    print(f"[kage {time.strftime('%H:%M:%S')}] {msg}", file=sys.stderr, flush=True)


# ---------------------------------------------------------------- tool discovery

def find_engine_source() -> Path | None:
    env = os.environ.get("KAGE_ENGINE_SRC")
    if env and (Path(env) / "CMakeLists.txt").exists():
        return Path(env)
    for parent in [SKILL_DIR, *SKILL_DIR.parents]:
        cand = parent / "engine" / "CMakeLists.txt"
        if cand.exists():
            return cand.parent
    return None


def find_engine() -> Path | None:
    """Locate kage_render: $KAGE_ENGINE, engine.local.json, source tree build, PATH."""
    env = os.environ.get("KAGE_ENGINE")
    if env and Path(env).exists():
        return Path(env)
    local = SKILL_DIR / "engine.local.json"
    if local.exists():
        p = Path(load_json(local).get("kage_render", ""))
        if p.exists():
            return p
    src = find_engine_source()
    exe = "kage_render.exe" if os.name == "nt" else "kage_render"
    if src:
        for cand in (src / "build" / exe, src / "build" / "Release" / exe):
            if cand.exists():
                return cand
    found = shutil.which("kage_render")
    return Path(found) if found else None


def require_engine() -> Path:
    eng = find_engine()
    if not eng:
        raise KageError("kage_render not found. Build it with `kage.py engine-build` or set KAGE_ENGINE.")
    return eng


def require_tool(name: str) -> str:
    path = shutil.which(name)
    if not path:
        raise KageError(f"required tool '{name}' not found on PATH")
    return path


def run(cmd: list, *, timeout: float | None = None, input_bytes: bytes | None = None, cwd=None,
        env=None, check: bool = True) -> subprocess.CompletedProcess:
    """Run a command; classify failures as transient (crash/timeout/75) or deterministic."""
    try:
        cp = subprocess.run([str(c) for c in cmd], input=input_bytes, capture_output=True, timeout=timeout,
                            cwd=cwd, env=env)
    except subprocess.TimeoutExpired as e:
        raise TransientError(f"timeout after {timeout}s: {cmd[0]}") from e
    if check and cp.returncode != 0:
        msg = cp.stderr.decode("utf-8", "replace").strip()[-2000:]
        if cp.returncode == 75 or cp.returncode < 0:
            raise TransientError(f"{Path(str(cmd[0])).name} exited {cp.returncode}: {msg}")
        raise KageError(f"{Path(str(cmd[0])).name} exited {cp.returncode}: {msg}")
    return cp


_FFMPEG_MAJOR = None


def ffmpeg_filter_script_args(script: Path) -> list[str]:
    """Pass a filter graph from a file: `-/filter_complex` (FFmpeg >= 7) or the
    legacy `-filter_complex_script` (removed in FFmpeg 8+)."""
    global _FFMPEG_MAJOR
    if _FFMPEG_MAJOR is None:
        import re as _re
        out = run([require_tool("ffmpeg"), "-version"]).stdout.decode()
        m = _re.search(r"ffmpeg version n?(\d+)", out)
        _FFMPEG_MAJOR = int(m.group(1)) if m else 7
    return ["-/filter_complex", str(script)] if _FFMPEG_MAJOR >= 7 else ["-filter_complex_script", str(script)]


def ffprobe_json(path: Path, *extra: str) -> dict:
    cp = run([require_tool("ffprobe"), "-v", "error", "-print_format", "json", "-show_format", "-show_streams",
              *extra, str(path)])
    return json.loads(cp.stdout)


def media_duration(path: Path) -> float:
    return float(ffprobe_json(path)["format"]["duration"])


def rel(path: Path, root: Path) -> str:
    try:
        return str(Path(path).resolve().relative_to(Path(root).resolve()))
    except ValueError:
        return str(path)
