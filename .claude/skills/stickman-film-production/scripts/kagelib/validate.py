"""Planning QA: schema + semantic validation of a project before any rendering."""
from __future__ import annotations

import re
from pathlib import Path

from . import schema
from .characters import age_profiles, component_library, rig_measurements, standing_height
from .project import ID_PREFIX, Project
from .timeline import build_timeline
from .util import KageError, load_json, sha256_file

SUPPORTED_ASPECTS = {"16:9": (16, 9), "9:16": (9, 16), "4:5": (4, 5), "5:4": (5, 4), "1:1": (1, 1)}
ACTION_TYPES = {"walk", "run", "turn", "sit", "stand", "fall", "recover", "gesture", "talk", "look", "lean", "reach",
                "pick_up", "put_down", "keyframes", "pose"}
RENDERABLE = {"approved", "locked"}

DOC_SCHEMAS = {
    "production": "production.schema.json", "brief": "story_brief.schema.json", "bible": "visual_bible.schema.json",
    "registry": "asset_registry.schema.json", "dialogue": "dialogue.schema.json", "audio_plan": "audio_plan.schema.json",
    "render": "render_config.schema.json",
}
DIR_SCHEMAS = {"characters": "character.schema.json", "voices": "voice.schema.json", "locations": "location.schema.json",
               "scenes": "scene.schema.json", "shots": "shot.schema.json"}


class Report:
    def __init__(self, stage: str):
        self.stage = stage
        self.issues: list[dict] = []
        self.checks: dict[str, int] = {}

    def check(self, category: str):
        self.checks[category] = self.checks.get(category, 0) + 1

    def error(self, category: str, ref: str, msg: str, fix: str | None = None):
        self.issues.append({"severity": "error", "category": category, "ref": ref, "message": msg, **({"suggested_fix": fix} if fix else {})})

    def warn(self, category: str, ref: str, msg: str, fix: str | None = None):
        self.issues.append({"severity": "warning", "category": category, "ref": ref, "message": msg, **({"suggested_fix": fix} if fix else {})})

    @property
    def errors(self):
        return [i for i in self.issues if i["severity"] == "error"]

    def to_json(self, project_id: str, extra: dict | None = None) -> dict:
        return {"schema": "kage.qa_report/1", "project_id": project_id, "stage": self.stage,
                "passed": not self.errors, "checks_run": self.checks,
                "error_count": len(self.errors), "warning_count": len(self.issues) - len(self.errors),
                "issues": self.issues, **(extra or {})}


def _colors_in(prims: list, palette: dict, ref: str, rep: Report):
    for p in prims:
        for key in ("fill", "stroke"):
            if key in p:
                rep.check("palette")
                if p[key] not in palette:
                    rep.error("palette", ref, f"color '{p[key]}' is not in the visual bible palette",
                              "add it to bible.palette (versioned change) or use an existing color name")
        if p.get("stroke") and not p.get("stroke_width"):
            rep.error("geometry", ref, "stroke without stroke_width")


