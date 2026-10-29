"""Production QA: render, framing, motion, continuity, audio and final film.

Deterministic technical checks only. Subjective creative review (does it look
right, is the performance convincing) is recorded separately with
`kage.py review`, stating who performed it; it is never implied by these checks.
"""
from __future__ import annotations

import math
from pathlib import Path

from .png import read_png
from .project import Project
from .validate import Report


def _hex(c: str) -> tuple[int, int, int]:
    return int(c[1:3], 16), int(c[3:5], 16), int(c[5:7], 16)


def frame_stats(png_path: Path, palette_hex: list[str], tolerance: float = 24.0) -> dict:
    img = read_png(png_path)
    pal = [_hex(c) for c in palette_hex]
    n = img.width * img.height
    near = 0
    lum_sum = lum_sq = 0.0
    step = 3  # sample every 3rd pixel; plenty for statistics
    count = 0
    cache = {}
    rgb = img.rgb
    for i in range(0, n, step):
        p = (rgb[3 * i], rgb[3 * i + 1], rgb[3 * i + 2])
        count += 1
        lum = 0.2126 * p[0] + 0.7152 * p[1] + 0.0722 * p[2]
        lum_sum += lum
        lum_sq += lum * lum
        hit = cache.get(p)
        if hit is None:
            hit = min((p[0] - c[0]) ** 2 + (p[1] - c[1]) ** 2 + (p[2] - c[2]) ** 2 for c in pal) <= tolerance ** 2
            cache[p] = hit
        near += hit
    mean = lum_sum / count
    return {"width": img.width, "height": img.height, "palette_conformity": round(near / count, 4),
            "luma_mean": round(mean, 2), "luma_std": round(math.sqrt(max(0.0, lum_sq / count - mean * mean)), 2)}


def shot_qa(project: Project, fmt: dict, shot_id: str, scene: dict, probe: dict, render_info: dict,
            sample_pngs: list[Path], camera_spec: dict | None = None) -> Report:
    rep = Report(f"shot:{fmt['id']}:{shot_id}")
    shot = project.shots[shot_id]
    expected = scene["output"]["frame_count"]
    rep.check("render")
    if render_info.get("frames") != expected:
        rep.error("render", shot_id, f"rendered {render_info.get('frames')} frames, expected {expected}")
    palette = list(project.bible["palette"].values())
    for png in sample_pngs:
        rep.check("render")
        st = frame_stats(png, palette)
        if (st["width"], st["height"]) != (fmt["width"], fmt["height"]):
            rep.error("render", png.name, f"frame is {st['width']}x{st['height']}, expected {fmt['width']}x{fmt['height']}")
        if st["luma_std"] < 2.0:
            rep.error("render", png.name, "frame is nearly uniform (blank or missing content)")
        if st["palette_conformity"] < 0.85:
            rep.error("render", png.name, f"only {st['palette_conformity']:.0%} of pixels are near palette colors (wrong colors?)")
        rep.issues.append({"severity": "info", "category": "render", "ref": png.name, "message": "frame statistics", "data": st})

    allow = set(shot.get("framing", {}).get("allow_partial", []))
    frames = probe["frames"]
    cast = {m["character"] for m in shot.get("cast", [])}
    spec = camera_spec or shot["camera"]
    framing = spec.get("framing", "custom")
    subjects = set(spec.get("subjects") or cast)
    W, H = probe["width"], probe["height"]

    def head_inside(e) -> bool:
        x, y, r = e.get("head_px", [0, 0, 0])
        return e["drawn"] and x - r >= 0 and y - r >= 0 and x + r <= W and y + r <= H

    for ent_id in sorted({e["id"] for f in frames for e in f["entities"]}):
        if ent_id in allow:
            continue
        series = [next(e for e in f["entities"] if e["id"] == ent_id) for f in frames]
        is_char = ent_id in cast
        if not is_char and not any(e.get("important") for e in series):
            continue
        rep.check("framing")
        if is_char and ent_id not in subjects:
            # Not a camera subject: being partly or wholly off-screen can be intended.
            if any(e["drawn"] and e["visible_fraction"] > 0.05 and not head_inside(e) for e in series):
                rep.warn("framing", shot_id, f"{ent_id} (not a camera subject) appears with its head cut off ({fmt['id']})")
            continue
        full_body = not is_char or framing in ("wide", "custom")
        for e, f in zip(series, frames):
            ok = (e["drawn"] and e["visible_fraction"] >= 0.98) if full_body else (head_inside(e) and e["visible_fraction"] >= 0.25)
            if not ok:
                what = f"visible {e['visible_fraction']:.0%}" if full_body else "head not fully in frame"
                rep.error("framing", f"{shot_id}@{f['frame']}", f"{ent_id} is cut off ({what}) in format {fmt['id']} ({framing} framing)",
                          "adjust the framing or add a camera variant for this aspect; list it in framing.allow_partial only if intended")
                break
        if full_body:
            safe = sum(1 for e in series if e["in_safe_area"]) / len(series)
            if safe < 1.0:
                rep.warn("framing", shot_id, f"{ent_id} leaves the {probe['safe_area']:.0%} safe area in {1 - safe:.0%} of frames ({fmt['id']})")

    for cid in sorted(cast):
        rep.check("motion")
        series = [(f["frame"], next(e for e in f["entities"] if e["id"] == cid)) for f in frames]
        H = project.characters[cid]["body"].get("height_m", 1.7)
        for (fa, a), (fb, b) in zip(series, series[1:]):
            dh = math.dist(a["head"], b["head"])
            if dh > 0.2 * H:
                rep.error("motion", f"{shot_id}@{fb}", f"{cid} head jumps {dh:.2f} m in one frame (pose pop)")
            for hand in ("hand_l", "hand_r"):
                d = math.dist(a[hand], b[hand])
                if d > 0.35 * H:
                    rep.warn("motion", f"{shot_id}@{fb}", f"{cid} {hand} moves {d:.2f} m in one frame")
            if b["posture"] == "stand" and min(b["sole_y"]) < -0.02:
                rep.error("motion", f"{shot_id}@{fb}", f"{cid} foot below the ground ({min(b['sole_y']):.3f} m)")
            if b["posture"] == "stand" and min(b["sole_y"]) > 0.03 and a["posture"] == "stand":
                rep.warn("motion", f"{shot_id}@{fb}", f"{cid} floating above the ground while standing")
    return rep


