"""Rendering and encoding: engine frames -> lossless intermediates -> delivery."""
from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

from .util import KageError, TransientError, ffmpeg_filter_script_args, ffprobe_json, require_engine, require_tool, run


def render_shot(scene_path: Path, out_video: Path, probe_path: Path, events_path: Path, *, backend: str = "auto",
                ssaa: int = 3, msaa: int = 8, timeout: float = 900, env=None) -> dict:
    """Stream engine frames into an FFV1 (lossless RGB) intermediate; verify frame count."""
    engine = require_engine()
    ffmpeg = require_tool("ffmpeg")
    scene = json.loads(Path(scene_path).read_text())
    out = scene["output"]
    w, h, frames = out["width"], out["height"], out["frame_count"]
    rate = f"{out['fps']['num']}/{out['fps'].get('den', 1)}"
    out_video.parent.mkdir(parents=True, exist_ok=True)
    tmp = out_video.with_name(out_video.stem + ".partial.mkv")
    eng = subprocess.Popen([str(engine), str(scene_path), "--raw", "-", "--backend", backend, "--ssaa", str(ssaa), "--msaa", str(msaa),
                            "--probe", str(probe_path), "--events", str(events_path), "--stats"],
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env)
    enc = subprocess.Popen([ffmpeg, "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{w}x{h}", "-r", rate,
                            "-i", "-", "-c:v", "ffv1", "-level", "3", "-pix_fmt", "bgr0", str(tmp)],
                           stdin=eng.stdout, stderr=subprocess.PIPE)
    eng.stdout.close()
    try:
        _, enc_err = enc.communicate(timeout=timeout)
        eng_err = eng.stderr.read().decode("utf-8", "replace")
        eng.wait(timeout=30)
    except subprocess.TimeoutExpired as e:
        eng.kill()
        enc.kill()
        raise TransientError(f"render of {scene['shot_id']} timed out after {timeout}s") from e
    if eng.returncode != 0:
        msg = eng_err.strip()[-1500:]
        if eng.returncode in (75,) or eng.returncode < 0:
            raise TransientError(f"engine exited {eng.returncode}: {msg}")
        raise KageError(f"engine exited {eng.returncode}: {msg}")
    if enc.returncode != 0:
        raise TransientError(f"ffmpeg encode failed: {enc_err.decode('utf-8', 'replace')[-1500:]}")
    got = count_frames(tmp)
    if got != frames:
        raise TransientError(f"{scene['shot_id']}: intermediate has {got} frames, expected {frames}")
    tmp.replace(out_video)
    stats = re.search(r"(\d+) frames in ([\d.]+) ms.*?([\d.]+) fps", eng_err)
    backend_line = re.search(r"backend (.+)", eng_err)
    return {"frames": got, "width": w, "height": h, "fps": rate,
            "engine_fps": float(stats.group(3)) if stats else None,
            "backend": backend_line.group(1).strip() if backend_line else backend,
            "warnings": [l for l in eng_err.splitlines() if "unavailable" in l]}


def count_frames(path: Path) -> int:
    info = ffprobe_json(path, "-count_packets", "-select_streams", "v:0")
    return int(info["streams"][0]["nb_read_packets"])


def render_frame_png(scene_path: Path, frame: int, png: Path, *, backend: str = "soft", style: str = "final") -> None:
    run([require_engine(), scene_path, "--frame", str(frame), "--png", png, "--backend", backend, "--style", style], timeout=300)


def assemble(shot_videos: list[tuple[Path, dict]], audio: Path | None, subtitles: Path | None, out: Path, delivery: dict,
             fps: tuple[int, int]) -> None:
    """Concatenate shot intermediates with fades/dissolves, mux audio and subtitles."""
    ffmpeg = require_tool("ffmpeg")
    args = [ffmpeg, "-v", "error", "-y"]
    chains = []
    for i, (path, tl) in enumerate(shot_videos):
        args += ["-i", str(path)]
        f = ["setpts=PTS-STARTPTS", f"fps={fps[0]}/{fps[1]}", "format=yuv444p"]
        tin, tout = tl["transition_in"], tl["transition_out"]
        if tin.get("type") == "fade":
            f.append(f"fade=t=in:st=0:d={tin['duration_s']}:color={tin.get('color', '#000000')}")
        if tout.get("type") == "fade":
            f.append(f"fade=t=out:st={tl['duration_s'] - tout['duration_s']:.6f}:d={tout['duration_s']}:color={tout.get('color', '#000000')}")
        chains.append(f"[{i}:v]{','.join(f)}[v{i}]")
    # Dissolves use xfade (shots overlap on the timeline); cuts/fades use concat.
    cur = "[v0]"
    acc_dur = shot_videos[0][1]["duration_s"]
    pending = []
    for i in range(1, len(shot_videos)):
        tl = shot_videos[i][1]
        if tl["transition_in"].get("type") == "dissolve":
            d = tl["transition_in"]["duration_s"]
            if pending:
                chains.append(f"{cur}{''.join(pending)}concat=n={len(pending) + 1}:v=1:a=0[cc{i}]")
                cur, pending = f"[cc{i}]", []
            chains.append(f"{cur}[v{i}]xfade=transition=fade:duration={d}:offset={acc_dur - d:.6f}[x{i}]")
            cur = f"[x{i}]"
            acc_dur += tl["duration_s"] - d
        else:
            pending.append(f"[v{i}]")
            acc_dur += tl["duration_s"]
    if pending:
        chains.append(f"{cur}{''.join(pending)}concat=n={len(pending) + 1}:v=1:a=0[vcat]")
        cur = "[vcat]"
    chains.append(f"{cur}format={delivery.get('pixel_format', 'yuv420p')}[vout]")
    maps = ["-map", "[vout]"]
    n_in = len(shot_videos)
    if audio:
        args += ["-i", str(audio)]
        maps += ["-map", f"{n_in}:a"]
        n_in += 1
    if subtitles:
        args += ["-i", str(subtitles)]
        maps += ["-map", f"{n_in}:s"]
    graph_file = out.with_suffix(".graph.txt")
    out.parent.mkdir(parents=True, exist_ok=True)
    graph_file.write_text(";".join(chains))
    enc = ["-c:v", delivery.get("video_codec", "libx264"), "-crf", str(delivery.get("crf", 18)), "-preset", "medium",
           "-r", f"{fps[0]}/{fps[1]}", "-movflags", "+faststart"]
    if audio:
        enc += ["-c:a", delivery.get("audio_codec", "aac"), "-b:a", delivery.get("audio_bitrate", "192k"),
                "-ar", str(delivery.get("sample_rate", 48000)), "-ac", str(delivery.get("channels", 2))]
    if subtitles:
        enc += ["-c:s", "mov_text", "-metadata:s:s:0", "language=eng"]
    run(args + ffmpeg_filter_script_args(graph_file) + maps + enc + [str(out)], timeout=1800)
    graph_file.unlink(missing_ok=True)


