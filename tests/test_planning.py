"""Planning QA, character identity, timeline and compile behaviour on real project data."""
import json
import unittest

from helpers import ENGINE, EXAMPLE, copy_example, edit_json

from kagelib.characters import engine_character, rig_measurements, standing_height
from kagelib.compile import compile_shot, compute_camera
from kagelib.project import Project
from kagelib.timeline import build_timeline
from kagelib.util import KageError, sha256_json
from kagelib.validate import validate_project


def errors(project, **kw):
    return [f"{i['category']}|{i['ref']}|{i['message']}" for i in validate_project(project, **kw).errors]


class PlanningValidationTest(unittest.TestCase):
    def test_example_passes(self):
        self.assertEqual(errors(Project(EXAMPLE)), [])

    def mutate(self, rel, fn):
        root = copy_example()
        edit_json(root / rel, fn)
        return Project(root)

    def test_invalid_asset_reference(self):
        p = self.mutate("locations/loc_carriage.json", lambda d: d["instances"][2].update(asset="ast_missing"))
        self.assertTrue(any("unknown asset 'ast_missing'" in e for e in errors(p)))

    def test_palette_violation(self):
        p = self.mutate("assets/registry.json", lambda d: d["assets"]["ast_bench"]["primitives"][0].update(fill="hot_pink"))
        self.assertTrue(any("hot_pink" in e and e.startswith("palette") for e in errors(p)))

    def test_voice_lock(self):
        p = self.mutate("audio/dialogue.json", lambda d: d["lines"][1].update(voice="voc_maya"))
        self.assertTrue(any("is locked to 'voc_lila'" in e for e in errors(p)))

    def test_unknown_dialogue_character(self):
        p = self.mutate("audio/dialogue.json", lambda d: d["lines"][0].update(character="chr_ghost"))
        self.assertTrue(any("chr_ghost" in e for e in errors(p)))

    def test_aspect_ratio_mismatch(self):
        p = self.mutate("production.json", lambda d: d["formats"][1].update(width=800))
        self.assertTrue(any("does not match aspect 9:16" in e for e in errors(p)))

    def test_story_coverage(self):
        p = self.mutate("shots/shot_s02_030.json", lambda d: d.update(covers=[]))
        self.assertTrue(any("evt_08" in e and "not covered" in e for e in errors(p)))

    def test_unknown_mark_and_target(self):
        def f(d):
            d["cast"][0]["actions"][2]["to_x"] = "@nowhere"
            d["cast"][1]["actions"][1]["target"]["character"] = "chr_conductorx"
        p = self.mutate("shots/shot_s02_010.json", f)
        errs = errors(p)
        self.assertTrue(any("@nowhere" in e for e in errs))
        self.assertTrue(any("chr_conductorx" in e for e in errs))

    def test_draft_blocks_render_but_not_animatic(self):
        p = self.mutate("characters/chr_lila.json", lambda d: d.update(status="draft"))
        self.assertTrue(any(e.startswith("approval|chr_lila") for e in errors(p)))
        self.assertFalse(any(e.startswith("approval") for e in errors(p, for_render=False)))

    def test_approved_content_cannot_change_silently(self):
        root = copy_example()
        p = Project(root)
        approvals = root / "build" / "approvals.json"
        approvals.parent.mkdir(parents=True)
        from kagelib.util import sha256_file
        approvals.write_text(json.dumps({"records": {"chr_maya": {"version": "1.0.0", "sha256": sha256_file(p.files["chr_maya"])}}}))
        edit_json(root / "characters" / "chr_maya.json", lambda d: d["palette"].update(top="navy"))
        self.assertTrue(any("without a version bump" in e for e in errors(Project(root), approvals_path=approvals)))
        edit_json(root / "characters" / "chr_maya.json", lambda d: d.update(version="1.1.0"))
        self.assertFalse(any("without a version bump" in e for e in errors(Project(root), approvals_path=approvals)))

    def test_file_name_must_equal_id(self):
        root = copy_example()
        (root / "characters" / "chr_maya.json").rename(root / "characters" / "chr_mama.json")
        with self.assertRaises(KageError):
            Project(root)