def validate_project(project: Project, *, for_render: bool = True, approvals_path: Path | None = None) -> Report:
    rep = Report("planning")
    store = schema.SchemaStore()
    pid = project.production["project_id"]

    # ---- schemas
    for key, sname in DOC_SCHEMAS.items():
        if key == "render" and not project.render:
            continue
        doc = getattr(project, {"brief": "brief", "bible": "bible", "registry": "registry"}.get(key, key))
        rep.check("schema")
        for e in schema.validate(doc, sname, store):
            rep.error("schema", str(project.files[key].relative_to(project.root)), e)
    for dirname, sname in DIR_SCHEMAS.items():
        for doc_id, doc in getattr(project, dirname).items():
            rep.check("schema")
            for e in schema.validate(doc, sname, store):
                rep.error("schema", f"{dirname}/{doc_id}.json", e)

    # ---- ids
    for kind, coll in (("character", project.characters), ("voice", project.voices), ("location", project.locations),
                       ("scene", project.scenes), ("shot", project.shots), ("asset", project.assets), ("line", project.lines)):
        for doc_id in coll:
            rep.check("ids")
            if not re.fullmatch(ID_PREFIX[kind] + r"[a-z0-9_]+", doc_id):
                rep.error("ids", doc_id, f"{kind} id must match {ID_PREFIX[kind]}[a-z0-9_]+")

    # ---- formats / aspect ratios
    for fmt in project.formats():
        rep.check("format")
        ref = f"formats.{fmt['id']}"
        if fmt["aspect"] not in SUPPORTED_ASPECTS:
            rep.error("format", ref, f"aspect {fmt['aspect']} unsupported (supported: {', '.join(SUPPORTED_ASPECTS)})")
            continue
        a, b = SUPPORTED_ASPECTS[fmt["aspect"]]
        if abs(fmt["width"] / fmt["height"] - a / b) > 0.005:
            rep.error("format", ref, f"{fmt['width']}x{fmt['height']} does not match aspect {fmt['aspect']}",
                      f"use e.g. {fmt['height'] * a // b // 2 * 2}x{fmt['height']}")
        if fmt["width"] % 2 or fmt["height"] % 2:
            rep.error("format", ref, "width and height must be even for yuv420p delivery")
    if sum(1 for f in project.formats() if f.get("primary")) > 1:
        rep.error("format", "formats", "more than one primary format")

    # ---- story coverage
    events = {e["id"]: e for e in project.brief["events"]}
    covered = {}
    for sid, shot in project.shots.items():
        for ev in shot.get("covers", []):
            rep.check("coverage")
            if ev not in events:
                rep.error("coverage", sid, f"covers unknown event '{ev}'")
            covered.setdefault(ev, []).append(sid)
    for ev_id, ev in events.items():
        rep.check("coverage")
        if ev.get("importance") == "essential" and ev_id not in covered:
            rep.error("coverage", ev_id, f"essential story event not covered by any shot: {ev['summary']}",
                      "add it to a shot's 'covers' or add a shot")
    src = project.root / project.brief.get("source", "story/story.md")
    if not src.exists():
        rep.error("story", "brief.source", f"original story file {src.name} missing")

    # ---- sequence / scenes / shots structure
    seen_shots = {}
    for scn in project.production["sequence"]:
        rep.check("structure")
        if scn not in project.scenes:
            rep.error("structure", "production.sequence", f"unknown scene '{scn}'")
            continue
        scene = project.scenes[scn]
        if scene["location"] not in project.locations:
            rep.error("structure", scn, f"unknown location '{scene['location']}'")
        for sid in scene["shots"]:
            if sid not in project.shots:
                rep.error("structure", scn, f"unknown shot '{sid}'")
            elif project.shots[sid]["scene"] != scn:
                rep.error("structure", sid, f"shot.scene is '{project.shots[sid]['scene']}' but listed in {scn}")
            if sid in seen_shots:
                rep.error("structure", sid, f"shot appears in both {seen_shots[sid]} and {scn}")
            seen_shots[sid] = scn
    for sid in project.shots:
        if sid not in seen_shots:
            rep.warn("structure", sid, "shot is not part of the production sequence (will not render)")

    palette = project.bible["palette"]
    lib = component_library()["components"]
    profiles = age_profiles()["profiles"]

    # ---- characters & voices
    for cid, ch in project.characters.items():
        rep.check("character")
        if ch["identity"]["age_category"] not in profiles:
            rep.error("character", cid, f"unknown age_category '{ch['identity']['age_category']}'")
        for slot, color in ch["palette"].items():
            if color not in palette:
                rep.error("palette", cid, f"palette slot '{slot}' uses '{color}' which is not in the bible palette")
        for comp in ch["components"] + [c for v in ch.get("variants", []) for c in v.get("add", [])]:
            cdef = lib.get(comp["id"])
            if not cdef:
                rep.error("character", cid, f"unknown component '{comp['id']}'")
                continue
            for prim in cdef["primitives"]:
                for key in ("fill", "stroke"):
                    v = prim.get(key)
                    if isinstance(v, str) and v.startswith("$") and v[1:] not in ch["palette"] and v[1:] not in comp.get("colors", {}):
                        rep.error("character", cid, f"component '{comp['id']}' needs palette slot '{v[1:]}'")
        cats = [lib[c["id"]]["category"] for c in ch["components"] if c["id"] in lib]
        for cat in ("hair", "top"):
            if cats.count(cat) > 1:
                rep.error("character", cid, f"more than one '{cat}' component")
        try:
            h = standing_height(rig_measurements(ch, project.bible))
            if abs(h - ch["body"].get("height_m", h)) > 0.01:
                rep.error("character", cid, f"rig height {h:.3f} m disagrees with height_m")
        except KageError as e:
            rep.error("character", cid, str(e))
        vid = ch.get("voice")
        if vid:
            if vid not in project.voices:
                rep.error("voice", cid, f"voice profile '{vid}' missing")
            elif project.voices[vid]["character"] != cid:
                rep.error("voice", vid, f"voice profile belongs to {project.voices[vid]['character']}, not {cid}")
        if for_render and ch.get("status") not in RENDERABLE:
            rep.error("approval", cid, f"character status '{ch.get('status')}' is not approved for rendering")
    for vid, v in project.voices.items():
        rep.check("voice")
        if v["character"] not in project.characters:
            rep.error("voice", vid, f"voice references unknown character '{v['character']}'")

    # ---- dialogue
    for ln in project.dialogue["lines"]:
        rep.check("dialogue")
        ref = ln["id"]
        ch = project.characters.get(ln["character"])
        if not ch:
            rep.error("dialogue", ref, f"unknown character '{ln['character']}'")
            continue
        if ln["voice"] != ch.get("voice") and not ln.get("voice_override_reason"):
            rep.error("dialogue", ref, f"line uses voice '{ln['voice']}' but {ch['id']} is locked to '{ch.get('voice')}'",
                      "use the character's voice or set voice_override_reason (story-required change)")
        if ln["shot"] not in project.shots:
            rep.error("dialogue", ref, f"unknown shot '{ln['shot']}'")
            continue
        shot = project.shots[ln["shot"]]
        if ln["id"] not in shot.get("dialogue", []):
            rep.error("dialogue", ref, f"shot {ln['shot']} does not list this line in 'dialogue'")
        if ln["character"] not in {m["character"] for m in shot.get("cast", [])} and not ln.get("offscreen"):
            rep.error("dialogue", ref, f"speaker {ln['character']} is not in {ln['shot']}'s cast (mark the line offscreen if intended)")
        if not ln["text"].strip():
            rep.error("dialogue", ref, "empty text")
    for sid, shot in project.shots.items():
        for lid in shot.get("dialogue", []):
            if lid not in project.lines:
                rep.error("dialogue", sid, f"lists unknown line '{lid}'")

    # ---- assets & locations
    for aid, asset in project.assets.items():
        rep.check("asset")
        _colors_in(asset.get("primitives", []), palette, aid, rep)
        for part in asset.get("parts", []):
            _colors_in(part.get("primitives", []), palette, f"{aid}.{part['id']}", rep)
        for dep in asset.get("composed_of", []):
            if dep not in project.assets:
                rep.error("asset", aid, f"composed_of references unknown asset '{dep}'")
        if for_render and asset.get("status") not in RENDERABLE:
            rep.error("approval", aid, f"asset status '{asset.get('status')}' is not approved for rendering")
    for lid, loc in project.locations.items():
        rep.check("location")
        layer_ids = [l["id"] for l in loc["layers"]]
        if len(set(layer_ids)) != len(layer_ids):
            rep.error("location", lid, "duplicate layer ids")
        if loc["background"] not in palette:
            rep.error("palette", lid, f"background '{loc['background']}' not in palette")
        for inst in loc.get("instances", []):
            if inst["asset"] not in project.assets:
                rep.error("location", lid, f"instance {inst['id']} references unknown asset '{inst['asset']}'")
            if inst.get("layer", layer_ids[0]) not in layer_ids:
                rep.error("location", lid, f"instance {inst['id']} uses unknown layer '{inst.get('layer')}'")
            if inst.get("tile") and not project.assets.get(inst["asset"], {}).get("tileable_period"):
                rep.warn("location", lid, f"instance {inst['id']} tiles an asset that does not declare tileable_period (seams likely)")
        if loc.get("ambience") and loc["ambience"] not in project.audio_plan.get("assets", {}):
            rep.error("audio", lid, f"ambience '{loc['ambience']}' is not in the audio plan")
        used = set()
        for inst in loc.get("instances", []):
            a = project.assets.get(inst["asset"], {})
            for p in a.get("primitives", []) + [q for part in a.get("parts", []) for q in part.get("primitives", [])]:
                used |= {p.get("fill"), p.get("stroke")} - {None}
        limit = project.bible["style"].get("max_palette_colors_per_location")
        if limit and len(used) > limit:
            rep.warn("palette", lid, f"location uses {len(used)} colors (> {limit} in the style guide)")

    # ---- shots
    for sid, shot in project.shots.items():
        rep.check("shot")
        if shot["scene"] not in project.scenes:
            rep.error("shot", sid, f"unknown scene '{shot['scene']}'")
            continue
        loc = project.shot_location(sid)
        layer_ids = {l["id"] for l in loc["layers"]}
        cast_ids = [m["character"] for m in shot.get("cast", [])]
        if len(set(cast_ids)) != len(cast_ids):
            rep.error("shot", sid, "character listed twice in cast")
        for m in shot.get("cast", []):
            where = f"{sid}.cast[{m['character']}]"
            if m["character"] not in project.characters:
                rep.error("shot", where, "unknown character")
                continue
            if m.get("variant") and m["variant"] not in {v["id"] for v in project.characters[m["character"]].get("variants", [])}:
                rep.error("shot", where, f"unknown variant '{m['variant']}'")
            try:
                project.resolve_mark(m["start"]["x"], loc, 0, where)
            except KageError as e:
                rep.error("shot", where, str(e))
            last_end = {}
            for i, a in enumerate(m.get("actions", [])):
                aref = f"{where}.actions[{i}]"
                if a["type"] not in ACTION_TYPES:
                    rep.error("motion", aref, f"unknown action type '{a['type']}'")
                if a["start"] < 0 or a["duration"] <= 0:
                    rep.error("motion", aref, "start must be >= 0 and duration > 0")
                if a["start"] + a["duration"] > shot.get("timing", {}).get("max_s", shot["duration_s"]) + 1e-6 and not a.get("hold"):
                    rep.warn("motion", aref, "action extends past the end of the shot")
                for key in ("to_x", "seat_y"):
                    if key in a:
                        try:
                            project.resolve_mark(a[key], loc, 0 if key == "to_x" else 1, aref)
                        except KageError as e:
                            rep.error("motion", aref, str(e))
                tgt = a.get("target")
                if isinstance(tgt, dict) and tgt.get("character") not in cast_ids:
                    rep.error("motion", aref, f"target character '{tgt.get('character')}' is not in the shot cast")
                if a["type"] in ("walk", "run"):
                    prev = last_end.get("loco")
                    if prev is not None and a["start"] < prev - 1e-6:
                        rep.error("motion", aref, "overlapping walk/run actions for the same character")
                    last_end["loco"] = a["start"] + a["duration"]
                if a.get("prop") and a["prop"] not in {p["id"] for p in shot.get("props", [])}:
                    rep.error("motion", aref, f"prop '{a['prop']}' is not a shot prop instance")
        for p in shot.get("props", []):
            if p["asset"] not in project.assets:
                rep.error("shot", sid, f"prop {p['id']} references unknown asset '{p['asset']}'")
            if p.get("layer") and p["layer"] not in layer_ids:
                rep.error("shot", sid, f"prop {p['id']} uses unknown layer '{p['layer']}'")
        cam = shot["camera"]
        for s in cam.get("subjects", []):
            if s not in cast_ids:
                rep.error("camera", sid, f"camera subject '{s}' not in cast")
        for asp, var in cam.get("variants", {}).items():
            if asp not in SUPPORTED_ASPECTS:
                rep.error("camera", sid, f"variant for unsupported aspect '{asp}'")
            for s in var.get("subjects", []):
                if s not in cast_ids:
                    rep.error("camera", sid, f"{asp} variant subject '{s}' not in cast")
        if cam.get("framing", "custom") == "custom" and not cam.get("keys"):
            rep.error("camera", sid, "custom framing requires explicit keys")
        for t in ("transition_in", "transition_out"):
            tr = shot.get(t, {"type": "cut"})
            if tr["type"] != "cut" and tr.get("duration_s", 0) * 2 > shot["duration_s"]:
                rep.error("timing", sid, f"{t} lasts more than half the shot")
        if for_render and shot.get("status") not in RENDERABLE:
            rep.error("approval", sid, f"shot status '{shot.get('status')}' is not approved for rendering")

    # ---- audio plan
    plan = project.audio_plan
    for aid, a in plan.get("assets", {}).items():
        rep.check("audio")
        if not re.fullmatch(r"aud_[a-z0-9_]+", aid):
            rep.error("ids", aid, "audio asset id must match aud_[a-z0-9_]+")
        if not a.get("license"):
            rep.error("licensing", aid, "audio asset has no license/provenance record", "record the license or generate the asset")
    for ev in plan.get("events", []):
        if ev["asset"] not in plan.get("assets", {}):
            rep.error("audio", ev["id"], f"unknown audio asset '{ev['asset']}'")
        if ev["anchor"]["shot"] not in project.shots:
            rep.error("audio", ev["id"], f"anchor references unknown shot '{ev['anchor']['shot']}'")
    for rule in plan.get("animation_sfx", []):
        for s in rule.get("shots", []):
            if s not in project.shots:
                rep.error("audio", "animation_sfx", f"unknown shot '{s}'")
        if rule["asset"] not in plan.get("assets", {}):
            rep.error("audio", "animation_sfx", f"unknown audio asset '{rule['asset']}'")
    for cue in plan.get("music", []):
        if cue["asset"] not in plan.get("assets", {}):
            rep.error("audio", cue["id"], f"unknown audio asset '{cue['asset']}'")

    # ---- timeline (estimated timing at planning stage)
    try:
        tl = build_timeline(project)
        rep.check("timing")
        for e in tl["errors"]:
            rep.error("timing", "timeline", e)
        target = project.production.get("target_duration_s")
        if target and abs(tl["duration_s"] - target) > max(2.0, 0.15 * target):
            rep.warn("timing", "timeline", f"estimated duration {tl['duration_s']:.1f}s differs from target {target}s")
    except KageError as e:
        rep.error("timing", "timeline", str(e))

    # ---- approval locks: approved content must not change without a version bump
    if approvals_path and approvals_path.exists():
        approvals = load_json(approvals_path)
        for key, rec in approvals.get("records", {}).items():
            if key not in project.files:
                continue
            rep.check("approval")
            cur = sha256_file(project.files[key])
            if cur != rec["sha256"]:
                ver = _version_of(project, key)
                if ver == rec.get("version"):
                    rep.error("approval", key, f"approved content changed without a version bump (still {ver})",
                              "bump the version and re-approve with `kage.py approve`, or revert the change")
                else:
                    rep.warn("approval", key, f"version {rec.get('version')} -> {ver} is not yet re-approved")
    return rep


def _version_of(project: Project, key: str):
    path = project.files[key]
    try:
        return load_json(path).get("version")
    except KageError:
        return None
