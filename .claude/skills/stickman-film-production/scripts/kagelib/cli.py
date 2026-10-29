"""kage.py command-line interface."""
from __future__ import annotations

import argparse
import json
import os
import platform
import re
import shutil
import sys
import time
from pathlib import Path

from . import media as M
from .audio import say_available
from .compile import compile_shot
from .pipeline import Pipeline, slug
from .project import Project
from .scheduler import Scheduler
from .timeline import build_timeline
from .util import (KageError, SKILL_DIR, TEMPLATE_DIR, TOOL_VERSION, dump_json, find_engine, find_engine_source, load_json,
                   log, require_engine, require_tool, run, sha256_file)
from .validate import validate_project


def _tool_version(cmd: list[str], pattern: str = r"(\d+(\.\d+)+)") -> str | None:
    if not shutil.which(cmd[0]):
        return None
    try:
        out = run(cmd, check=False, timeout=20)
        m = re.search(pattern, (out.stdout + out.stderr).decode("utf-8", "replace"))
        return m.group(1) if m else "present"
    except Exception:
        return None


def environment_report() -> dict:
    engine = find_engine()
    gl = None
    if engine:
        probe = Path(os.environ.get("TMPDIR", "/tmp")) / "kage_gl_probe.json"
        probe.write_text(json.dumps({"kage_scene_version": "1.0", "shot_id": "probe", "output": {"width": 64, "height": 64, "fps": {"num": 24},
                                     "frame_count": 1}, "camera": {"keys": [{"t": 0, "x": 0, "y": 0, "height": 2}]}}))
        cp = run([engine, probe, "--backend", "gl", "--frame", "0", "--stats"], check=False, timeout=60)
        err = cp.stderr.decode("utf-8", "replace")
        gl = {"available": cp.returncode == 0, "detail": (re.search(r"backend (.+)", err) or re.search(r"unavailable: (.+)", err) or [None, err.strip()[:200]])[1]}
    return {
        "schema": "kage.environment/1", "checked": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "os": f"{platform.system()} {platform.release()} ({platform.machine()})", "python": platform.python_version(),
        "tools": {"kage_tools": TOOL_VERSION, "cmake": _tool_version(["cmake", "--version"]),
                  "cxx": _tool_version(["c++", "--version"]), "ffmpeg": _tool_version(["ffmpeg", "-version"], r"version (\S+)"),
                  "ffprobe": _tool_version(["ffprobe", "-version"], r"version (\S+)")},
        "engine": {"path": str(engine) if engine else None, "version": _tool_version([str(engine), "--version"]) if engine else None,
                   "source": str(find_engine_source()) if find_engine_source() else None, "opengl": gl},
        "voice_providers": {"macos_say": say_available()},
        "ffmpeg_encoders": {e: _has_encoder(e) for e in ("libx264", "aac", "ffv1")},
    }


def _has_encoder(name: str) -> bool:
    if not shutil.which("ffmpeg"):
        return False
    out = run(["ffmpeg", "-hide_banner", "-encoders"], check=False).stdout.decode()
    return re.search(rf"\s{name}\s", out) is not None


def cmd_doctor(a):
    rep = environment_report()
    print(json.dumps(rep, indent=2))
    if a.project:
        dump_json(Path(a.project) / "build" / "environment.json", rep)
    blocking = [k for k, v in {"engine": rep["engine"]["path"], "ffmpeg": rep["tools"]["ffmpeg"]}.items() if not v]
    if blocking:
        print(f"\nBLOCKING: missing {blocking}. Run `kage.py engine-build` / install FFmpeg.", file=sys.stderr)
        return 1
    return 0


