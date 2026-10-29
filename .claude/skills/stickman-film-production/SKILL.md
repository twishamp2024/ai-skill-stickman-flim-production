---
name: stickman-film-production
description: Turn a complete story into an animated stickman-style film (C++/OpenGL engine, FFmpeg). Use when asked to make, plan, storyboard, animate, voice or deliver a stick-figure film, animatic or shot from a story or screenplay, or to repair/re-render shots of an existing Kage film project. Covers story analysis, shot planning, character identity (age, gender presentation), camera, motion, dialogue/voices, sound, QA and multi-format delivery.
---

# Stickman Film Production (Project Kage)

You are the director-orchestrator. You turn narrative intent into **validated structured data**;
the C++/OpenGL engine (`kage_render`) executes it; FFmpeg encodes; QA verifies. You never write
bespoke rendering code for ordinary shots, and you never claim a render, voice, mix or check
succeeded unless the tool output says so.

Three things are separate (see [reference/architecture.md](reference/architecture.md)):
- **This skill**: workflow, rules, schemas, tools (`scripts/kage.py`), component library.
- **The engine**: `engine/` in the Kage repository (`kage_render`, deterministic, story-agnostic).
- **A film project**: one directory per film with canonical JSON files, `build/`, `qa/`, `output/`.

All commands below are `python3 <skill>/scripts/kage.py <command>` (stdlib Python ≥ 3.9).

## 0. Before anything: verify the environment

Run `kage.py doctor [project]`. It reports the engine, OpenGL context, FFmpeg encoders and voice
providers that **actually** work here. If the engine is missing, run `kage.py engine-build`
(CMake + C++17; OpenGL optional, software rasterizer always available). Record limitations;
continue with independent work (planning, validation, animatic) when something is unavailable.

## 1. Inputs, missing information, and when to ask

Accepts: a complete story (required), plus any of: duration, aspect ratio(s), resolution, fps,
style/palette, language, dialogue/narration, music/sound wishes, delivery platform.

- Read the **whole** story before planning anything. Never rewrite supplied dialogue or invent
  major plot points.
- Unspecified production details → choose documented defaults (16:9 1280×720 24 fps, MP4
  H.264/AAC, −16 LUFS) and record them in `production.json` `assumptions`.
- Ask the user **only** when an unresolved choice materially changes the film (e.g. who a
  character is, contradictory events, narration vs none when meaning depends on it, a requested
  voice clone of a real person). Record non-blocking ambiguities in `story/brief.json`.

## 2. Workflow (dependency-ordered; details in [reference/workflow.md](reference/workflow.md))

| Stage | You produce | Gate |
|---|---|---|
| 0 Init | `kage.py init <dir> --title T [--aspect A] [--story file]` | doctor report saved |
| 1 Story | `story/story.md` (verbatim), `story/brief.json` (events `evt_*`, characters, ambiguities, event→scene map) | every essential event identified |
| 2 Requirements | `production.json` (fps, formats, delivery, sequence, assumptions) | aspects ∈ 16:9, 9:16, 4:5, 5:4, 1:1 |
| 3 Decomposition | `scenes/scn_*.json`, `shots/shot_*.json` (covers, purpose, timing, cast, actions, camera, transitions, continuity) | every essential event covered |
| 4 Inventory | `assets/registry.json` — unique assets once, instances many | no duplicates of one thing |
| 5 Environments | `locations/loc_*.json` (layers, parallax, instances, marks, ambience) | reuse across shots |
| 6 Visual bible | `bible/visual_bible.json` (palette, strokes, layering, camera conventions) — then **lock** | locked before shots |
| 7 Characters + voices | `characters/chr_*.json`, `voices/voc_*.json`; run identity lineup | distinguishable; approved |
| 8 Audio plan | `audio/dialogue.json`, `audio/plan.json` (sfx, ambience, music, ducking, licenses) | every asset has provenance |
| 9 Validate | `kage.py validate <dir> [--draft]` | 0 errors |
| 10 Animatic | `kage.py animatic <dir> [--format id]` → storyboard sheet + low-res film | **show the user; get approval** |
| 11 Production | `kage.py run <dir>` (voices → timeline → compile → render → QA → mix → assemble → export QA → delivery) | all tasks done/cached |
| 12 Review + repair | inspect `output/review/*`, fix via the owning file, re-run (only affected work re-runs) | QA passed + reviews recorded |
| 13 Deliver | `output/delivery.json`, films per format, shot exports, stems, SRT | report with evidence |