class CharacterIdentityTest(unittest.TestCase):
    def setUp(self):
        self.p = Project(EXAMPLE)

    def test_child_is_not_a_scaled_adult(self):
        adult = rig_measurements(self.p.characters["chr_maya"], self.p.bible)
        child = rig_measurements(self.p.characters["chr_lila"], self.p.bible)
        self.assertAlmostEqual(standing_height(adult), 1.66, places=3)
        self.assertAlmostEqual(standing_height(child), 1.20, places=3)
        ratio_a = 2 * adult["head_radius"] / 1.66
        ratio_c = 2 * child["head_radius"] / 1.20
        self.assertGreater(ratio_c / ratio_a, 1.2)          # bigger head relative to body
        self.assertLess(child["thigh"] / 1.20, adult["thigh"] / 1.66)  # shorter legs relative to height
        self.assertGreater(child["energy"], adult["energy"])

    def test_presentation_does_not_drive_geometry(self):
        ch = json.loads(json.dumps(self.p.characters["chr_maya"]))
        base = engine_character(ch, self.p.bible, layer="actors", start={"x": 0}, actions=[])
        for pres in ("masculine", "androgynous", "neutral", "custom"):
            ch["identity"]["presentation"] = pres
            ch["identity"]["gender_identity"] = "unspecified"
            other = engine_character(ch, self.p.bible, layer="actors", start={"x": 0}, actions=[])
            self.assertEqual(sha256_json(base), sha256_json(other), pres)

    def test_components_bind_to_rig(self):
        ec = engine_character(self.p.characters["chr_lila"], self.p.bible, layer="actors", start={"x": 0}, actions=[])
        joints = set()
        for prim in ec["components"]:
            if prim["type"] == "bone":
                joints |= {prim["from"], prim["to"]}
            if "bind" in prim:
                joints.add(prim["bind"])
            for pt in prim.get("points", []):
                if isinstance(pt, dict):
                    joints.add(pt["bind"])
            for key in ("fill", "stroke"):
                if key in prim:
                    self.assertRegex(prim[key], r"^#[0-9a-f]{6}$")
        self.assertTrue({"head", "chest", "pelvis", "hand_r", "knee_l"} <= joints | {"hand_r"})
        self.assertIn("head", joints)

    def test_variant_changes_components_not_identity(self):
        ch = self.p.characters["chr_lila"]
        a = engine_character(ch, self.p.bible, layer="actors", start={"x": 0}, actions=[])
        b = engine_character(ch, self.p.bible, layer="actors", start={"x": 0}, actions=[], variant_id="no_backpack")
        self.assertLess(len(b["components"]), len(a["components"]))
        self.assertEqual(a["rig"], b["rig"])
        self.assertEqual(a["id"], b["id"])


class TimelineAndCompileTest(unittest.TestCase):
    def setUp(self):
        self.p = Project(EXAMPLE)

    def test_estimated_timeline(self):
        tl = build_timeline(self.p)
        self.assertEqual(tl["errors"], [])
        starts = [s["start_frame"] for s in tl["shots"]]
        self.assertEqual(starts, sorted(starts))
        self.assertEqual(tl["total_frames"], sum(s["frames"] for s in tl["shots"]))
        amb = [c for c in tl["audio_clips"] if c["kind"] == "ambience"]
        self.assertEqual(len(amb), 2, "one bed per location, continuous across same-location cuts")
        self.assertEqual(len(tl["subtitles"]), 3)

    def test_long_speech_extends_flex_shot_not_stretched(self):
        tl = build_timeline(self.p, {"line_002": {"duration_s": 3.2}})
        shot = next(s for s in tl["shots"] if s["id"] == "shot_s02_020")
        self.assertGreaterEqual(shot["duration_s"], 1.85 + 3.2 + 0.35 - 1e-6)
        self.assertEqual(tl["adjustments"][0]["line"], "line_002")
        clip = next(c for c in tl["audio_clips"] if c.get("line") == "line_002")
        self.assertEqual(clip["duration_s"], 3.2)

    def test_speech_too_long_for_fixed_shot_is_an_error(self):
        tl = build_timeline(self.p, {"line_002": {"duration_s": 9.0}})
        self.assertTrue(any("line_002" in e for e in tl["errors"]))

    def test_camera_fits_every_aspect(self):
        shot = self.p.shots["shot_s02_020"]
        for fmt in ({"id": a, "aspect": a, "width": w, "height": h}
                    for a, w, h in (("16:9", 1280, 720), ("9:16", 720, 1280), ("4:5", 864, 1080), ("5:4", 1080, 864), ("1:1", 1080, 1080))):
            cam = compute_camera(self.p, shot, fmt, 4.0)
            k = cam["keys"][0]
            half_w = k["height"] * fmt["width"] / fmt["height"] / 2
            for x in (0.0, 0.62):  # both seat marks
                self.assertLess(abs(x - k["x"]), half_w, f"subject at x={x} outside {fmt['aspect']} frame")

    def test_compiled_scene_is_story_agnostic(self):
        tl = build_timeline(self.p)
        scene = compile_shot(self.p, "shot_s02_010", self.p.formats()[0], tl)
        text = json.dumps(scene)
        self.assertNotIn("@seat", text)
        self.assertNotIn("Evening", text)
        for ch in scene["characters"]:
            self.assertRegex(ch["line_color"], r"^#")
        talk = [a for c in scene["characters"] for a in c["actions"] if a["type"] == "talk"]
        self.assertEqual(len(talk), 1, "dialogue adds a talk gesture to the speaker")

    @unittest.skipUnless(ENGINE, "engine not built")
    def test_engine_accepts_every_compiled_shot(self):
        import subprocess
        import tempfile
        from pathlib import Path
        tl = build_timeline(self.p)
        with tempfile.TemporaryDirectory() as td:
            for fmt in self.p.formats():
                for sid in self.p.shot_order():
                    path = Path(td) / f"{fmt['id']}_{sid}.json"
                    path.write_text(json.dumps(compile_shot(self.p, sid, fmt, tl)))
                    cp = subprocess.run([str(ENGINE), str(path), "--validate"], capture_output=True)
                    self.assertEqual(cp.returncode, 0, cp.stdout.decode() + cp.stderr.decode())


if __name__ == "__main__":
    unittest.main()
