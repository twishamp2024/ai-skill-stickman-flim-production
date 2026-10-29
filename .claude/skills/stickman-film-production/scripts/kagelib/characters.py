"""Character identity -> engine rig + bound appearance components.

Gender identity and presentation are recorded on the profile for story and
review purposes only: nothing in this module reads them. Geometry comes from
age profile, height, build and explicitly chosen components.
"""
from __future__ import annotations

import copy
import math

from .util import KageError, LIBRARY_DIR, load_json

_PROFILES = None
_LIBRARY = None


def age_profiles() -> dict:
    global _PROFILES
    if _PROFILES is None:
        _PROFILES = load_json(LIBRARY_DIR / "age_profiles.json")
    return _PROFILES


def component_library() -> dict:
    global _LIBRARY
    if _LIBRARY is None:
        _LIBRARY = load_json(LIBRARY_DIR / "components.json")
    return _LIBRARY


def resolve_color(name, palette: dict, where: str) -> str:
    """Palette color name -> '#RRGGBB'. Raw hex is rejected to keep palettes restrained."""
    if not isinstance(name, str) or name not in palette:
        raise KageError(f"{where}: color '{name}' is not in the visual bible palette")
    return palette[name]


def rig_measurements(character: dict, bible: dict) -> dict:
    """Numeric rig (metres) from the age profile, height and overrides."""
    body = character["body"]
    cat = character["identity"]["age_category"]
    prof = dict(age_profiles()["profiles"].get(cat) or {})
    if not prof:
        raise KageError(f"{character['id']}: unknown age_category '{cat}'")
    for k, mult in body.get("proportion_overrides", {}).items():
        if k not in prof or not isinstance(prof[k], (int, float)):
            raise KageError(f"{character['id']}: cannot override unknown proportion '{k}'")
        prof[k] = prof[k] * mult
    H = body.get("height_m", prof["default_height_m"])
    head_d = H / prof["heads_tall"]
    neck = prof["neck_fraction"] * H
    leg_total = prof["leg_fraction"] * H
    ankle = 0.028 * H
    torso = H - head_d - neck - leg_total
    if torso <= 0.1 * H:
        raise KageError(f"{character['id']}: proportions leave no torso (check overrides)")
    arm = prof["arm_fraction"] * H
    legs = leg_total - ankle
    motion = character.get("motion", {})
    rig = {
        "head_radius": head_d / 2, "neck": neck, "torso": torso, "shoulder_drop": 0.08 * torso,
        "upper_arm": 0.53 * arm, "forearm": 0.47 * arm, "hand_radius": 0.02 * H,
        "thigh": 0.51 * legs, "shin": 0.49 * legs, "foot": 0.075 * H, "ankle_height": ankle,
        "line_width": bible["style"]["body_line_width"] * prof["line_scale"],
        "stride": prof["stride_legs"] * legs, "run_stride": prof["run_stride_legs"] * legs,
        "arm_swing": prof["arm_swing"], "energy": prof["energy"] * motion.get("energy", 1.0),
        "posture_spine": prof["posture_spine"] + motion.get("posture_spine", 0),
        "posture_neck": prof["posture_neck"] + motion.get("posture_neck", 0),
    }
    return {k: round(v, 6) if isinstance(v, float) else v for k, v in rig.items()}


def standing_height(rig: dict) -> float:
    return rig["thigh"] + rig["shin"] + rig["ankle_height"] + rig["torso"] + rig["neck"] + 2 * rig["head_radius"]


def _expand_points(points, scale, sx):
    out = []
    for p in points:
        if isinstance(p, dict) and "arc" in p:
            cx, cy, r, a0, a1, n = p["arc"]
            for i in range(int(n)):
                a = math.radians(a0 + (a1 - a0) * i / (n - 1))
                out.append([round((cx + r * math.cos(a)) * scale * sx, 6), round((cy + r * math.sin(a)) * scale, 6)])
        elif isinstance(p, dict):
            q = dict(p)
            q["p"] = [round(p["p"][0] * scale * sx, 6), round(p["p"][1] * scale, 6)]
            out.append(q)
        else:
            out.append([round(p[0] * scale * sx, 6), round(p[1] * scale, 6)])
    return out


def active_components(character: dict, variant_id: str | None) -> list[dict]:
    comps = [dict(c) for c in character["components"]]
    if variant_id:
        variant = next((v for v in character.get("variants", []) if v["id"] == variant_id), None)
        if variant is None:
            raise KageError(f"{character['id']}: unknown variant '{variant_id}'")
        removed = set(variant.get("remove", []))
        comps = [c for c in comps if c["id"] not in removed] + [dict(c) for c in variant.get("add", [])]
    return comps