def cmd_engine_build(a):
    src = find_engine_source()
    if not src:
        raise KageError("engine source not found (set KAGE_ENGINE_SRC to the directory with engine/CMakeLists.txt)")
    build = src / "build"
    require_tool("cmake")
    gl = "ON" if not a.no_gl else "OFF"
    log(f"configuring {src} (OpenGL={gl})")
    cp = run(["cmake", "-S", src, "-B", build, "-DCMAKE_BUILD_TYPE=Release", f"-DKAGE_WITH_GL={gl}"], timeout=600)
    print(cp.stdout.decode()[-800:])
    run(["cmake", "--build", build, "--config", "Release", "-j", str(os.cpu_count() or 4)], timeout=1800)
    tests = run(["ctest", "--test-dir", build, "-C", "Release", "--output-on-failure"], check=False, timeout=600)
    print(tests.stdout.decode()[-1500:])
    engine = find_engine()
    if engine:
        dump_json(SKILL_DIR / "engine.local.json", {"kage_render": str(engine)})
        print(f"engine: {engine}")
    return 0 if tests.returncode == 0 else 1


def cmd_init(a):
    root = Path(a.directory)
    if root.exists() and any(root.iterdir()):
        raise KageError(f"{root} exists and is not empty; refusing to overwrite")
    shutil.copytree(TEMPLATE_DIR / "project", root)
    prod = load_json(root / "production.json")
    pid = "prj_" + slug(a.title)
    prod.update({"project_id": pid, "title": a.title})
    if a.aspect:
        dims = {"16:9": (1280, 720), "9:16": (720, 1280), "4:5": (864, 1080), "5:4": (1080, 864), "1:1": (1080, 1080)}
        w, h = dims[a.aspect]
        prod["formats"] = [{"id": "main", "aspect": a.aspect, "width": w, "height": h, "primary": True}]
    dump_json(root / "production.json", prod)
    if a.story:
        shutil.copy2(a.story, root / "story" / "story.md")
    print(f"initialised {root} ({pid}). Next: write story/brief.json, the visual bible, characters, shots; then `kage.py validate`.")
    return 0


def cmd_validate(a):
    p = Project(Path(a.project))
    rep = validate_project(p, for_render=not a.draft, approvals_path=p.build / "approvals.json")
    doc = rep.to_json(p.production["project_id"], {"tool_version": TOOL_VERSION})
    dump_json(p.qa_dir / "planning.json", doc)
    for i in rep.issues:
        print(f"{i['severity'].upper():7} [{i['category']}] {i['ref']}: {i['message']}")
    print(f"planning QA: {'PASSED' if doc['passed'] else 'FAILED'} ({doc['error_count']} errors, {doc['warning_count']} warnings)")
    return 0 if doc["passed"] else 2