def continuity_qa(project: Project, fmt: dict, probes: dict[str, dict], scenes: dict[str, dict]) -> Report:
    rep = Report(f"continuity:{fmt['id']}")
    order = project.shot_order()
    for a_id, b_id in zip(order, order[1:]):
        a, b = project.shots[a_id], project.shots[b_id]
        if a["scene"] != b["scene"]:
            continue
        pa, pb = probes[a_id]["frames"][-1], probes[b_id]["frames"][0]
        ea = {e["id"]: e for e in pa["entities"] if e["kind"] == "character"}
        eb = {e["id"]: e for e in pb["entities"] if e["kind"] == "character"}
        ref = f"{a_id}->{b_id}"
        declared = set(b.get("continuity", {}).get("offscreen", [])) | set(a.get("continuity", {}).get("exits", []))
        for cid in ea:
            rep.check("continuity")
            if cid not in eb and cid not in declared:
                rep.error("continuity", ref, f"{cid} disappears across the cut (declare in offscreen/exits if intended)")
        for cid in set(ea) & set(eb):
            x, y = ea[cid], eb[cid]
            rep.check("continuity")
            if x["facing"] != y["facing"]:
                rep.error("continuity", ref, f"{cid} faces {'right' if x['facing'] > 0 else 'left'} at the end of {a_id} "
                                             f"but {'right' if y['facing'] > 0 else 'left'} at the start of {b_id}")
            if x["posture"] != y["posture"]:
                rep.error("continuity", ref, f"{cid} posture {x['posture']} -> {y['posture']} across the cut")
            if abs(x["root"][0] - y["root"][0]) > 0.08:
                rep.error("continuity", ref, f"{cid} position jumps {abs(x['root'][0] - y['root'][0]):.2f} m across the cut")
            ha = scenes[a_id]["_character_hash"].get(cid)
            hb = scenes[b_id]["_character_hash"].get(cid)
            if ha and hb and ha != hb:
                rep.error("continuity", ref, f"{cid} appearance differs between shots (variant or version change)")
        both = [c for c in ea if c in eb]
        if len(both) >= 2 and not b.get("continuity", {}).get("allow_screen_direction_change"):
            rep.check("screen_direction")
            oa = sorted(both, key=lambda c: (ea[c]["bbox"][0] + ea[c]["bbox"][2]))
            ob = sorted(both, key=lambda c: (eb[c]["bbox"][0] + eb[c]["bbox"][2]))
            if oa != ob:
                rep.error("screen_direction", ref, f"left-to-right order changes across the cut ({oa} -> {ob}): 180-degree rule")
    return rep


