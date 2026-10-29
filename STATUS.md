# Project Kage: status report (2026-10-29)

Environment used for all results below: macOS 26.6.2 on an Apple M4, Apple clang 21, CMake 4.4.5,
FFmpeg 9.0.2 (libx264/AAC/FFV1), GLFW 3.5.1, Python 3.14 (stdlib only).

## Where things are
| | Path |
|---|---|
| Skill (Claude Code project skill) | `.claude/skills/stickman-film-production/` (`SKILL.md`, `reference/` (13 docs), `schemas/` (20), `library/`, `templates/`, `scripts/kage.py` + `kagelib/`) |
| Engine | `engine/` (C++17, about 2.9k lines, `kage_render` + `kage_tests`) |
| Example film project | `examples/train-journey/` |
| Tests | `tests/` (Python), `engine/tests/tests.cpp` |

## Implemented and tested
| Capability | Evidence |
|---|---|
| Geometry: concave fills, holes, self-intersection rejection, strokes with caps/joins, adaptive curves | engine tests `triangulation`, `stroke` |
| Shared rig, FK, 2-bone IK, age-aware proportions (child is not a scaled adult) | `rig_fk_ik`, `age_postures`, Python `test_child_is_not_a_scaled_adult` |
| Gender presentation independent of geometry | `test_presentation_does_not_drive_geometry` (compiled character byte-identical across presentations) |
| Motion: walk/run with no foot sliding, turn (rigid head), sit/stand with floor constraint, gestures, talk, look, lean, reach (IK hand-holding), pick-up/put-down, keyframes | `walk_no_foot_sliding` (< 1 mm per 1/240 s), `sit_transition_floor`, `turn_and_mouth`, `prop_attachment` |
| Deterministic, order-independent frames | `deterministic_rendering`, `test_frames_are_deterministic_and_order_independent` |
| OpenGL 3.3 backend + software backend | `gl_vs_soft`: mean absolute difference 0.29/255 (Apple M4, GL 4.1 Metal, 4× MSAA) |
| Scene contract validation with precise error paths | `scene_validation`, `test_invalid_scene_exit_code` |
| 20 JSON Schemas, stdlib validator | Python schema tests. The reference `jsonschema` 4.26 (in a throwaway venv) accepts every schema against the 2020-12 metaschema and validates 36 example and generated documents with 0 failures |
| Planning QA (coverage, references, palette, voice lock, aspect ratios, approvals/version lock, timing) | `test_planning.PlanningValidationTest` (11 tests) |
| Semantic camera with per-aspect variants (16:9, 9:16, 4:5, 5:4, 1:1) | `test_camera_fits_every_aspect` |
| Scheduler: DAG, content-hash cache, early cutoff, selective invalidation, bounded retries, blocking, cycle detection, GPU concurrency limit, relocatable caches, per-project run lock | `SchedulerTest` (7), `ProjectLockTest`, `test_transient_engine_fault_is_retried_by_scheduler` (real engine fault injection) |
| Selective regeneration on a real project | `test_editing_one_line_reruns_only_dependents`: editing one line re-runs that voice, the timeline, and that shot's compile/render; unrelated shots and other voices stay cached |
| Audio: deterministic synth, envelopes, pitch estimate, exact-length stems, SRT | `AudioTest` (4) |
| Export validation reads real files | `test_real_file_checks` (positive and negative cases) |

Test results (latest run): engine **151 checks, 0 failures, 0 skipped**; Python **51 tests OK**, with 1
skipped under the system Python because the reference `jsonschema` library is not installed. That
test passes under the venv where it is.

## Real end-to-end production of the example (completed)
`kage.py run examples/train-journey` runs **44 tasks** (about 24 s from scratch; 0.03 s when nothing changed):
- Films: `output/the_evening_train_landscape.mp4` (1280×720) and `…_vertical.mp4` (720×1280).
  Each is 25.58 s, 614 frames at 24/1 fps, H.264 yuv420p plus AAC 48 kHz stereo plus mov_text subtitles.
  Hashes are in `output/delivery.json`.
