"""Master timeline: shot timing, transitions, audio placement and subtitles.

Shot durations grow (within `timing.max_s`) to fit generated speech rather than
time-stretching or truncating dialogue. Audio is placed in absolute film time,
so clips may start before and continue after a cut.
"""
from __future__ import annotations

from .project import Project
from .util import KageError

SPEECH_TAIL_S = 0.35  # minimum breathing room after a line before the cut


def build_timeline(project: Project, line_audio: dict[str, dict] | None = None, *, allow_estimates: bool = True) -> dict:
    line_audio = line_audio or {}
    num, den = project.fps
    fps = num / den
    errors, adjustments, shots = [], [], []
    durations: dict[str, tuple[float, str]] = {}
    for ln in project.dialogue["lines"]:
        info = line_audio.get(ln["id"])
        if info and info.get("duration_s"):
            durations[ln["id"]] = (info["duration_s"], "generated")
        elif allow_estimates:
            durations[ln["id"]] = (ln["estimated_s"], "estimated")
        else:
            errors.append(f"{ln['id']}: no generated audio and estimates are not allowed")

    cursor_frame = 0
    order = project.shot_order()
    for idx, sid in enumerate(order):
        shot = project.shots[sid]
        dur = float(shot["duration_s"])
        timing = shot.get("timing", {})
        for ln in project.lines_for_shot(sid):
            if ln["id"] not in durations:
                continue
            need = ln["offset_s"] + durations[ln["id"]][0] + SPEECH_TAIL_S
            if need > dur + 1e-9:
                if timing.get("flex") and need <= timing.get("max_s", dur):
                    adjustments.append({"shot": sid, "line": ln["id"], "from_s": dur, "to_s": round(need, 4),
                                        "reason": "extend shot to fit generated speech (no time-stretch)"})
                    dur = need
                else:
                    errors.append(f"{sid}: line {ln['id']} ends at {need:.2f}s but shot is {dur:.2f}s "
                                  f"(flex={timing.get('flex', False)}, max_s={timing.get('max_s')}); "
                                  f"shorten the line, move its offset, or allow the shot to flex")
        frames = max(1, round(dur * fps))
        overlap = 0
        tin = shot.get("transition_in", {"type": "cut"})
        if tin.get("type") == "dissolve" and idx > 0:
            overlap = round(tin["duration_s"] * fps)
        start = cursor_frame - overlap
        shots.append({
            "id": sid, "scene": shot["scene"], "location": project.shot_location(sid)["id"],
            "start_frame": start, "frames": frames, "start_s": round(start / fps, 6), "duration_s": round(frames / fps, 6),
            "transition_in": tin, "transition_out": shot.get("transition_out", {"type": "cut"}),
        })
        cursor_frame = start + frames
    total_frames = cursor_frame
    by_id = {s["id"]: s for s in shots}

    def anchor_time(anchor: dict) -> float:
        s = by_id.get(anchor["shot"])
        if s is None:
            raise KageError(f"audio anchor references unknown shot '{anchor['shot']}'")
        off = anchor.get("offset_s", 0)
        return s["start_s"] + (s["duration_s"] if off == "end" else float(off))

    clips, subtitles = [], []
    for ln in project.dialogue["lines"]:
        if ln["id"] not in durations or ln["shot"] not in by_id:
            continue
        start = by_id[ln["shot"]]["start_s"] + ln["offset_s"]
        d, src = durations[ln["id"]]
        clips.append({"id": ln["id"], "stem": "dialogue", "kind": "dialogue", "line": ln["id"], "character": ln["character"],
                      "start_s": round(start, 6), "duration_s": round(d, 6), "timing_source": src, "gain_db": 0})
        if ln.get("subtitle", True):
            subtitles.append({"line": ln["id"], "start_s": round(start, 3), "end_s": round(start + d, 3), "text": ln["text"]})
    for ev in project.audio_plan.get("events", []):
        clips.append({"id": ev["id"], "stem": "sfx", "kind": "sfx", "asset": ev["asset"], "start_s": round(anchor_time(ev["anchor"]), 6),
                      "duration_s": None, "gain_db": ev.get("gain_db", 0)})
    amb = project.audio_plan.get("ambience", {})
    if amb.get("by_location", True):
        segs = []
        for s in shots:
            amb_id = project.locations[s["location"]].get("ambience")
            if not amb_id:
                continue
            end = s["start_s"] + s["duration_s"]
            if segs and segs[-1]["asset"] == amb_id:
                segs[-1]["end_s"] = end  # same location: one continuous bed across the cut
            else:
                segs.append({"asset": amb_id, "start_s": s["start_s"], "end_s": end})
        xf = amb.get("crossfade_s", 0.4)
        for i, seg in enumerate(segs):
            clips.append({"id": f"amb_{i + 1:02d}", "stem": "ambience", "kind": "ambience", "asset": seg["asset"],
                          "start_s": round(seg["start_s"], 6), "duration_s": round(seg["end_s"] - seg["start_s"] + (xf if i < len(segs) - 1 else 0), 6),
                          "fade_in_s": xf if i else 0.0, "fade_out_s": xf, "gain_db": amb.get("gain_db", -20)})
    for cue in project.audio_plan.get("music", []):
        start, end = anchor_time(cue["start"]), anchor_time(cue["end"])
        if end <= start:
            errors.append(f"{cue['id']}: music cue ends before it starts")
            continue
        clips.append({"id": cue["id"], "stem": "music", "kind": "music", "asset": cue["asset"], "start_s": round(start, 6),
                      "duration_s": round(end - start, 6), "fade_in_s": cue.get("fade_in_s", 0), "fade_out_s": cue.get("fade_out_s", 0),
                      "gain_db": cue.get("gain_db", -18), "duck_under_dialogue_db": cue.get("duck_under_dialogue_db", 0)})

    # Same character speaking over themself is a timing error.
    by_char: dict[str, list] = {}
    for c in clips:
        if c["kind"] == "dialogue":
            by_char.setdefault(c["character"], []).append(c)
    for ch, cs in by_char.items():
        cs.sort(key=lambda c: c["start_s"])
        for a, b in zip(cs, cs[1:]):
            if a["start_s"] + a["duration_s"] > b["start_s"] + 1e-6:
                errors.append(f"{ch}: lines {a['id']} and {b['id']} overlap")

    return {
        "schema": "kage.timeline/1", "project_id": project.production["project_id"],
        "fps": {"num": num, "den": den}, "total_frames": total_frames, "duration_s": round(total_frames / fps, 6),
        "shots": shots, "audio_clips": sorted(clips, key=lambda c: (c["start_s"], c["id"])), "subtitles": subtitles,
        "adjustments": adjustments, "errors": errors,
        "line_timing": {k: v[1] for k, v in durations.items()},
    }