def cmd_animatic(a):
    """Low-res, low-fps flat-style animatic with estimated (or generated) timing."""
    p = Project(Path(a.project))
    pipe = Pipeline(p)
    rep = validate_project(p, for_render=False)
    if rep.errors:
        for i in rep.errors:
            print(f"ERROR [{i['category']}] {i['ref']}: {i['message']}")
        return 2
    rc = p.render.get("animatic", {})
    fps = (rc.get("fps", {}).get("num", 12), rc.get("fps", {}).get("den", 1))
    fmt = next((f for f in p.formats() if f["id"] == a.format), p.primary_format())
    width = rc.get("width", 480)
    height = int(round(width * fmt["height"] / fmt["width"] / 2)) * 2
    lines = pipe.line_records()
    tl = build_timeline(p, lines)
    out = p.build / "animatic" / fmt["id"]
    out.mkdir(parents=True, exist_ok=True)
    engine = require_engine()
    clips, mids = [], []
    for s in tl["shots"]:
        scene = compile_shot(p, s["id"], fmt, tl, lines, fps_override=fps, size_override=(width, height))
        sp = out / f"{s['id']}.scene.json"
        dump_json(sp, scene)
        n = scene["output"]["frame_count"]
        clip = out / f"{s['id']}.mp4"
        cp = run([engine, sp, "--raw", "-", "--backend", "soft", "--ssaa", "2", "--style", rc.get("style", "flat")], timeout=600)
        run(["ffmpeg", "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{width}x{height}", "-r", f"{fps[0]}/{fps[1]}",
             "-i", "-", "-c:v", "libx264", "-pix_fmt", "yuv420p", clip], input_bytes=cp.stdout, timeout=600)
        clips.append(clip)
        mid = out / f"{s['id']}_mid.png"
        run([engine, sp, "--frame", str(n // 2), "--png", mid, "--backend", "soft", "--style", rc.get("style", "flat")])
        mids.append(mid)
    listing = out / "concat.txt"
    listing.write_text("".join(f"file '{c.name}'\n" for c in clips))
    film = p.output / "review" / f"animatic_{fmt['id']}.mp4"
    film.parent.mkdir(parents=True, exist_ok=True)
    run(["ffmpeg", "-v", "error", "-y", "-f", "concat", "-safe", "0", "-i", listing, "-c", "copy", film])
    sheet = p.output / "review" / f"storyboard_{fmt['id']}.png"
    args = ["ffmpeg", "-v", "error", "-y"]
    for m in mids:
        args += ["-i", m]
    cols = 3
    rows = -(-len(mids) // cols)
    layout = "|".join(f"{(i % cols) * width}_{(i // cols) * height}" for i in range(len(mids)))
    args += ["-filter_complex", f"{''.join(f'[{i}]' for i in range(len(mids)))}xstack=inputs={len(mids)}:layout={layout}:fill=white", sheet]
    if len(mids) > 1:
        run(args)
    index = {"schema": "kage.animatic/1", "format": fmt["id"], "fps": {"num": fps[0], "den": fps[1]}, "size": [width, height],
             "film": str(film.relative_to(p.root)), "storyboard": str(sheet.relative_to(p.root)), "storyboard_columns": cols,
             "shots": [{"id": s["id"], "start_s": s["start_s"], "duration_s": s["duration_s"], "tile": i,
                        "timing": "generated" if all(lines.get(l["id"]) for l in p.lines_for_shot(s["id"])) else "estimated"}
                       for i, s in enumerate(tl["shots"])],
             "dialogue_timing": tl["line_timing"], "review_required": "Approve shot order, timing and framing before final rendering."}
    dump_json(p.output / "review" / f"animatic_{fmt['id']}.json", index)
    print(json.dumps({k: index[k] for k in ("film", "storyboard")}, indent=2))
    return 0


def _scheduler(p: Project, pipe: Pipeline, a) -> Scheduler:
    conc = dict(p.render.get("concurrency", {}))
    if getattr(a, "gpu", None):
        conc["gpu"] = a.gpu
    if getattr(a, "cpu", None):
        conc["cpu"] = a.cpu
    return Scheduler(pipe.tasks(), pipe.paths["state"], concurrency=conc, backoff_s=p.render.get("retries", {}).get("backoff_s", 0.5),
                     root=p.root)


def cmd_plan(a):
    p = Project(Path(a.project))
    pipe = Pipeline(p, formats=a.formats)
    sched = _scheduler(p, pipe, a)
    graph = {"schema": "kage.task_graph/1", "project_id": p.production["project_id"],
             "concurrency": sched.limits,
             "tasks": [{"id": t.id, "kind": t.kind, "deps": t.deps, "resource": t.resource, "max_attempts": t.max_attempts,
                        "outputs": [str(Path(o).relative_to(p.root)) for o in t.outputs],
                        "status": sched.state["tasks"].get(t.id, {}).get("status", "pending")}
                       for t in (sched.tasks[i] for i in sched.topo_order())]}
    dump_json(p.build / "tasks.json", graph)
    for t in graph["tasks"]:
        print(f"{t['status']:8} {t['id']:40} <- {', '.join(t['deps']) or '-'}")
    return 0


class ProjectLock:
    """Exclusive per-project run lock: two concurrent runs could overwrite each
    other's outputs and checkpoint state. Locks left by dead processes are reclaimed."""

    def __init__(self, project: Project):
        self.path = project.build / "run.lock"

    def __enter__(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        for _ in range(2):
            try:
                fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                os.write(fd, f"{os.getpid()}\n".encode())
                os.close(fd)
                return self
            except FileExistsError:
                try:
                    pid = int(self.path.read_text().strip() or 0)
                    os.kill(pid, 0)
                except (ValueError, ProcessLookupError):
                    log(f"removing stale run lock {self.path}")
                    self.path.unlink(missing_ok=True)
                    continue
                except PermissionError:
                    pass
                raise KageError(f"another kage run (pid {pid}) is active on this project; wait for it or stop it")
        raise KageError(f"could not acquire {self.path}")

    def __exit__(self, *exc):
        self.path.unlink(missing_ok=True)


def cmd_run(a):
    p = Project(Path(a.project))
    with ProjectLock(p):
        dump_json(p.build / "environment.json", environment_report())
        pipe = Pipeline(p, formats=a.formats, backend=a.backend)
        sched = _scheduler(p, pipe, a)
        rep = sched.run(only=a.only, force=a.force)
    dump_json(p.build / "run_report.json", rep)
    print(json.dumps({"counts": rep["counts"], "elapsed_s": rep["elapsed_s"], "failures": rep["failures"]}, indent=2))
    return 0 if not rep["tasks"]["failed"] and not rep["tasks"]["blocked"] else 3


def cmd_status(a):
    p = Project(Path(a.project))
    state = load_json(p.build / "state.json") if (p.build / "state.json").exists() else {"tasks": {}}
    for tid, rec in sorted(state["tasks"].items()):
        extra = rec.get("error", "")[:120] if rec.get("status") == "failed" else ""
        print(f"{rec.get('status', '?'):8} {tid:40} attempts={rec.get('attempts', '-')} {extra}")
    return 0


def cmd_impact(a):
    p = Project(Path(a.project))
    print(json.dumps(p.impact(a.id), indent=2))
    return 0


def cmd_approve(a):
    """Record approved content hashes; later edits without a version bump fail validation."""
    p = Project(Path(a.project))
    path = p.build / "approvals.json"
    data = load_json(path) if path.exists() else {"schema": "kage.approvals/1", "records": {}}
    keys = list(p.files) if a.keys == ["all"] else a.keys
    for k in keys:
        if k not in p.files:
            raise KageError(f"unknown document key '{k}' (use an id such as chr_maya, or production/bible/registry/dialogue/audio_plan)")
        doc = load_json(p.files[k])
        status = doc.get("status")
        if status not in (None, "approved", "locked"):
            raise KageError(f"{k} has status '{status}'; set it to approved/locked before recording approval")
        data["records"][k] = {"version": doc.get("version"), "sha256": sha256_file(p.files[k]), "by": a.by,
                              "at": time.strftime("%Y-%m-%dT%H:%M:%S")}
    dump_json(path, data)
    print(f"recorded approval for {len(keys)} document(s) by {a.by}")
    return 0


def cmd_review(a):
    """Record a subjective (visual/listening/creative) review, stating who performed it."""
    p = Project(Path(a.project))
    path = p.qa_dir / "reviews.json"
    data = load_json(path) if path.exists() else {"schema": "kage.reviews/1", "reviews": []}
    data["reviews"].append({"stage": a.stage, "reviewer": a.by, "kind": a.kind, "verdict": a.verdict,
                            "artifacts": a.artifacts or [], "notes": a.notes, "at": time.strftime("%Y-%m-%dT%H:%M:%S")})
    dump_json(path, data)
    print(f"recorded {a.kind} review of {a.stage} by {a.by}: {a.verdict}")
    return 0


def cmd_render_frames(a):
    """Re-render a frame range of one shot as PNGs (inspection or targeted repair)."""
    p = Project(Path(a.project))
    fmt = next((f for f in p.formats() if f["id"] == a.format), None)
    if not fmt:
        raise KageError(f"unknown format '{a.format}'")
    scene = p.build / fmt["id"] / "scenes" / f"{a.shot}.scene.json"
    if not scene.exists():
        raise KageError(f"{scene} missing; run `kage.py run --only compile:{fmt['id']}:{a.shot}` first")
    out = Path(a.out or p.build / "frames" / fmt["id"] / a.shot)
    out.mkdir(parents=True, exist_ok=True)
    run([require_engine(), scene, "--frames", a.frames, "--png-dir", out, "--png-every", str(a.every), "--backend", a.backend,
         "--style", a.style, "--stats"])
    print(f"frames written to {out}")
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(prog="kage.py", description="Kage stickman film production tools")
    sub = ap.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("doctor", help="report tools, engine, OpenGL and voice availability")
    s.add_argument("project", nargs="?")
    s.set_defaults(fn=cmd_doctor)
    s = sub.add_parser("engine-build", help="configure, build and test the C++ engine")
    s.add_argument("--no-gl", action="store_true")
    s.set_defaults(fn=cmd_engine_build)
    s = sub.add_parser("init", help="create a new project skeleton")
    s.add_argument("directory")
    s.add_argument("--title", required=True)
    s.add_argument("--aspect", choices=["16:9", "9:16", "4:5", "5:4", "1:1"])
    s.add_argument("--story", help="path to the story text to copy into story/story.md")
    s.set_defaults(fn=cmd_init)
    s = sub.add_parser("validate", help="planning QA (schemas, references, coverage, timing, approvals)")
    s.add_argument("project")
    s.add_argument("--draft", action="store_true", help="allow draft statuses (animatic stage)")
    s.set_defaults(fn=cmd_validate)
    s = sub.add_parser("animatic", help="render a low-res flat animatic + storyboard sheet")
    s.add_argument("project")
    s.add_argument("--format")
    s.set_defaults(fn=cmd_animatic)
    for name, fn, hlp in (("plan", cmd_plan, "write and print the task graph"), ("run", cmd_run, "execute the production graph")):
        s = sub.add_parser(name, help=hlp)
        s.add_argument("project")
        s.add_argument("--formats", nargs="*")
        s.add_argument("--only", nargs="*", help="task id globs (dependencies included)")
        s.add_argument("--force", nargs="*", help="task id globs to re-run even if cached")
        s.add_argument("--backend", choices=["auto", "gl", "soft"])
        s.add_argument("--gpu", type=int)
        s.add_argument("--cpu", type=int)
        s.set_defaults(fn=fn)
    s = sub.add_parser("status", help="show task states")
    s.add_argument("project")
    s.set_defaults(fn=cmd_status)
    s = sub.add_parser("impact", help="list shots/lines affected by changing an id")
    s.add_argument("project")
    s.add_argument("id")
    s.set_defaults(fn=cmd_impact)
    s = sub.add_parser("approve", help="record approval hashes for documents")
    s.add_argument("project")
    s.add_argument("keys", nargs="+")
    s.add_argument("--by", required=True)
    s.set_defaults(fn=cmd_approve)
    s = sub.add_parser("review", help="record a subjective review and who performed it")
    s.add_argument("project")
    s.add_argument("--stage", required=True)
    s.add_argument("--by", required=True)
    s.add_argument("--kind", choices=["visual", "listening", "creative"], required=True)
    s.add_argument("--verdict", choices=["approved", "changes_requested", "rejected"], required=True)
    s.add_argument("--notes", default="")
    s.add_argument("--artifacts", nargs="*")
    s.set_defaults(fn=cmd_review)
    s = sub.add_parser("render-frames", help="render a frame range of a compiled shot to PNG")
    s.add_argument("project")
    s.add_argument("--shot", required=True)
    s.add_argument("--format", required=True)
    s.add_argument("--frames", required=True, help="A:B")
    s.add_argument("--every", type=int, default=1)
    s.add_argument("--backend", default="soft", choices=["auto", "gl", "soft"])
    s.add_argument("--style", default="final", choices=["final", "flat", "silhouette"])
    s.add_argument("--out")
    s.set_defaults(fn=cmd_render_frames)

    a = ap.parse_args(argv)
    try:
        return a.fn(a)
    except KageError as e:
        print(f"kage: error: {e}", file=sys.stderr)
        return 2
