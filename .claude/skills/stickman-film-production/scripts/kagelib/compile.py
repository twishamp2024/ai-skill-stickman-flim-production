"""Compile a validated shot into the engine scene contract (kage.engine_scene v1).

The engine never sees story language: this step resolves marks, palette names,
character proportions, components, dialogue-driven gestures, mouth tracks and
semantic camera framing into explicit numbers.
"""
from __future__ import annotations

import copy

from .characters import engine_character, rig_measurements, standing_height
from .project import Project
from .util import KageError

ENGINE_SCENE_VERSION = "1.0"


def _resolve_prims(prims: list, palette: dict, where: str) -> list:
    out = []
    for p in prims:
        q = copy.deepcopy(p)
        for key in ("fill", "stroke"):
            if key in q:
                if q[key] not in palette:
                    raise KageError(f"{where}: color '{q[key]}' not in visual bible palette")
                q[key] = palette[q[key]]
        out.append(q)
    return out


def engine_prop(inst: dict, asset: dict, palette: dict, layer_default: str) -> dict:
    where = f"instance {inst['id']} (asset {inst['asset']})"
    prop = {
        "id": inst["id"], "asset": inst["asset"], "layer": inst.get("layer", layer_default), "z": inst.get("z", 0),
        "primitives": _resolve_prims(asset.get("primitives", []), palette, where),
        "important": inst.get("important", False),
    }
    if asset.get("parts"):
        prop["parts"] = [{**{k: v for k, v in part.items() if k != "primitives"},
                          "primitives": _resolve_prims(part["primitives"], palette, where)} for part in asset["parts"]]
    if "keys" in inst:
        prop["keys"] = [{**k, "scale": k.get("scale", inst.get("scale", 1))} for k in inst["keys"]]
    else:
        prop["position"] = inst.get("position", [0, 0])
        prop["scale"] = inst.get("scale", 1)
        prop["rotation"] = inst.get("rotation", 0)
    for key in ("velocity", "tile", "flip", "grip", "attach"):
        if key in inst:
            prop[key] = inst[key]
    if "grip" not in prop and "grip" in asset:
        prop["grip"] = asset["grip"]
    return prop


# ---------------------------------------------------------------- camera framing

def _subject_extent(project: Project, member: dict, location: dict, sid: str) -> dict:
    ch = project.characters[member["character"]]
    rig = rig_measurements(ch, project.bible)
    H = standing_height(rig)
    where = f"{sid}.cast[{ch['id']}]"
    xs = [project.resolve_mark(member["start"]["x"], location, 0, where)]
    postures = [member["start"].get("posture", "stand")]
    seat = project.resolve_mark(member["start"].get("seat_y", 0.45), location, 1, where)
    facing = 1 if member["start"].get("facing", "right") == "right" else -1
    for a in member.get("actions", []):
        if "to_x" in a:
            xs.append(project.resolve_mark(a["to_x"], location, 0, where))
        if a["type"] == "sit":
            postures.append("sit")
            seat = project.resolve_mark(a.get("seat_y", 0.45), location, 1, where)
    seated_top = seat + rig["line_width"] / 2 + rig["torso"] + rig["neck"] + 2 * rig["head_radius"]
    tops = [H if p == "stand" else seated_top for p in postures]
    return {"xmin": min(xs), "xmax": max(xs), "top": max(tops), "head_r": rig["head_radius"], "height": H,
            "seated": "sit" in postures and "stand" not in postures, "seat": seat, "facing": facing}


def active_camera(shot: dict, fmt: dict) -> dict:
    """Shot camera with the variant for this format's aspect merged in."""
    spec = dict(shot["camera"])
    variant = spec.pop("variants", {}).get(fmt["aspect"])
    if variant:
        spec.update(variant)
        if "keys" not in variant and "framing" in variant:
            spec.pop("keys", None)
    return spec


