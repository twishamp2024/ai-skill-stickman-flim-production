"""Film project model: canonical files, ID index and dependency tracking."""
from __future__ import annotations

from pathlib import Path

from .util import KageError, load_json, sha256_file

ID_PREFIX = {
    "character": "chr_", "voice": "voc_", "asset": "ast_", "location": "loc_", "scene": "scn_", "shot": "shot_",
    "line": "line_", "audio": "aud_", "event": "evt_", "cue": "cue_",
}


class Project:
    def __init__(self, root: Path):
        self.root = Path(root).resolve()
        if not (self.root / "production.json").exists():
            raise KageError(f"{self.root}: not a Kage project (production.json missing)")
        self.files: dict[str, Path] = {}
        self.production = self._load("production.json", "production")
        self.brief = self._load("story/brief.json", "brief")
        self.bible = self._load("bible/visual_bible.json", "bible")
        self.registry = self._load("assets/registry.json", "registry")
        self.dialogue = self._load("audio/dialogue.json", "dialogue")
        self.audio_plan = self._load("audio/plan.json", "audio_plan")
        self.render = self._load("render.json", "render") if (self.root / "render.json").exists() else {}
        self.characters = self._load_dir("characters")
        self.voices = self._load_dir("voices")
        self.locations = self._load_dir("locations")
        self.scenes = self._load_dir("scenes")
        self.shots = self._load_dir("shots")
        self.assets = self.registry.get("assets", {})
        self.lines = {ln["id"]: ln for ln in self.dialogue.get("lines", [])}

    # ------------------------------------------------------------------ loading
    def _load(self, rel_path: str, key: str):
        path = self.root / rel_path
        self.files[key] = path
        return load_json(path)

    def _load_dir(self, name: str) -> dict:
        out = {}
        for path in sorted((self.root / name).glob("*.json")):
            doc = load_json(path)
            doc_id = doc.get("id")
            if not doc_id:
                raise KageError(f"{path}: missing 'id'")
            if path.stem != doc_id:
                raise KageError(f"{path}: file name must equal id '{doc_id}'")
            out[doc_id] = doc
            self.files[doc_id] = path
        return out

    # ------------------------------------------------------------------ paths
    @property
    def build(self) -> Path:
        return self.root / "build"

    @property
    def output(self) -> Path:
        return self.root / "output"

    @property
    def qa_dir(self) -> Path:
        return self.root / "qa"

    @property
    def fps(self) -> tuple[int, int]:
        f = self.production["fps"]
        return int(f["num"]), int(f.get("den", 1))

    @property
    def fps_value(self) -> float:
        n, d = self.fps
        return n / d

    def formats(self) -> list[dict]:
        return self.production["formats"]

    def primary_format(self) -> dict:
        return next((f for f in self.formats() if f.get("primary")), self.formats()[0])

    # ------------------------------------------------------------------ queries
    def shot_order(self) -> list[str]:
        order = []
        for scn in self.production["sequence"]:
            if scn not in self.scenes:
                raise KageError(f"production.sequence references unknown scene '{scn}'")
            order.extend(self.scenes[scn]["shots"])
        return order

    def shot_location(self, shot_id: str) -> dict:
        return self.locations[self.scenes[self.shots[shot_id]["scene"]]["location"]]

    def resolve_mark(self, value, location: dict, axis: int, where: str):
        """'@mark' -> coordinate from the location's marks; numbers pass through."""
        if isinstance(value, str) and value.startswith("@"):
            mark = location.get("marks", {}).get(value[1:])
            if mark is None:
                raise KageError(f"{where}: unknown mark '{value}' in location {location['id']}")
            return mark[axis]
        return value

    def lines_for_shot(self, shot_id: str) -> list[dict]:
        return [ln for ln in self.dialogue["lines"] if ln["shot"] == shot_id]

    # ------------------------------------------------------------------ dependencies
    def shot_dependencies(self, shot_id: str) -> list[str]:
        """Canonical document keys whose content affects the shot's compiled scene."""
        shot = self.shots[shot_id]
        scene = self.scenes[shot["scene"]]
        loc = self.locations[scene["location"]]
        deps = {"production", "bible", "registry", shot_id, scene["id"], loc["id"]}
        for member in shot.get("cast", []):
            deps.add(member["character"])
        if self.lines_for_shot(shot_id):
            deps.add("dialogue")
        return sorted(deps)

    def doc_hash(self, key: str) -> str:
        return sha256_file(self.files[key])

    def impact(self, target_id: str) -> dict:
        """Which shots, lines and tasks are affected by a change to `target_id`."""
        shots, lines = set(), set()
        for sid in self.shots:
            shot = self.shots[sid]
            loc = self.shot_location(sid)
            refs = set(self.shot_dependencies(sid))
            refs |= {p["asset"] for p in shot.get("props", [])}
            refs |= {i["asset"] for i in loc.get("instances", [])}
            for a in list(refs):
                refs |= set(self.assets.get(a, {}).get("composed_of", []))
            if target_id in refs:
                shots.add(sid)
        for ln in self.dialogue["lines"]:
            if target_id in (ln["id"], ln["character"], ln["voice"]):
                lines.add(ln["id"])
                shots.add(ln["shot"])
        for vid, v in self.voices.items():
            if target_id == vid:
                lines |= {ln["id"] for ln in self.dialogue["lines"] if ln["voice"] == vid}
        audio_assets = {target_id} & set(self.audio_plan.get("assets", {}))
        return {"target": target_id, "shots": sorted(shots), "lines": sorted(lines), "audio_assets": sorted(audio_assets),
                "note": "Changing these invalidates compile/render tasks for the listed shots in every format, "
                        "and the mix/assemble tasks downstream."}
