"""End-to-end checks on the real example production.

1. The delivered example outputs exist, hash-match the delivery manifest and
   validate against the delivery schema.
2. Selective regeneration: on a copy of the fully built project, editing one
   dialogue line re-runs only the work that depends on it.
"""
import json
import subprocess
import sys
import unittest

from helpers import ENGINE, EXAMPLE, HAVE_FFMPEG, HAVE_SAY, SKILL, copy_example, edit_json

from kagelib import schema
from kagelib.util import sha256_file

KAGE = [sys.executable, str(SKILL / "scripts" / "kage.py")]


class DeliveredExampleTest(unittest.TestCase):
    def setUp(self):
        if not (EXAMPLE / "output" / "delivery.json").exists():
            self.skipTest("example not produced yet: run `kage.py run examples/train-journey`")
        self.delivery = json.loads((EXAMPLE / "output" / "delivery.json").read_text())

    def test_manifest_schema_and_hashes(self):
        self.assertEqual(schema.validate(self.delivery, "delivery.schema.json"), [])
        for film in self.delivery["films"]:
            path = EXAMPLE / film["path"]
            self.assertTrue(path.exists(), film["path"])
            self.assertEqual(sha256_file(path), film["sha256"], "film changed after delivery")
            qa = json.loads((EXAMPLE / film["qa"]).read_text())
            self.assertTrue(qa["passed"])
            self.assertTrue(all(c["passed"] for c in qa["export"]["checks"]))
        self.assertEqual({f["aspect"] for f in self.delivery["films"]}, {"16:9", "9:16"})

    def test_all_qa_reports_pass(self):
        for rel in self.delivery["qa_reports"]:
            doc = json.loads((EXAMPLE / rel).read_text())
            if doc.get("schema") == "kage.qa_report/1":
                self.assertTrue(doc["passed"], rel)

    def test_reviews_are_attributed(self):
        reviews = self.delivery["subjective_reviews"]
        self.assertTrue(reviews, "subjective review status must be recorded")
        for r in reviews:
            self.assertTrue(r["reviewer"])
        listening = [r for r in reviews if r["kind"] == "listening"]
        self.assertTrue(listening and listening[0]["verdict"] != "approved",
                        "no human listening review was performed; it must not be recorded as approved")


@unittest.skipUnless(ENGINE and HAVE_FFMPEG and HAVE_SAY, "requires engine, FFmpeg and macOS say")
class SelectiveRegenerationTest(unittest.TestCase):
    def test_unversioned_edit_of_approved_content_is_blocked(self):
        if not (EXAMPLE / "build" / "approvals.json").exists():
            self.skipTest("example approvals not recorded")
        root = copy_example(with_build=True)
        edit_json(root / "audio" / "dialogue.json", lambda d: d["lines"][1].update(text="Mama?"))
        cp = subprocess.run(KAGE + ["validate", str(root)], capture_output=True)
        self.assertEqual(cp.returncode, 2)
        self.assertIn("without a version bump", cp.stdout.decode())

    def test_editing_one_line_reruns_only_dependents(self):
        if not (EXAMPLE / "build" / "state.json").exists():
            self.skipTest("example not built yet")
        root = copy_example(with_build=True)
        # Paths in state are absolute; rebase them onto the copy.
        state_path = root / "build" / "state.json"
        state_path.write_text(state_path.read_text().replace(str(EXAMPLE), str(root)))
        def change(d):
            d["lines"][1]["text"] = "Mama, will Nani still remember me?"
            d["version"] = "1.0.1"  # approved content may only change with a version bump
        edit_json(root / "audio" / "dialogue.json", change)
        cp = subprocess.run(KAGE + ["run", str(root)], capture_output=True, timeout=900)
        self.assertEqual(cp.returncode, 0, cp.stderr.decode()[-3000:])
        rep = json.loads((root / "build" / "run_report.json").read_text())
        done = set(rep["tasks"]["done"])
        self.assertIn("voice:line_002", done)
        self.assertIn("timeline", done)
        self.assertIn("render:landscape:shot_s02_020", done)
        self.assertNotIn("voice:line_001", done)
        self.assertNotIn("render:landscape:shot_s01_010", done, "unrelated shots must not re-render")
        self.assertNotIn("render:vertical:shot_s02_040", done)
        self.assertNotIn("identity", done)


if __name__ == "__main__":
    unittest.main()