def compute_camera(project: Project, shot: dict, fmt: dict, duration: float) -> dict:
    spec = active_camera(shot, fmt)
    cam = {"projection": spec.get("projection", project.bible.get("camera_conventions", {}).get("default_projection", "orthographic"))}
    if spec.get("keys"):
        cam["keys"] = spec["keys"]
        return cam
    location = project.shot_location(shot["id"])
    cast = {m["character"]: m for m in shot.get("cast", [])}
    subjects = spec.get("subjects") or list(cast)
    if not subjects:
        raise KageError(f"{shot['id']}: camera framing '{spec.get('framing')}' needs subjects or explicit keys")
    ext = []
    for s in subjects:
        if s not in cast:
            raise KageError(f"{shot['id']}: camera subject '{s}' is not in the shot cast")
        ext.append(_subject_extent(project, cast[s], location, shot["id"]))
    aspect = fmt["width"] / fmt["height"]
    top = max(e["top"] for e in ext)
    hmax = max(e["height"] for e in ext)
    framing = spec.get("framing", "wide")
    head_r = max(e["head_r"] for e in ext)
    follow = spec.get("move", {}).get("type") == "follow"
    if framing == "wide":
        vis_h = top * (2.3 if follow else 1.75)  # following needs room for lag and companions
        cy = vis_h * 0.5 - 0.12 * vis_h
    elif framing == "medium":
        bottom = min(e["seat"] * 0.6 if e["seated"] else e["height"] * 0.35 for e in ext)
        vis_h = (top - bottom) * 1.6
        cy = bottom + 0.46 * vis_h  # headroom above the tallest subject
    elif framing == "close":
        head_y = top - head_r
        vis_h = max(7.5 * head_r, 6.5 * head_r / aspect)  # narrow formats keep head + lead room
        cy = head_y - 1.4 * head_r
    elif framing == "extreme_close":
        head_y = top - head_r
        vis_h = max(3.4 * head_r, 3.6 * head_r / aspect)
        cy = head_y
    else:
        raise KageError(f"{shot['id']}: framing '{framing}' requires explicit camera keys")
    xmin, xmax = min(e["xmin"] for e in ext), max(e["xmax"] for e in ext)
    move = spec.get("move", {"type": "static"})
    if move.get("type") != "follow":
        margin = max(0.45, 0.3 * hmax) if framing in ("wide", "medium") else 0.25 * hmax
        if framing in ("wide", "medium"):
            vis_h = max(vis_h, ((xmax - xmin) + 2 * margin) / aspect)
            if framing == "wide":
                cy = vis_h * 0.5 - 0.12 * vis_h
            else:
                cy = bottom + 0.46 * vis_h
    cx = (xmin + xmax) / 2
    if len(ext) == 1 and framing in ("close", "extreme_close", "medium"):
        cx += ext[0]["facing"] * 0.12 * vis_h * aspect  # lead room in the look direction
    angle = spec.get("angle", "eye")
    if angle == "high":
        cy += 0.12 * vis_h
    elif angle == "low":
        cy -= 0.12 * vis_h
    key0 = {"t": 0, "x": round(cx, 5), "y": round(cy, 5), "height": round(vis_h, 5)}
    keys = [key0]
    mtype = move.get("type", "static")
    if mtype == "zoom":
        keys.append({**key0, "t": duration, "height": round(vis_h * move["factor"], 5), "ease": move.get("ease", "in_out")})
    elif mtype == "pan":
        to_x = project.resolve_mark(move["to_x"], location, 0, shot["id"]) if "to_x" in move else cx + move.get("by_x", 0)
        keys.append({**key0, "t": duration, "x": round(to_x, 5), "ease": move.get("ease", "in_out")})
    elif mtype == "follow":
        cam["follow"] = {"character": move["character"], "offset": [move.get("offset_x", 0), 0], "smoothing": move.get("smoothing", 0.5), "axes": "x"}
    elif mtype != "static":
        raise KageError(f"{shot['id']}: unknown camera move '{mtype}'")
    cam["keys"] = keys
    return cam


# ---------------------------------------------------------------- speech animation

def mouth_track(line_infos: list[tuple[float, dict]], mode: str, fps: float) -> list:
    """Mouth keys from audio amplitude envelopes (simple mode).

    line_infos: (offset_s within shot, line audio info with 'envelope' [rms per frame]
    or None when only an estimate exists).
    """
    track = [[0.0, "closed" if mode == "simple" else "rest"]]
    for offset, info in sorted(line_infos, key=lambda x: x[0]):
        env = info.get("envelope") if info else None
        if env:
            peak = max(env) or 1.0
            last = None
            for i, v in enumerate(env):
                r = v / peak
                state = "wide" if r > 0.62 else ("open" if r > 0.22 else "closed")
                if state != last:
                    track.append([round(offset + i / fps, 4), state])
                    last = state
            track.append([round(offset + len(env) / fps, 4), "closed"])
        else:  # estimated timing: neutral flap at ~4 Hz, clearly marked as an estimate upstream
            d = info["duration_s"] if info else 1.0
            n = int(d / 0.125)
            for i in range(n):
                track.append([round(offset + i * 0.125, 4), "open" if i % 2 == 0 else "closed"])
            track.append([round(offset + n * 0.125, 4), "closed"])
    track.sort(key=lambda k: k[0])
    dedup = []
    for k in track:
        if dedup and abs(dedup[-1][0] - k[0]) < 1e-9:
            dedup[-1] = k
        else:
            dedup.append(k)
    return dedup


