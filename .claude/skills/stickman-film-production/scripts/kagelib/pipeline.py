"""Production task graph for one film project.

Granularity is chosen so that edits invalidate only what they affect: a
dialogue change re-generates one line, re-times the timeline, recompiles only
shots whose timing or speech changed, and re-mixes; a character change
recompiles/re-renders only shots that include the character.
"""
from __future__ import annotations

import json
import re
import shutil
import time
from pathlib import Path

from . import audio as A
from . import media as M
from . import qa as Q
from .characters import component_library, lineup_scene, silhouette_metrics, feature_signature
from .compile import active_camera, compile_shot
from .png import read_png
from .project import Project
from .scheduler import Task
from .timeline import build_timeline
from .util import (KageError, LIBRARY_DIR, TOOL_VERSION, dump_json, find_engine, load_json, log, rel, require_engine,
                   run, sha256_file, sha256_json)
from .validate import validate_project


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")


class Pipeline:
    def __init__(self, project: Project, *, formats: list[str] | None = None, backend: str | None = None):
        self.p = project
        self.formats = [f for f in project.formats() if not formats or f["id"] in formats]
        if not self.formats:
            raise KageError(f"no matching formats (available: {[f['id'] for f in project.formats()]})")
        rc = project.render
        self.backend = backend or rc.get("backend", "auto")
        self.ssaa, self.msaa = rc.get("ssaa", 3), rc.get("msaa", 8)
        self.max_attempts = rc.get("retries", {}).get("max_attempts", 3)
        self.timeout = rc.get("timeout_s", 900)
        b = project.build
        self.paths = {
            "state": b / "state.json", "timeline": b / "timeline.json", "lines": b / "audio" / "lines",
            "assets": b / "audio" / "assets", "stems": b / "audio" / "stems", "approvals": b / "approvals.json",
        }
        self.slug = slug(project.production["title"])

    # ------------------------------------------------------------ helpers
    def canonical_files(self) -> list[Path]:
        return sorted(set(self.p.files.values()))

    def fmt_dir(self, fmt: dict) -> Path:
        return self.p.build / fmt["id"]

    def line_record(self, line_id: str) -> dict | None:
        path = self.paths["lines"] / f"{line_id}.json"
        return load_json(path) if path.exists() else None

    def line_records(self) -> dict:
        return {ln["id"]: r for ln in self.p.dialogue["lines"] if (r := self.line_record(ln["id"]))}

    def timeline(self) -> dict:
        return load_json(self.paths["timeline"])

    # ------------------------------------------------------------ task bodies
    def t_validate(self, task):
        rep = validate_project(self.p, for_render=True, approvals_path=self.paths["approvals"])
        doc = rep.to_json(self.p.production["project_id"], {"tool_version": TOOL_VERSION})
        dump_json(self.p.qa_dir / "planning.json", doc)
        if not doc["passed"]:
            raise KageError(f"planning QA failed with {doc['error_count']} error(s); see qa/planning.json: "
                            + "; ".join(f"{i['ref']}: {i['message']}" for i in rep.errors[:5]))
        return {"errors": 0, "warnings": doc["warning_count"]}

    def t_lineup(self, task):
        engine = require_engine()
        out = self.p.output / "review"
        out.mkdir(parents=True, exist_ok=True)
        tmp = self.p.build / "identity"
        tmp.mkdir(parents=True, exist_ok=True)
        chars = [self.p.characters[c] for c in sorted(self.p.characters)]
        sc = lineup_scene(chars, self.p.bible)
        dump_json(tmp / "lineup.scene.json", sc)
        for style in ("final", "flat", "silhouette"):
            run([engine, tmp / "lineup.scene.json", "--png", out / f"lineup_{style}.png", "--style", style, "--backend", "soft"])
        small = lineup_scene(chars, self.p.bible, width=320, height=180)
        dump_json(tmp / "lineup_small.scene.json", small)
        run([engine, tmp / "lineup_small.scene.json", "--png", out / "lineup_small.png", "--backend", "soft"])
        masks = {}
        for ch in chars:
            solo = lineup_scene([ch], self.p.bible, width=256, height=256, solo=True)
            solo["camera"]["keys"][0].update({"x": 0, "y": 1.0, "height": 2.2})
            dump_json(tmp / f"solo_{ch['id']}.scene.json", solo)
            png = tmp / f"solo_{ch['id']}.png"
            run([engine, tmp / f"solo_{ch['id']}.scene.json", "--png", png, "--style", "silhouette", "--backend", "soft"])
            img = read_png(png)
            masks[ch["id"]] = [img.rgb[i] < 128 for i in range(0, len(img.rgb), 3)]
        pairs = silhouette_metrics(masks)
        sigs = {c["id"]: feature_signature(c, self.p.bible) for c in chars}
        issues = []
        for pr in pairs:
            a, b = sigs[pr["a"]], sigs[pr["b"]]
            diffs = []
            if abs(a["height_m"] - b["height_m"]) / max(a["height_m"], b["height_m"]) > 0.08:
                diffs.append("height")
            if set(a["components"]) != set(b["components"]):
                diffs.append("components: " + ", ".join(sorted(set(a["components"]) ^ set(b["components"]))))
            if a["palette"] != b["palette"]:
                diffs.append("palette")
            pr["distinguishing"] = diffs
            shape_diffs = [d for d in diffs if d != "palette"]
            if pr["iou"] > 0.9 and not shape_diffs:
                issues.append({"severity": "error", "category": "identity", "ref": f"{pr['a']}/{pr['b']}",
                               "message": f"silhouettes nearly identical (IoU {pr['iou']}) and differ only by color",
                               "suggested_fix": "change hair/headwear/garment silhouette, build or height for one character"})
            elif pr["iou"] > 0.75 and len(shape_diffs) < 2:
                issues.append({"severity": "warning", "category": "identity", "ref": f"{pr['a']}/{pr['b']}",
                               "message": f"similar silhouettes (IoU {pr['iou']}); distinguished only by {diffs}"})
        doc = {"schema": "kage.qa_report/1", "project_id": self.p.production["project_id"], "stage": "identity",
               "passed": not any(i["severity"] == "error" for i in issues), "silhouette_pairs": pairs, "signatures": sigs,
               "previews": [rel(out / f"lineup_{s}.png", self.p.root) for s in ("final", "flat", "silhouette", "small")],
               "issues": issues, "visual_review": "not recorded by this task; use `kage.py review --stage identity`"}
        dump_json(self.p.qa_dir / "identity.json", doc)
        if not doc["passed"]:
            raise KageError("identity QA failed: characters not distinguishable; see qa/identity.json")
        return {"pairs": len(pairs), "issues": len(issues)}

    def t_voice(self, task):
        ln = self.p.lines[task.params["line"]]
        voice = self.p.voices[ln["voice"]]
        wav = self.paths["lines"] / f"{ln['id']}.wav"
        rec = A.generate_line(ln, voice, wav, self.p.fps_value)
        dump_json(self.paths["lines"] / f"{ln['id']}.json", rec)
        return {"duration_s": rec["duration_s"], "f0_hz": rec["f0_hz"], "hash_payload": rec["cache_key"]}

    def t_timeline(self, task):
        tl = build_timeline(self.p, self.line_records(), allow_estimates=False)
        dump_json(self.paths["timeline"], tl)
        dump_json(self.p.build / "dialogue_generated.json", {
            "schema": "kage.dialogue_generated/1",
            "lines": [{"id": ln["id"], **{k: v for k, v in (self.line_record(ln["id"]) or {}).items() if k != "envelope"},
                       "estimated_s": ln["estimated_s"], "qa_status": "pending"} for ln in self.p.dialogue["lines"]]})
        if tl["errors"]:
            raise KageError("timeline errors: " + "; ".join(tl["errors"]))
        return {"duration_s": tl["duration_s"], "frames": tl["total_frames"], "adjustments": tl["adjustments"],
                "hash_payload": {"shots": tl["shots"]}}

    def shot_timeline_payload(self, shot_id: str) -> dict:
        tl = self.timeline()
        entry = next(s for s in tl["shots"] if s["id"] == shot_id)
        lines = {ln["id"]: (self.line_record(ln["id"]) or {}).get("cache_key") for ln in self.p.lines_for_shot(shot_id)}
        return {"frames": entry["frames"], "lines": lines}

    def t_compile(self, task):
        fmt = next(f for f in self.formats if f["id"] == task.params["format"])
        sid = task.params["shot"]
        scene = compile_shot(self.p, sid, fmt, self.timeline(), self.line_records())
        d = self.fmt_dir(fmt)
        path = d / "scenes" / f"{sid}.scene.json"
        dump_json(path, scene)
        (d / "events").mkdir(parents=True, exist_ok=True)
        cp = run([require_engine(), path, "--validate", "--events", d / "events" / f"{sid}.events.json"], check=False)
        if cp.returncode != 0:
            raise KageError(f"engine rejected compiled scene {sid}: {cp.stdout.decode()[:600]} {cp.stderr.decode()[:600]}")
        char_hash = {c["id"]: sha256_json({"rig": c["rig"], "components": c["components"], "line": c["line_color"]})
                     for c in scene["characters"]}
        dump_json(d / "scenes" / f"{sid}.characters.json", char_hash)
        return {"frames": scene["output"]["frame_count"], "camera": scene["camera"]}

    def t_render(self, task):
        fmt = next(f for f in self.formats if f["id"] == task.params["format"])
        sid = task.params["shot"]
        d = self.fmt_dir(fmt)
        scene_path = d / "scenes" / f"{sid}.scene.json"
        for sub in ("shots", "probes", "samples", "events"):
            (d / sub).mkdir(parents=True, exist_ok=True)
        info = M.render_shot(scene_path, d / "shots" / f"{sid}.mkv", d / "probes" / f"{sid}.probe.json",
                             d / "events" / f"{sid}.events.json", backend=self.backend, ssaa=self.ssaa, msaa=self.msaa,
                             timeout=self.timeout)
        n = info["frames"]
        for f in sorted({0, n // 2, n - 1}):
            M.extract_frame(d / "shots" / f"{sid}.mkv", M.frame_seek_time(f, self.p.fps_value), d / "samples" / f"{sid}_f{f:04d}.png")
        dump_json(d / "shots" / f"{sid}.render.json", info)
        return info

    def t_shotqa(self, task):
        fmt = next(f for f in self.formats if f["id"] == task.params["format"])
        sid = task.params["shot"]
        d = self.fmt_dir(fmt)
        scene = load_json(d / "scenes" / f"{sid}.scene.json")
        probe = load_json(d / "probes" / f"{sid}.probe.json")
        info = load_json(d / "shots" / f"{sid}.render.json")
        samples = sorted((d / "samples").glob(f"{sid}_f*.png"))
        rep = Q.shot_qa(self.p, fmt, sid, scene, probe, info, samples, camera_spec=active_camera(self.p.shots[sid], fmt))
        doc = rep.to_json(self.p.production["project_id"], {"shot": sid, "format": fmt["id"], "backend": info.get("backend")})
        dump_json(self.p.qa_dir / fmt["id"] / f"{sid}.json", doc)
        if not doc["passed"]:
            raise KageError(f"shot QA failed for {fmt['id']}/{sid}: " + "; ".join(f"{i['ref']}: {i['message']}" for i in rep.errors[:4]))
        return {"warnings": doc["warning_count"]}

    def t_continuity(self, task):
        fmt = next(f for f in self.formats if f["id"] == task.params["format"])
        d = self.fmt_dir(fmt)
        order = self.p.shot_order()
        probes = {s: load_json(d / "probes" / f"{s}.probe.json") for s in order}
        hashes = {s: {"_character_hash": load_json(d / "scenes" / f"{s}.characters.json")} for s in order}
        rep = Q.continuity_qa(self.p, fmt, probes, hashes)
        doc = rep.to_json(self.p.production["project_id"], {"format": fmt["id"]})
        dump_json(self.p.qa_dir / fmt["id"] / "continuity.json", doc)
        if not doc["passed"]:
            raise KageError(f"continuity QA failed ({fmt['id']}): " + "; ".join(f"{i['ref']}: {i['message']}" for i in rep.errors[:4]))
        return {"warnings": doc["warning_count"]}

    def t_mix(self, task):
        tl = self.timeline()
        plan = self.p.audio_plan
        total = tl["duration_s"]
        gen = self.line_records()
        stems_clips = {"dialogue": [], "sfx": [], "ambience": [], "music": []}
        provenance = []
        by_shot = {s["id"]: s for s in tl["shots"]}
        for c in tl["audio_clips"]:
            if c["kind"] == "dialogue":
                stems_clips["dialogue"].append((self.paths["lines"] / f"{c['line']}.wav", c["start_s"], {"gain_db": c["gain_db"]}))
                provenance.append({"clip": c["id"], "source": "voice", "voice": gen[c["line"]]["voice"],
                                   "provider": gen[c["line"]]["provider"], "provider_voice": gen[c["line"]]["provider_voice"]})
                continue
            asset = plan["assets"][c["asset"]]
            path, prov = A.asset_audio(c["asset"], asset, self.paths["assets"], c.get("duration_s"))
            provenance.append({"clip": c["id"], **prov})
            stems_clips[c["stem"]].append((path, c["start_s"], {k: c.get(k) for k in ("gain_db", "fade_in_s", "fade_out_s", "duration_s")}))
        # Footsteps synchronised to the engine's contact events (primary format; motion is format-independent).
        primary = self.p.primary_format()
        footsteps = []
        for rule in plan.get("animation_sfx", []):
            if rule["source"] != "footsteps":
                continue
            path, prov = A.asset_audio(rule["asset"], plan["assets"][rule["asset"]], self.paths["assets"])
            for sid in rule.get("shots", []):
                ev = load_json(self.p.build / primary["id"] / "events" / f"{sid}.events.json")
                for fs in ev["footsteps"]:
                    if fs["character"] not in rule.get("characters", []) or fs["kind"] not in rule.get("kinds", ["step"]):
                        continue
                    age = self.p.characters[fs["character"]]["identity"]["age_category"]
                    gain = rule.get("child_gain_db", rule["gain_db"]) if age in ("child", "young_child", "infant") else rule["gain_db"]
                    t = by_shot[sid]["start_s"] + fs["t"]
                    stems_clips["sfx"].append((path, t, {"gain_db": gain}))
                    footsteps.append({"shot": sid, "character": fs["character"], "t": round(t, 4), "foot": fs["foot"]})
        stems = {}
        self.paths["stems"].mkdir(parents=True, exist_ok=True)
        for name, clips in stems_clips.items():
            stems[name] = self.paths["stems"] / f"{name}.wav"
            A.build_stem(clips, total, stems[name])
        duck = next((c for c in plan.get("music", []) if c.get("duck_under_dialogue_db")), None)
        target = self.p.production["delivery"]
        master = self.paths["stems"] / "master.wav"
        mixrep = A.master_mix(stems, {"db": duck["duck_under_dialogue_db"]} if duck else None, master,
                              target["loudness_lufs"], target["true_peak_dbtp"], total)
        srt = self.p.output / f"{self.slug}.srt"
        srt.parent.mkdir(parents=True, exist_ok=True)
        A.write_srt(tl["subtitles"], srt)
        rep = Q.audio_qa(self.p, tl, gen, mixrep, stems, self.paths["stems"] / "music_ducked.wav")
        doc = rep.to_json(self.p.production["project_id"], {"mix": mixrep, "footstep_events": footsteps, "provenance": provenance,
                                                            "line_f0_hz": {k: v.get("f0_hz") for k, v in gen.items()},
                                                            "listening_review": "not performed by tooling; see qa/reviews.json"})
        dump_json(self.p.qa_dir / "audio.json", doc)
        out_stems = self.p.output / "stems"
        out_stems.mkdir(parents=True, exist_ok=True)
        for name, path in {**stems, "master": master}.items():
            shutil.copy2(path, out_stems / f"{name}.wav")
        if not doc["passed"]:
            raise KageError("audio QA failed: " + "; ".join(f"{i['ref']}: {i['message']}" for i in rep.errors[:4]))
        return {"lufs": mixrep["master"]["integrated_lufs"], "true_peak": mixrep["master"]["true_peak_dbtp"], "footsteps": len(footsteps)}

    def final_path(self, fmt: dict) -> Path:
        return self.p.output / f"{self.slug}_{fmt['id']}.mp4"

    def t_assemble(self, task):
        fmt = next(f for f in self.formats if f["id"] == task.params["format"])
        tl = self.timeline()
        d = self.fmt_dir(fmt)
        shots = [(d / "shots" / f"{s['id']}.mkv", s) for s in tl["shots"]]
        srt = self.p.output / f"{self.slug}.srt" if self.p.production["delivery"].get("subtitles") else None
        out = self.final_path(fmt)
        M.assemble(shots, self.paths["stems"] / "master.wav", srt, out, self.p.production["delivery"], self.p.fps)
        # Individual shot exports (with their slice of the final mix) for review.
        shot_dir = self.p.output / "shots" / fmt["id"]
        shot_dir.mkdir(parents=True, exist_ok=True)
        for path, s in shots:
            run(["ffmpeg", "-v", "error", "-y", "-i", path, "-ss", f"{s['start_s']:.6f}", "-t", f"{s['duration_s']:.6f}",
                 "-i", self.paths["stems"] / "master.wav", "-map", "0:v", "-map", "1:a", "-c:v", "libx264", "-crf", "18",
                 "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k", "-shortest", shot_dir / f"{s['id']}.mp4"], timeout=600)
        return {"file": rel(out, self.p.root)}

    def t_exportqa(self, task):
        fmt = next(f for f in self.formats if f["id"] == task.params["format"])
        tl = self.timeline()
        out = self.final_path(fmt)
        dl = self.p.production["delivery"]
        res = M.validate_export(out, width=fmt["width"], height=fmt["height"], fps=self.p.fps, duration=tl["duration_s"],
                                frames=tl["total_frames"], expect_audio=True, expect_subtitles=bool(dl.get("subtitles")),
                                sample_rate=dl["sample_rate"], channels=dl["channels"])
        # Shot order / content check: the delivered frame at each shot midpoint must
        # match an independent engine render of that shot frame.
        d = self.fmt_dir(fmt)
        tmp = self.p.build / "exportqa" / fmt["id"]
        tmp.mkdir(parents=True, exist_ok=True)
        frame_checks = []
        for s in tl["shots"]:
            local = s["frames"] // 2
            t = M.frame_seek_time(s["start_frame"] + local, self.p.fps_value)
            a, b = tmp / f"{s['id']}_final.png", tmp / f"{s['id']}_engine.png"
            M.extract_frame(out, t, a)
            M.render_frame_png(d / "scenes" / f"{s['id']}.scene.json", local, b, backend="soft")
            val = M.psnr(a, b)
            frame_checks.append({"shot": s["id"], "frame": s["start_frame"] + local, "psnr_db": round(val, 2), "passed": val >= 30.0})
        # Dialogue sync: the master gets louder right where each line starts.
        sync = []
        mono = tmp / "master_mono.wav"
        run(["ffmpeg", "-v", "error", "-y", "-i", out, "-vn", "-ac", "1", "-ar", "48000", "-c:a", "pcm_s16le", mono])
        for c in tl["audio_clips"]:
            if c["kind"] != "dialogue":
                continue
            before = A.rms_window(mono, max(0.0, c["start_s"] - 0.35), c["start_s"] - 0.05)
            during = A.rms_window(mono, c["start_s"] + 0.05, c["start_s"] + 0.35)
            sync.append({"line": c["line"], "start_s": c["start_s"], "rms_before": round(before, 4), "rms_during": round(during, 4),
                         "passed": during > before * 1.4})
        passed = res["passed"] and all(f["passed"] for f in frame_checks) and all(s["passed"] for s in sync)
        doc = {"schema": "kage.qa_report/1", "project_id": self.p.production["project_id"], "stage": f"final:{fmt['id']}",
               "passed": passed, "export": res, "frame_checks": frame_checks, "dialogue_sync": sync}
        dump_json(self.p.qa_dir / fmt["id"] / "final.json", doc)
        contact = self.p.output / "review" / f"contact_{fmt['id']}.png"
        contact.parent.mkdir(parents=True, exist_ok=True)
        imgs = sorted(tmp.glob("*_final.png"), key=lambda p: [s["id"] for s in tl["shots"]].index(p.name.rsplit("_final", 1)[0]))
        args = ["ffmpeg", "-v", "error", "-y"]
        for i in imgs:
            args += ["-i", i]
        tile_w = 480 if fmt["width"] >= fmt["height"] else 270
        args += ["-filter_complex", "".join(f"[{k}]scale={tile_w}:-2[s{k}];" for k in range(len(imgs)))
                 + "".join(f"[s{k}]" for k in range(len(imgs))) + f"hstack=inputs={len(imgs)}", contact]
        if len(imgs) > 1:
            run(args)
        if not passed:
            bad = [c["check"] for c in res["checks"] if not c["passed"]] + [f["shot"] for f in frame_checks if not f["passed"]] + \
                  [s["line"] for s in sync if not s["passed"]]
            raise KageError(f"final QA failed for {fmt['id']}: {bad}")
        return {"checks": len(res["checks"]), "min_psnr": min(f["psnr_db"] for f in frame_checks)}

    def t_deliver(self, task):
        tl = self.timeline()
        files = []
        for fmt in self.formats:
            out = self.final_path(fmt)
            files.append({"format": fmt["id"], "aspect": fmt["aspect"], "path": rel(out, self.p.root), "sha256": sha256_file(out),
                          "bytes": out.stat().st_size, "qa": rel(self.p.qa_dir / fmt["id"] / "final.json", self.p.root)})
        extra = sorted(self.p.output.glob("stems/*.wav")) + [self.p.output / f"{self.slug}.srt"]
        engine = find_engine()
        env = load_json(self.p.build / "environment.json") if (self.p.build / "environment.json").exists() else {}
        reviews = load_json(self.p.qa_dir / "reviews.json") if (self.p.qa_dir / "reviews.json").exists() else {"reviews": []}
        doc = {
            "schema": "kage.delivery/1", "project_id": self.p.production["project_id"], "title": self.p.production["title"],
            "version": self.p.production["version"], "created": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "duration_s": tl["duration_s"], "frames": tl["total_frames"], "fps": tl["fps"],
            "films": files,
            "supporting_files": [{"path": rel(p, self.p.root), "sha256": sha256_file(p)} for p in extra if p.exists()],
            "shot_exports": sorted(rel(p, self.p.root) for p in self.p.output.glob("shots/*/*.mp4")),
            "qa_reports": sorted(rel(p, self.p.root) for p in self.p.qa_dir.rglob("*.json")),
            "subjective_reviews": reviews["reviews"],
            "tools": {"kage_tools": TOOL_VERSION, "engine": str(engine) if engine else None, **env.get("tools", {})},
            "provenance": "Visuals rendered by the Kage C++/OpenGL engine from project data. Dialogue: macOS system voices. "
                          "SFX, ambience and music: procedurally generated by kage synth (no third-party material).",
        }
        dump_json(self.p.output / "delivery.json", doc)
        return {"films": len(files)}

    # ------------------------------------------------------------ graph
    def tasks(self) -> list[Task]:
        p = self.p
        lib_files = [LIBRARY_DIR / "components.json", LIBRARY_DIR / "age_profiles.json"]
        engine = find_engine()
        engine_files = [engine] if engine else []
        A_ = self.max_attempts
        tasks = [Task("validate", "validate", [], self.t_validate, params={"v": TOOL_VERSION},
                      input_files=self.canonical_files() + lib_files, outputs=[p.qa_dir / "planning.json"], max_attempts=1),
                 Task("identity", "identity", ["validate"], self.t_lineup, params={},
                      input_files=[p.files[c] for c in p.characters] + [p.files["bible"]] + lib_files + engine_files,
                      outputs=[p.qa_dir / "identity.json", p.output / "review" / "lineup_final.png"], resource="gpu", max_attempts=A_,
                      hash_deps=[])]  # validation is a gate, not an input
        voice_ids = []
        for ln in p.dialogue["lines"]:
            tid = f"voice:{ln['id']}"
            voice_ids.append(tid)
            tasks.append(Task(tid, "voice", ["validate"], self.t_voice,
                              params={"line": ln["id"], "text": ln["text"], "delivery": ln.get("delivery"), "voice": p.voices[ln["voice"]]},
                              outputs=[self.paths["lines"] / f"{ln['id']}.wav", self.paths["lines"] / f"{ln['id']}.json"], max_attempts=A_,
                              hash_deps=[]))
        tasks.append(Task("timeline", "timeline", voice_ids, self.t_timeline,
                          params={"shots": {s: p.shots[s].get("duration_s") for s in p.shots}, "timing": {s: p.shots[s].get("timing") for s in p.shots},
                                  "transitions": {s: [p.shots[s].get("transition_in"), p.shots[s].get("transition_out")] for s in p.shots}},
                          input_files=[p.files["production"], p.files["dialogue"], p.files["audio_plan"]] + [p.files[s] for s in p.scenes],
                          outputs=[self.paths["timeline"]], max_attempts=1))
        order = p.shot_order()
        for fmt in self.formats:
            d = self.fmt_dir(fmt)
            qa_ids = []
            for sid in order:
                comp, rend, sqa = f"compile:{fmt['id']}:{sid}", f"render:{fmt['id']}:{sid}", f"shotqa:{fmt['id']}:{sid}"
                deps_files = [p.files[k] for k in p.shot_dependencies(sid)] + lib_files + engine_files
                tasks.append(Task(comp, "compile", ["timeline", "identity"], self.t_compile,
                                  params={"format": fmt["id"], "fmt": fmt, "shot": sid, "timing": self._lazy_payload(sid)},
                                  input_files=deps_files, outputs=[d / "scenes" / f"{sid}.scene.json", d / "events" / f"{sid}.events.json"],
                                  max_attempts=1, hash_deps=[]))
                tasks.append(Task(rend, "render", [comp], self.t_render,
                                  params={"format": fmt["id"], "shot": sid, "backend": self.backend, "ssaa": self.ssaa, "msaa": self.msaa},
                                  input_files=[d / "scenes" / f"{sid}.scene.json"] + engine_files,
                                  outputs=[d / "shots" / f"{sid}.mkv", d / "probes" / f"{sid}.probe.json", d / "shots" / f"{sid}.render.json"],
                                  resource="gpu", max_attempts=A_))
                tasks.append(Task(sqa, "shotqa", [rend], self.t_shotqa, params={"format": fmt["id"], "shot": sid, "v": 1},
                                  outputs=[p.qa_dir / fmt["id"] / f"{sid}.json"], max_attempts=1))
                qa_ids.append(sqa)
            tasks.append(Task(f"continuity:{fmt['id']}", "continuity", [f"render:{fmt['id']}:{s}" for s in order], self.t_continuity,
                              params={"format": fmt["id"], "v": 1}, outputs=[p.qa_dir / fmt["id"] / "continuity.json"], max_attempts=1))
        primary = p.primary_format()
        if primary["id"] in [f["id"] for f in self.formats]:
            mix_deps = ["timeline"] + [f"compile:{primary['id']}:{s}" for s in order]
        else:
            mix_deps = ["timeline"] + [f"compile:{self.formats[0]['id']}:{s}" for s in order]
        tasks.append(Task("mix", "mix", mix_deps, self.t_mix, params={"plan": p.audio_plan, "delivery": p.production["delivery"]},
                          input_files=[self.paths["lines"] / f"{ln['id']}.wav" for ln in p.dialogue["lines"]]
                          + [self.p.build / primary["id"] / "events" / f"{s}.events.json" for s in order],
                          outputs=[self.paths["stems"] / "master.wav", p.qa_dir / "audio.json"], max_attempts=A_,
                          hash_deps=["timeline"]))
        final_ids = []
        for fmt in self.formats:
            deps = ["mix", f"continuity:{fmt['id']}"] + [f"shotqa:{fmt['id']}:{s}" for s in order] + [f"render:{fmt['id']}:{s}" for s in order]
            tasks.append(Task(f"assemble:{fmt['id']}", "assemble", deps, self.t_assemble, params={"format": fmt["id"], "delivery": p.production["delivery"]},
                              outputs=[self.final_path(fmt)], max_attempts=A_))
            tasks.append(Task(f"exportqa:{fmt['id']}", "exportqa", [f"assemble:{fmt['id']}"], self.t_exportqa, params={"format": fmt["id"], "v": 1},
                              outputs=[p.qa_dir / fmt["id"] / "final.json"], max_attempts=1))
            final_ids.append(f"exportqa:{fmt['id']}")
        tasks.append(Task("deliver", "deliver", final_ids, self.t_deliver, params={},
                          input_files=[p.qa_dir / "reviews.json", self.paths["approvals"]], outputs=[p.output / "delivery.json"], max_attempts=1))
        return tasks

    def _lazy_payload(self, sid: str):
        """Timing payload for a shot's compile hash (only this shot's frames and lines)."""
        return _LazyPayload(self, sid)


class _LazyPayload:
    """Evaluated when the scheduler hashes the task (after the timeline exists)."""

    def __init__(self, pipe: Pipeline, sid: str):
        self.pipe, self.sid = pipe, sid

    def __json__(self):
        if not self.pipe.paths["timeline"].exists():
            return None
        return self.pipe.shot_timeline_payload(self.sid)