def expand_components(character: dict, bible: dict, rig: dict, variant_id: str | None = None) -> list[dict]:
    lib = component_library()["components"]
    palette = bible["palette"]
    slots = character["palette"]
    build = age_profiles()["builds"].get(character["body"].get("build", "average"))
    if build is None:
        raise KageError(f"{character['id']}: unknown build '{character['body'].get('build')}'")
    units = {"head": rig["head_radius"], "torso": rig["torso"], "leg": rig["thigh"] + rig["shin"], None: 1.0}
    out = []
    for comp in active_components(character, variant_id):
        cdef = lib.get(comp["id"])
        if cdef is None:
            raise KageError(f"{character['id']}: unknown component '{comp['id']}'")
        sx = build if cdef.get("scale_x_by_build") else 1.0
        for prim in cdef["primitives"]:
            p = copy.deepcopy(prim)
            scale = units[p.pop("unit", None)]
            if "points" in p:
                p["points"] = _expand_points(p["points"], scale, sx)
            if "center" in p:
                p["center"] = [round(p["center"][0] * scale * sx, 6), round(p["center"][1] * scale, 6)]
            for k in ("radius", "rx", "ry"):
                if k in p:
                    p[k] = round(p[k] * scale, 6)
            if "stroke_width_lw" in p:
                p["stroke_width"] = round(p.pop("stroke_width_lw") * rig["line_width"], 6)
            for key in ("fill", "stroke"):
                if key in p and isinstance(p[key], str) and p[key].startswith("$"):
                    slot = p[key][1:]
                    color_name = comp.get("colors", {}).get(slot, slots.get(slot))
                    if color_name is None:
                        raise KageError(f"{character['id']}: component '{comp['id']}' needs palette slot '{slot}'")
                    p[key] = resolve_color(color_name, palette, f"{character['id']}.palette.{slot}")
            p["z"] = p.get("z", 0) + comp.get("z_offset", 0)
            p["tag"] = f"component:{comp['id']}"
            out.append(p)
    return out


def engine_character(character: dict, bible: dict, *, layer: str, start: dict, actions: list,
                     variant_id: str | None = None, mouth: dict | None = None, z: float = 0) -> dict:
    rig = rig_measurements(character, bible)
    slots = character["palette"]
    palette = bible["palette"]
    face = character.get("face", {})
    ec = {
        "id": character["id"], "layer": layer, "rig": rig,
        "line_color": resolve_color(slots["line"], palette, f"{character['id']}.palette.line"),
        "head_fill": resolve_color(slots["head_fill"], palette, f"{character['id']}.palette.head_fill"),
        "face": face.get("enabled", True), "face_style": face.get("style", "profile"),
        "components": expand_components(character, bible, rig, variant_id),
        "start": start, "actions": actions, "z": z,
    }
    if mouth:
        ec["mouth"] = mouth
    return ec


def lineup_scene(characters: list[dict], bible: dict, *, width=1280, height=720, solo: bool = False) -> dict:
    """Identity lineup: all characters standing side by side, facing right."""
    rigs = [rig_measurements(c, bible) for c in characters]
    heights = [standing_height(r) for r in rigs]
    hmax = max(heights)
    spacing = max(0.75, 0.55 * hmax)
    xs = [(i - (len(characters) - 1) / 2) * spacing for i in range(len(characters))]
    if solo:
        xs = [0.0] * len(characters)
    view_h = hmax * 1.3
    view_h = max(view_h, (spacing * len(characters) + 0.6) / (width / height)) if not solo else view_h
    chars = [engine_character(c, bible, layer="actors", start={"x": x, "facing": "right"}, actions=[])
             for c, x in zip(characters, xs)]
    bg = bible["palette"].get(bible["style"].get("lineup_background", ""), "#f4f1ea")
    return {
        "kage_scene_version": "1.0", "shot_id": "lineup",
        "output": {"width": width, "height": height, "fps": {"num": 24, "den": 1}, "frame_count": 1, "background": bg},
        "layers": [{"id": "actors", "order": 0}],
        "camera": {"keys": [{"t": 0, "x": 0, "y": view_h / 2 - 0.05 * view_h, "height": view_h}]},
        "characters": chars,
    }


def silhouette_metrics(masks: dict[str, list[bool]]) -> list[dict]:
    """Pairwise IoU of equal-size silhouette masks."""
    ids = sorted(masks)
    out = []
    for i, a in enumerate(ids):
        for b in ids[i + 1:]:
            ma, mb = masks[a], masks[b]
            inter = sum(1 for x, y in zip(ma, mb) if x and y)
            union = sum(1 for x, y in zip(ma, mb) if x or y)
            out.append({"a": a, "b": b, "iou": round(inter / union, 4) if union else 1.0})
    return out


def feature_signature(character: dict, bible: dict) -> dict:
    rig = rig_measurements(character, bible)
    return {
        "height_m": round(standing_height(rig), 3),
        "head_ratio": round(2 * rig["head_radius"] / standing_height(rig), 3),
        "components": sorted(c["id"] for c in character["components"]),
        "palette": {k: v for k, v in sorted(character["palette"].items())},
        "build": character["body"].get("build", "average"),
    }