def audio_qa(project: Project, timeline: dict, generated: dict[str, dict], mix: dict, stems: dict[str, Path],
             music_ducked: Path | None) -> Report:
    from .audio import rms_window

    rep = Report("audio")
    target = project.production["delivery"]
    for ln in project.dialogue["lines"]:
        rep.check("dialogue")
        g = generated.get(ln["id"])
        if not g:
            rep.error("dialogue", ln["id"], "line has no generated audio")
            continue
        if g["peak"] >= 0.999:
            rep.error("clipping", ln["id"], f"line audio clips (peak {g['peak']})")
        for note in g.get("approximations", []):
            rep.warn("voice", ln["id"], note)
    by_voice: dict[str, list] = {}
    for g in generated.values():
        if g.get("f0_hz"):
            by_voice.setdefault(g["voice"], []).append(g["f0_hz"])
    medians = {v: sorted(f)[len(f) // 2] for v, f in by_voice.items()}
    for v, fs in by_voice.items():
        rep.check("voice_consistency")
        if max(fs) / min(fs) > 1.3:
            rep.warn("voice_consistency", v, f"estimated pitch varies {min(fs):.0f}-{max(fs):.0f} Hz across lines")
    vids = sorted(medians)
    for i, a in enumerate(vids):
        for b in vids[i + 1:]:
            rep.check("voice_distinct")
            ratio = max(medians[a], medians[b]) / min(medians[a], medians[b])
            if ratio < 1.06 and project.voices[a]["provider_voice"] == project.voices[b]["provider_voice"]:
                rep.warn("voice_distinct", f"{a}/{b}", "two characters share a provider voice and similar pitch")
    for e in timeline["errors"]:
        rep.error("timing", "timeline", e)
    m = mix["master"]
    rep.check("loudness")
    if abs(m["integrated_lufs"] - target["loudness_lufs"]) > 1.0:
        rep.error("loudness", "master", f"integrated loudness {m['integrated_lufs']:.1f} LUFS (target {target['loudness_lufs']})")
    if m["true_peak_dbtp"] > target["true_peak_dbtp"] + 0.3:
        rep.error("loudness", "master", f"true peak {m['true_peak_dbtp']:.1f} dBTP exceeds {target['true_peak_dbtp']}")
    for name, path in stems.items():
        rep.check("stems")
        if not path.exists() or path.stat().st_size < 1000:
            rep.error("stems", name, "stem missing or empty")
    if music_ducked and any(c["kind"] == "dialogue" for c in timeline["audio_clips"]):
        rep.check("ducking")
        dl = [c for c in timeline["audio_clips"] if c["kind"] == "dialogue"]
        during = sum(rms_window(music_ducked, c["start_s"] + 0.1, c["start_s"] + c["duration_s"] - 0.1) for c in dl) / len(dl)
        before = sum(rms_window(music_ducked, max(0, c["start_s"] - 1.3), c["start_s"] - 0.3) for c in dl) / len(dl)
        drop_db = 20 * math.log10(max(before, 1e-9) / max(during, 1e-9))
        rep.issues.append({"severity": "info", "category": "ducking", "ref": "music", "message": f"music drops {drop_db:.1f} dB under dialogue"})
        requested = max((c.get("duck_under_dialogue_db") or 0) for c in timeline["audio_clips"] if c["kind"] == "music") \
            if any(c["kind"] == "music" for c in timeline["audio_clips"]) else 0
        if requested and drop_db < 0.6 * requested:
            rep.warn("ducking", "music", f"music drops {drop_db:.1f} dB under dialogue; {requested} dB was requested (may mask speech)")
    return rep