# ---------------------------------------------------------------- shot compilation

def compile_shot(project: Project, shot_id: str, fmt: dict, timeline: dict, line_audio: dict | None = None,
                 *, fps_override: tuple[int, int] | None = None, size_override: tuple[int, int] | None = None) -> dict:
    line_audio = line_audio or {}
    shot = project.shots[shot_id]
    tl = next(s for s in timeline["shots"] if s["id"] == shot_id)
    location = project.shot_location(shot_id)
    palette = project.bible["palette"]
    num, den = fps_override or project.fps
    fps = num / den
    duration = tl["duration_s"]
    width, height = size_override or (fmt["width"], fmt["height"])

    layers = location["layers"]
    layer_ids = {l["id"] for l in layers}
    props = []
    for inst in location.get("instances", []) + shot.get("props", []):
        asset = project.assets.get(inst["asset"])
        if asset is None:
            raise KageError(f"{shot_id}: unknown asset '{inst['asset']}' (instance {inst['id']})")
        if inst.get("layer") and inst["layer"] not in layer_ids:
            raise KageError(f"{shot_id}: instance {inst['id']} uses unknown layer '{inst['layer']}'")
        props.append(engine_prop(inst, asset, palette, layers[0]["id"]))

    speech = project.dialogue.get("speech_animation", {})
    actor_layer = "actors" if "actors" in layer_ids else layers[-1]["id"]
    characters = []
    for member in shot.get("cast", []):
        ch = project.characters[member["character"]]
        where = f"{shot_id}.cast[{ch['id']}]"
        start = dict(member["start"])
        start["x"] = project.resolve_mark(start["x"], location, 0, where)
        if "seat_y" in start:
            start["seat_y"] = project.resolve_mark(start["seat_y"], location, 1, where)
        actions = []
        for a in member.get("actions", []):
            b = dict(a)
            if "to_x" in b:
                b["to_x"] = project.resolve_mark(b["to_x"], location, 0, where)
            if "seat_y" in b:
                b["seat_y"] = project.resolve_mark(b["seat_y"], location, 1, where)
            actions.append(b)
        infos = []
        for ln in project.lines_for_shot(shot_id):
            if ln["character"] != ch["id"]:
                continue
            info = line_audio.get(ln["id"]) or {"duration_s": ln["estimated_s"]}
            infos.append((ln["offset_s"], info if "envelope" in info else {"duration_s": info["duration_s"]}))
            if ln.get("delivery", {}).get("gesture", "talk") == "talk" and speech.get("gesture", True):
                actions.append({"type": "talk", "start": ln["offset_s"], "duration": max(0.3, info["duration_s"]),
                                "side": "r", "intensity": 0.7, "blend_in": 0.2, "blend_out": 0.35})
        mode = ch.get("face", {}).get("mouth_mode", speech.get("default_mode", "simple"))
        if mode == "viseme":
            mode = "simple"  # viseme alignment is not produced by the configured provider; documented fallback
        mouth = {"mode": mode, "track": mouth_track(infos, mode, fps)} if (mode != "none" and infos) else None
        characters.append(engine_character(ch, project.bible, layer=member.get("layer", actor_layer), start=start,
                                           actions=actions, variant_id=member.get("variant"), mouth=mouth, z=member.get("z", 0)))

    camera = compute_camera(project, shot, fmt, duration)
    return {
        "kage_scene_version": ENGINE_SCENE_VERSION,
        "shot_id": shot_id,
        "format": fmt["id"],
        "output": {"width": width, "height": height, "fps": {"num": num, "den": den}, "frame_count": max(1, round(duration * fps)),
                   "background": palette[location["background"]],
                   "safe_area": project.bible.get("camera_conventions", {}).get("safe_area", 0.9)},
        "layers": layers,
        "camera": camera,
        "props": props,
        "characters": characters,
    }