def validate_export(path: Path, *, width: int, height: int, fps: tuple[int, int], duration: float, frames: int,
                    expect_audio: bool, expect_subtitles: bool, sample_rate: int = 48000, channels: int = 2) -> dict:
    """Inspect a delivered file; every check reads the real file."""
    checks = []

    def check(name, ok, detail):
        checks.append({"check": name, "passed": bool(ok), "detail": detail})

    exists = path.exists() and path.stat().st_size > 0
    check("exists_nonempty", exists, f"{path.stat().st_size if path.exists() else 0} bytes")
    if not exists:
        return {"file": str(path), "passed": False, "checks": checks}
    info = ffprobe_json(path, "-count_packets")
    fmt = info["format"]
    check("container", "mp4" in fmt.get("format_name", ""), fmt.get("format_name"))
    v = [s for s in info["streams"] if s["codec_type"] == "video"]
    a = [s for s in info["streams"] if s["codec_type"] == "audio"]
    s = [s for s in info["streams"] if s["codec_type"] == "subtitle"]
    check("video_stream", len(v) == 1, f"{len(v)} video streams")
    if v:
        vs = v[0]
        check("resolution", (vs["width"], vs["height"]) == (width, height), f"{vs['width']}x{vs['height']} (expected {width}x{height})")
        g = _gcd(vs["width"], vs["height"])
        check("aspect_ratio", abs(vs["width"] / vs["height"] - width / height) < 1e-3, f"{vs['width'] // g}:{vs['height'] // g}")
        check("frame_rate", vs["r_frame_rate"] == f"{fps[0]}/{fps[1]}", f"{vs['r_frame_rate']} (expected {fps[0]}/{fps[1]})")
        nf = int(vs.get("nb_read_packets", 0))
        check("frame_count", nf == frames, f"{nf} frames (expected {frames})")
        check("pixel_format", vs.get("pix_fmt") == "yuv420p", vs.get("pix_fmt"))
        check("video_codec", vs.get("codec_name") == "h264", vs.get("codec_name"))
    dur = float(fmt.get("duration", 0))
    check("duration", abs(dur - duration) <= 1.5 / (fps[0] / fps[1]) + 0.03, f"{dur:.3f}s (expected {duration:.3f}s)")
    if expect_audio:
        check("audio_stream", len(a) == 1, f"{len(a)} audio streams")
        if a:
            ad = float(a[0].get("duration", 0))
            check("audio_sample_rate", int(a[0]["sample_rate"]) == sample_rate, a[0]["sample_rate"])
            check("audio_channels", int(a[0]["channels"]) == channels, a[0]["channels"])
            check("audio_video_duration_match", abs(ad - duration) < 0.06, f"audio {ad:.3f}s vs video {duration:.3f}s")
    if expect_subtitles:
        check("subtitle_stream", len(s) == 1, f"{len(s)} subtitle streams")
    cp = run([require_tool("ffmpeg"), "-v", "error", "-i", str(path), "-f", "null", "-"], timeout=600, check=False)
    errs = cp.stderr.decode("utf-8", "replace").strip()
    check("full_decode", cp.returncode == 0 and not errs, errs[:300] or "decoded without errors")
    return {"file": str(path), "passed": all(c["passed"] for c in checks), "checks": checks}


def frame_seek_time(frame: int, fps: float) -> float:
    """Seek time that selects exactly `frame`: FFmpeg returns the first frame whose
    pts >= the seek time, so aim a quarter frame early (never mid-frame)."""
    return max(0.0, (frame - 0.25) / fps)


def extract_frame(video: Path, t: float, png: Path) -> None:
    run([require_tool("ffmpeg"), "-v", "error", "-y", "-ss", f"{t:.6f}", "-i", str(video), "-frames:v", "1", str(png)], timeout=120)


def psnr(a_png: Path, b_png: Path) -> float:
    cp = run([require_tool("ffmpeg"), "-hide_banner", "-i", str(a_png), "-i", str(b_png), "-lavfi", "psnr", "-f", "null", "-"], timeout=120)
    m = re.search(r"average:([\d.]+|inf)", cp.stderr.decode())
    if not m:
        raise KageError("psnr measurement failed")
    return float("inf") if m.group(1) == "inf" else float(m.group(1))


def _gcd(a: int, b: int) -> int:
    while b:
        a, b = b, a % b
    return a