- Export QA, per format (`qa/<fmt>/final.json`): 16/16 container and stream checks pass, including exact frame count, full decode
  and audio/video duration match. Each shot's midpoint frame in the delivered film matches an independent
  engine render at **36.4–38.9 dB PSNR**. Each dialogue line's onset is audible at its timeline position.
- Audio (`qa/audio.json`): integrated loudness **−16.7 LUFS**, true peak **−1.5 dBTP** (target −16 / −1.5). Music ducks
  **6.9 dB** under dialogue (6 dB requested). 17 footsteps are placed at engine contact events. Voices come from
  macOS `say` (Samantha, Flo, Reed). Estimated median pitch: 167 / 229 / 76 Hz.
- Identity (`qa/identity.json` + `output/review/lineup_*.png`): silhouette IoU 0.39–0.66, all pairs distinct.
- Continuity and shot QA pass in both formats. Two warnings remain, both intended staging: an entrance near the frame edge,
  and a non-subject leaving the vertical follow frame.
- Also produced: animatic and storyboard (`output/review/animatic_*.mp4`, `storyboard_*.png`), per-shot exports
  (`output/shots/<fmt>/`), stems (`output/stems/`), SRT, task graph (`build/tasks.json`).

### Problems found by QA or visual review during development (and fixed)
Maya's hair was invisible and sleeves were unreadable (lineup review) · the head collapsed mid-turn · feet sank during
sit-down (motion QA) · the interior wall stopped short and the window showed solid hill (contact-sheet review,
leading to a new `parallax_y` engine feature) · framing QA was too naive for medium and close framings · an FFmpeg 9 option
had been removed · a stem filter graph hung · loudness was capped by true peak (fixed with a limiter stage) · an
off-by-one in frame seeking was found by the PSNR check · the pitch estimator made subharmonic errors · cache keys
used absolute paths · validation output rippled into every task · concurrent runs could overwrite each other (fixed with a lock).

## Not verified / limitations (honest list)
- **No listening review.** The authoring agent cannot hear. Voice quality, intelligibility, emotion, music and mix
  balance were checked only with objective measures. This is recorded as `changes_requested` in `qa/reviews.json`;
  a human should listen before release.
- **Visual review** was done by the model (Claude, image-capable) on lineups, contact sheets and full-resolution frames.
  It was not done by a human. The approvals in `build/approvals.json` were recorded by the example's author (Claude),
  not by a human client.
- **Windows and Linux were not built or tested.** The code has portable paths (`_WIN32` binary stdout, runtime GL loading,
  CMake), but this is unverified. The bundled voice adapter works on macOS only.
- **Voice provider limits:** `say` offers no emotion or style control and no word or phoneme timing. Viseme mouth mode is
  implemented in the engine, but the compiler falls back to amplitude-driven "simple" mode and says so.
- **Art scope:** profile-view rig only (no 3/4 or front-facing bodies); no hands or fingers; no camera shake or depth of field;
  no animals with dedicated rigs (birds use animated parts).
- Cross-machine determinism is within tolerance, not bit-exact (driver antialiasing and libm differences).
- Pipeline code changes do not invalidate caches automatically; use `--force` after upgrading the tools.
- Dissolve transitions are implemented in assembly but are not exercised by the example. Multilingual tracks are
  designed for (per-language manifests, timing from generated durations) but not implemented end to end.

## Recommended next stage
1. A human listening and visual pass on the example. Then port tests to Linux CI (`--backend soft`) and Windows (MSVC + GLFW).
2. A TTS adapter with word or phoneme timestamps, to enable viseme lip-sync and emotion control.
3. A three-quarter/front-view rig variant and hand shapes, plus more actions (climb, carry two-handed, dance cycle).
4. Multilingual delivery (alternate audio tracks and subtitles per language) and dissolve coverage in the example.
5. A longer stress film (30+ shots) to measure scheduler throughput and cache behavior at scale.