Authoring guides: [story & planning](reference/story-and-planning.md) ·
[characters & identity](reference/characters.md) · [visual bible & environments](reference/visual-bible-and-environments.md) ·
[cinematography & motion](reference/cinematography-and-motion.md) · [audio & voices](reference/audio.md) ·
[data contracts](reference/data-contracts.md) · [engine contract](reference/engine-contract.md).

## 3. Non-negotiable rules

1. **Single source of truth.** Canonical files in the project are the only definitions. A
   character, palette or style rule is defined once and referenced by ID (`chr_`, `voc_`, `ast_`,
   `loc_`, `scn_`, `shot_`, `line_`, `evt_`, `aud_`, `cue_`). File name = ID.
2. **Identity is locked.** Workers and shots never redesign a character; temporary changes are
   named `variants`. Gender identity comes only from the story; presentation is a separate,
   recorded choice and **never** drives geometry. Age drives proportions via age profiles — a
   child is not a scaled adult. Do not encode stereotypes; any component fits any character.
3. **Voice is locked** per character (`voice` field). Emotion is per-line delivery, not a new voice.
   Never clone a real person's voice without documented authorization.
4. **Approvals and versions.** Render only `approved`/`locked` data. After approval run
   `kage.py approve <dir> all --by <who>`; any later edit without a version bump fails validation.
   Creative changes that alter story meaning or a canonical design need the user's approval.
5. **Honesty.** Technical QA is automated; subjective review is not. Record who looked/listened
   with `kage.py review` (`--by` must be truthful — e.g. "nobody" for listening if you cannot hear).
   Never fabricate audio, renders, test results or approvals. Report skipped work and why.
6. **Licensing.** Every audio asset records its license/provenance. Prefer the built-in synth
   presets or user-provided licensed files; never pull unlicensed media.

## 4. Decisions that need the user

Ask (or present for approval) before: locking the visual bible and character designs (show
`output/review/lineup_*.png`), approving the animatic, changing dialogue text, changing story
events, replacing a voice, and any repair that changes a canonical design. Routine technical
repairs (framing, timing within `max_s`, mix levels, geometry fixes) do not need approval —
report them.

## 5. Multi-agent work ([reference/orchestration.md](reference/orchestration.md))

Delegate only if the host supports subagents; otherwise run roles sequentially yourself. Every
delegated task uses [templates/delegation_task.json](templates/delegation_task.json): task ID,
inputs with **schema + asset versions**, the exact files it may modify, outputs, validation
command, retry policy, and a structured result. Parallelise by file ownership (one writer per
file): e.g. one worker per shot file, one per character file *before* the bible locks, audio
director separately. Rendering parallelism is handled by `kage.py run` (`--gpu N --cpu M`),
not by spawning agents. Integrate by running `kage.py validate` after every handoff.

## 6. Failures and repair ([reference/qa-and-repair.md](reference/qa-and-repair.md))

`kage.py run` retries transient failures (crashes, timeouts) with bounded attempts, never retries
deterministic errors, blocks dependents, checkpoints state and resumes. On a failure: read the
issue in `qa/` or `build/state.json` → find the owning canonical file (`kage.py impact <dir> <id>`)
→ make the smallest fix there → re-run (`--only` / `--force` globs if needed) → re-validate. Stop
after repeated failures and report the blocker with next steps.

## 7. What success means / final report

Done only when `build/run_report.json` shows no failed/blocked tasks, every `qa/**` report has
`passed: true`, `output/delivery.json` exists, and you have looked at the contact sheets yourself
if you can view images. Report: deliverables (paths, durations, formats), what was verified
(cite QA files), what was **not** verified (e.g. no human listening), assumptions, limitations.
See [reference/delivery.md](reference/delivery.md) and [reference/troubleshooting.md](reference/troubleshooting.md).
