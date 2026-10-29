"""Skill discoverability, schema validity and the stdlib JSON Schema validator."""
import json
import re
import unittest

from helpers import EXAMPLE, SKILL

from kagelib import schema


class SkillStructureTest(unittest.TestCase):
    def test_skill_md_frontmatter(self):
        text = (SKILL / "SKILL.md").read_text()
        m = re.match(r"^---\n(.*?)\n---\n", text, re.S)
        self.assertIsNotNone(m, "SKILL.md must start with YAML frontmatter")
        fm = dict(re.findall(r"^(\w[\w-]*):\s*(.+)$", m.group(1), re.M))
        self.assertEqual(fm.get("name"), SKILL.name, "frontmatter name must equal the skill directory name")
        self.assertRegex(fm["name"], r"^[a-z0-9-]{1,64}$")
        self.assertTrue(20 < len(fm.get("description", "")) <= 1024)
        self.assertLess(len(text.splitlines()), 500, "keep SKILL.md focused; details belong in reference/")

    def test_skill_links_resolve(self):
        text = (SKILL / "SKILL.md").read_text()
        links = re.findall(r"\]\(([^)#]+)\)", text)
        self.assertGreater(len(links), 5)
        for link in links:
            if link.startswith("http"):
                continue
            self.assertTrue((SKILL / link).exists(), f"SKILL.md links to missing file {link}")
        for ref in (SKILL / "reference").glob("*.md"):
            for link in re.findall(r"\]\(([^)#]+)\)", ref.read_text()):
                if not link.startswith("http"):
                    self.assertTrue((ref.parent / link).resolve().exists(), f"{ref.name} links to missing {link}")

    def test_entry_point_and_library(self):
        self.assertTrue((SKILL / "scripts" / "kage.py").exists())
        lib = json.loads((SKILL / "library" / "components.json").read_text())
        for cid, c in lib["components"].items():
            self.assertRegex(cid, r"^[a-z]+\.[a-z0-9_]+$")
            self.assertNotIn("gender", json.dumps(c).lower(), f"component {cid} must not encode gender")


class SchemaTest(unittest.TestCase):
    def test_all_schemas_load_and_use_supported_keywords(self):
        store = schema.SchemaStore()
        names = sorted(p.name for p in (SKILL / "schemas").glob("*.schema.json"))
        self.assertGreaterEqual(len(names), 18)
        for n in names:
            s = store.get(n)
            self.assertEqual(s["$schema"], "https://json-schema.org/draft/2020-12/schema")
            schema.validate({}, n, store)  # walks the schema; raises on unsupported keywords

    def test_reference_validator_agrees(self):
        try:
            import jsonschema  # noqa: F401
            from referencing import Registry, Resource
        except ImportError:
            self.skipTest("third-party jsonschema not installed; stdlib validator used instead (cross-check run separately)")
        res = {p.name: Resource.from_contents(json.loads(p.read_text())) for p in (SKILL / "schemas").glob("*.json")}
        reg = Registry().with_resources(res.items())
        for p in (SKILL / "schemas").glob("*.json"):
            jsonschema.Draft202012Validator.check_schema(json.loads(p.read_text()))
        v = jsonschema.Draft202012Validator(json.loads((SKILL / "schemas" / "shot.schema.json").read_text()), registry=reg)
        doc = json.loads((EXAMPLE / "shots" / "shot_s02_010.json").read_text())
        self.assertEqual(list(v.iter_errors(doc)), [])

    def test_example_documents_validate(self):
        pairs = [("production.json", "production.schema.json"), ("story/brief.json", "story_brief.schema.json"),
                 ("bible/visual_bible.json", "visual_bible.schema.json"), ("assets/registry.json", "asset_registry.schema.json"),
                 ("audio/dialogue.json", "dialogue.schema.json"), ("audio/plan.json", "audio_plan.schema.json")]
        for d, s in (("characters", "character"), ("voices", "voice"), ("locations", "location"), ("shots", "shot"), ("scenes", "scene")):
            pairs += [(str(p.relative_to(EXAMPLE)), f"{s}.schema.json") for p in (EXAMPLE / d).glob("*.json")]
        for f, s in pairs:
            self.assertEqual(schema.validate(json.loads((EXAMPLE / f).read_text()), s), [], f)

    def test_validator_rejects_bad_documents(self):
        shot = json.loads((EXAMPLE / "shots" / "shot_s02_020.json").read_text())
        bad = json.loads(json.dumps(shot))
        bad["id"] = "S02-020"
        self.assertTrue(any("does not match" in e for e in schema.validate(bad, "shot.schema.json")))
        bad = json.loads(json.dumps(shot))
        del bad["camera"]
        self.assertTrue(any("missing required property 'camera'" in e for e in schema.validate(bad, "shot.schema.json")))
        bad = json.loads(json.dumps(shot))
        bad["cast"][0]["actions"][0]["type"] = "teleport"
        self.assertTrue(schema.validate(bad, "shot.schema.json"))
        bad = json.loads(json.dumps(shot))
        bad["cast"][1]["actions"].append({"type": "walk", "start": 0, "duration": 1})  # walk without to_x
        self.assertTrue(schema.validate(bad, "shot.schema.json"))
        prod = json.loads((EXAMPLE / "production.json").read_text())
        prod["formats"][0]["aspect"] = "21:9"
        self.assertTrue(any("not one of" in e for e in schema.validate(prod, "production.schema.json")))
        ch = json.loads((EXAMPLE / "characters" / "chr_maya.json").read_text())
        ch["identity"]["presentation"] = "female-coded"
        self.assertTrue(schema.validate(ch, "character.schema.json"))


if __name__ == "__main__":
    unittest.main()
