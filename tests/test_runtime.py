"""Scheduler, engine integration (determinism, retries), audio and export validation."""
import hashlib
import json
import os
import subprocess
import tempfile
import threading
import time
import unittest
from pathlib import Path

from helpers import ENGINE, EXAMPLE, HAVE_FFMPEG, copy_example

from kagelib import audio as A
from kagelib import media as M
from kagelib.png import read_png
from kagelib.scheduler import Scheduler, Task
from kagelib.util import KageError, TransientError


class SchedulerTest(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        self.calls = []

    def task(self, tid, deps=(), fn=None, **kw):
        out = self.dir / f"{tid}.out"

        def run(t):
            self.calls.append(tid)
            if fn:
                fn(t)
            # Output depends on inputs and upstream outputs, like a real build step.
            upstream = "".join((self.dir / f"{d}.out").read_text() for d in t.deps)
            own = "".join(Path(f).read_text() for f in t.input_files)
            out.write_text(tid + json.dumps(t.params) + own + upstream)
            return {}
        return Task(tid, "test", list(deps), run, outputs=[out], **kw)

    def sched(self, tasks, **kw):
        return Scheduler(tasks, self.dir / "state.json", backoff_s=0, **kw)

    def test_order_cache_and_selective_invalidation(self):
        src = self.dir / "input.txt"
        src.write_text("v1")
        mk = lambda: [self.task("a", input_files=[src]), self.task("b", ["a"]), self.task("c"), self.task("d", ["b", "c"])]
        rep = self.sched(mk()).run()
        self.assertEqual(rep["counts"]["done"], 4)
        self.assertLess(self.calls.index("a"), self.calls.index("b"))
        self.assertLess(self.calls.index("b"), self.calls.index("d"))
        self.calls.clear()
        self.assertEqual(self.sched(mk()).run()["counts"]["cached"], 4)
        self.assertEqual(self.calls, [])
        src.write_text("v2")  # only a and its dependents re-run
        self.sched(mk()).run()
        self.assertEqual(sorted(self.calls), ["a", "b", "d"])

    def test_early_cutoff_when_output_unchanged(self):
        src = self.dir / "input.txt"
        src.write_text("v1")

        def normalise(t):  # output ignores whitespace-only edits of the input
            pass
        mk = lambda: [Task("a", "test", [], lambda t: (self.calls.append("a"), (self.dir / "a.out").write_text(src.read_text().strip()), {})[2],
                           input_files=[src], outputs=[self.dir / "a.out"]), self.task("b", ["a"])]
        self.sched(mk()).run()
        self.calls.clear()
        src.write_text("v1\n")  # input changed, output identical
        self.sched(mk()).run()
        self.assertEqual(self.calls, ["a"], "dependents of an unchanged output stay cached")

    def test_transient_failure_is_retried(self):
        attempts = {"n": 0}

        def flaky(t):
            attempts["n"] += 1
            if attempts["n"] < 3:
                raise TransientError("simulated crash")
        rep = self.sched([self.task("x", fn=flaky, max_attempts=3)]).run()
        self.assertEqual(rep["counts"]["done"], 1)
        state = json.loads((self.dir / "state.json").read_text())
        self.assertEqual(state["tasks"]["x"]["attempts"], 3)
        self.assertEqual(len(state["tasks"]["x"]["retry_history"]), 2)

    def test_retries_are_bounded_and_dependents_blocked(self):
        def always(t):
            raise TransientError("still broken")
        rep = self.sched([self.task("x", fn=always, max_attempts=2), self.task("y", ["x"]), self.task("z")]).run()
        self.assertEqual(rep["tasks"]["failed"], ["x"])
        self.assertEqual(rep["tasks"]["blocked"], ["y"])
        self.assertEqual(rep["tasks"]["done"], ["z"])
        self.assertIn("after 2 attempts", rep["failures"]["x"])

    def test_deterministic_failure_not_retried(self):
        n = {"c": 0}

        def bad(t):
            n["c"] += 1
            raise KageError("invalid scene")
        self.sched([self.task("x", fn=bad, max_attempts=5)]).run()
        self.assertEqual(n["c"], 1)

    def test_cycle_detected(self):
        with self.assertRaises(KageError):
            self.sched([self.task("a", ["b"]), self.task("b", ["a"])])

    def test_gpu_concurrency_limit(self):
        active = {"now": 0, "max": 0}
        lock = threading.Lock()

        def slow(t):
            with lock:
                active["now"] += 1
                active["max"] = max(active["max"], active["now"])
            time.sleep(0.05)
            with lock:
                active["now"] -= 1
        tasks = [self.task(f"r{i}", fn=slow, resource="gpu") for i in range(6)]
        self.sched(tasks, concurrency={"gpu": 2, "cpu": 4}).run()
        self.assertEqual(active["max"], 2)


class ProjectLockTest(unittest.TestCase):
    def test_concurrent_runs_are_refused_and_stale_locks_reclaimed(self):
        from helpers import copy_example
        from kagelib.cli import ProjectLock
        from kagelib.project import Project
        p = Project(copy_example())
        with ProjectLock(p):
            with self.assertRaises(KageError):
                with ProjectLock(p):
                    pass
        self.assertFalse((p.build / "run.lock").exists())
        (p.build / "run.lock").write_text("999999\n")  # pid of a process that does not exist
        with ProjectLock(p):
            self.assertEqual((p.build / "run.lock").read_text().strip(), str(os.getpid()))


@unittest.skipUnless(ENGINE and HAVE_FFMPEG, "requires the built engine and FFmpeg")
class EngineIntegrationTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from kagelib.compile import compile_shot
        from kagelib.project import Project
        from kagelib.timeline import build_timeline
        p = Project(EXAMPLE)
        cls.dir = Path(tempfile.mkdtemp())
        scene = compile_shot(p, "shot_s02_010", p.formats()[0], build_timeline(p), size_override=(320, 180))
        cls.scene = cls.dir / "s.json"
        cls.scene.write_text(json.dumps(scene))

    def png(self, frame, name, backend="soft"):
        out = self.dir / name
        subprocess.run([str(ENGINE), str(self.scene), "--frame", str(frame), "--png", str(out), "--backend", backend], check=True)
        return out

    def test_frames_are_deterministic_and_order_independent(self):
        a = self.png(60, "a.png").read_bytes()
        subprocess.run([str(ENGINE), str(self.scene), "--frames", "0:61", "--png-dir", str(self.dir), "--backend", "soft"], check=True)
        b = (self.dir / "frame_00060.png").read_bytes()
        self.assertEqual(hashlib.sha256(a).hexdigest(), hashlib.sha256(b).hexdigest())
        self.assertNotEqual(a, self.png(61, "c.png").read_bytes())

    def test_render_is_not_blank_and_uses_palette(self):
        from kagelib.qa import frame_stats
        from kagelib.project import Project
        st = frame_stats(self.png(60, "d.png"), list(Project(EXAMPLE).bible["palette"].values()))
        self.assertEqual((st["width"], st["height"]), (320, 180))
        self.assertGreater(st["luma_std"], 10)
        self.assertGreater(st["palette_conformity"], 0.85)

    def test_transient_engine_fault_is_retried_by_scheduler(self):
        marker = self.dir / "fault.marker"
        env = {**os.environ, "KAGE_FAULT_INJECT_ONCE": str(marker)}
        out = self.dir / "shot.mkv"

        def render(t):
            return M.render_shot(self.scene, out, self.dir / "p.json", self.dir / "e.json", backend="soft", env=env)
        rep = Scheduler([Task("render", "render", [], render, outputs=[out], max_attempts=2)], self.dir / "st.json", backoff_s=0).run()
        self.assertEqual(rep["counts"]["done"], 1)
        st = json.loads((self.dir / "st.json").read_text())["tasks"]["render"]
        self.assertEqual(st["attempts"], 2)
        self.assertEqual(st["retry_history"][0]["class"], "transient")
        self.assertEqual(M.count_frames(out), json.loads(self.scene.read_text())["output"]["frame_count"])

    def test_invalid_scene_exit_code(self):
        bad = self.dir / "bad.json"
        bad.write_text(self.scene.read_text().replace('"hand_l"', '"tail"'))
        cp = subprocess.run([str(ENGINE), str(bad), "--validate"], capture_output=True)
        self.assertEqual(cp.returncode, 2)
        self.assertIn("unknown joint 'tail'", cp.stderr.decode())


class AudioTest(unittest.TestCase):
    def test_synth_is_deterministic(self):
        a = A.synth("footstep")
        b = A.synth("footstep")
        self.assertEqual(a, b)
        self.assertEqual(len(A.synth("train_interior", 1.5)), int(1.5 * A.SR))

    def test_envelope_and_pitch(self):
        tone = [0.5 * __import__("math").sin(2 * 3.14159265 * 220 * i / A.SR) for i in range(A.SR)]
        env = A.envelope(tone, A.SR, 24)
        self.assertEqual(len(env), 24)
        self.assertAlmostEqual(env[5], 0.5 / 2 ** 0.5, places=2)
        self.assertAlmostEqual(A.estimate_f0(tone, A.SR), 220, delta=6)

    def test_srt(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "s.srt"
            A.write_srt([{"start_s": 61.5, "end_s": 63.25, "text": "Hi"}], p)
            self.assertEqual(p.read_text().splitlines()[1], "00:01:01,500 --> 00:01:03,250")

    @unittest.skipUnless(HAVE_FFMPEG, "requires FFmpeg")
    def test_stem_duration_is_exact_and_clips_overlap_cuts(self):
        with tempfile.TemporaryDirectory() as td:
            clip = Path(td) / "c.wav"
            A.write_wav(clip, A.synth("whistle"))
            out = Path(td) / "stem.wav"
            A.build_stem([(clip, 2.0, {"gain_db": -6}), (clip, 4.5, {"gain_db": 0})], 5.0, out)
            samples, sr = A.read_wav_mono(out)
            self.assertEqual(len(samples), 5 * sr)
            self.assertLess(A.rms_window(out, 0.5, 1.9), 1e-4)
            self.assertGreater(A.rms_window(out, 2.2, 3.0), 0.01)


@unittest.skipUnless(HAVE_FFMPEG, "requires FFmpeg")
class ExportValidationTest(unittest.TestCase):
    def test_real_file_checks(self):
        with tempfile.TemporaryDirectory() as td:
            out = Path(td) / "t.mp4"
            subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "testsrc=s=320x180:r=24:d=2", "-f", "lavfi", "-i",
                            "sine=f=440:d=2:sample_rate=48000", "-ac", "2", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac",
                            "-shortest", str(out)], check=True)
            ok = M.validate_export(out, width=320, height=180, fps=(24, 1), duration=2.0, frames=48, expect_audio=True, expect_subtitles=False)
            self.assertTrue(ok["passed"], [c for c in ok["checks"] if not c["passed"]])
            bad = M.validate_export(out, width=1280, height=720, fps=(25, 1), duration=2.0, frames=50, expect_audio=True, expect_subtitles=True)
            failed = {c["check"] for c in bad["checks"] if not c["passed"]}
            self.assertTrue({"resolution", "frame_rate", "frame_count", "subtitle_stream"} <= failed)
            missing = M.validate_export(Path(td) / "nope.mp4", width=1, height=1, fps=(24, 1), duration=1, frames=1,
                                        expect_audio=False, expect_subtitles=False)
            self.assertFalse(missing["passed"])

    def test_png_reader_handles_ffmpeg_filters(self):
        with tempfile.TemporaryDirectory() as td:
            out = Path(td) / "f.png"
            subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "testsrc=s=64x48", "-frames:v", "1", str(out)], check=True)
            img = read_png(out)
            self.assertEqual((img.width, img.height), (64, 48))
            self.assertEqual(len(img.rgb), 64 * 48 * 3)


if __name__ == "__main__":
    unittest.main()
