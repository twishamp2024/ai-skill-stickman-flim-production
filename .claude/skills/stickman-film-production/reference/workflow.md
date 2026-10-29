# Production workflow (stage by stage)

Each stage lists inputs → outputs → gate. Stages may overlap where dependencies allow (e.g. audio
planning in parallel with environments), but gates are not skipped.

## Stage 0 — Project initialization
- `kage.py doctor` → note what works (engine, OpenGL, FFmpeg encoders, voices).
- `kage.py init films/<slug> --title "…" [--aspect 9:16] [--story story.txt]` creates the skeleton
  from `templates/project/`. The project ID is `prj_<slug>`; IDs are stable forever.
- Versioning: every canonical file has a semver `version` and `status`
  (`draft → review → approved → locked`). Bump `version` whenever approved content changes.

## Stage 1 — Story comprehension → `story/brief.json`
Read the full story first. Extract plot, subplots, chronology/flashbacks, characters + relationships
(stated gender only if stated), dialogue (verbatim), narration, emotional arc, actions, reveals,
locations and changes, props, implied sounds, audience/tone, ambiguities. Number events `evt_01…`
in story order; mark importance `essential | supporting | context`. Map events → scenes.
See [story-and-planning.md](story-and-planning.md).

## Stage 2 — Production requirements → `production.json`
fps (rational), `formats[]` (id, aspect, width, height, one `primary`), delivery (codec, pixel
format, audio, loudness, subtitles), `sequence` (scene order), `assumptions`. Scene coordinates are
metres and independent of resolution; extra formats get camera *variants*, never blind crops.

## Stage 3 — Scenes and shots
One file per scene and per shot. A shot has: `covers` (events), `purpose`, `duration_s` +
`timing.flex/max_s`, `cast` (start pose + actions), `props`, semantic `camera` (+ per-aspect
`variants`), `dialogue` (line IDs), transitions, continuity declarations (`offscreen`, `entrances`,
`exits`), `framing.allow_partial`. Prefer fewer, purposeful shots: 3–8 s for dialogue beats,
longer for action that needs it. See [cinematography-and-motion.md](cinematography-and-motion.md).

## Stage 4 — Element inventory → `assets/registry.json`
Every unique thing once (`ast_*`), instanced many times by locations/shots. Record category,
description, status/version, tileability, composition (`composed_of`). Characters are not assets
here — they live in `characters/`.

## Stage 5 — Environments → `locations/*.json`
Layers (order, parallax, `parallax_y`, depth), instances, staging `marks`, background color,
ambience. Plan with the shots that use the location: check every framing that location must
support before locking it. See [visual-bible-and-environments.md](visual-bible-and-environments.md).

## Stage 6 — Visual bible (lock before shot production)
Palette (named colors only), stroke widths/caps/joins, fill rules, transparency policy, layering,
camera conventions, approval rules. Status `locked`; changes are versioned and re-approved.

## Stage 7 — Characters and voices
Profiles per [characters.md](characters.md) and [audio.md](audio.md). Run `kage.py run <dir> --only identity`
to produce `output/review/lineup_{final,flat,silhouette,small}.png` and `qa/identity.json`
(silhouette IoU + feature differences). Show the lineup to the user; fix similar pairs before approval.

## Stage 8 — Audio planning
`audio/dialogue.json` (lines with exact text, voice, shot anchor, delivery), `audio/plan.json`
(assets with licenses, anchored sfx, animation-driven footsteps, ambience per location, music cues
with ducking, deliberate silence).

## Stage 9 — Planning QA
`kage.py validate <dir>` (use `--draft` before approvals). Fix every error at its source.

## Stage 10 — Storyboard / animatic
`kage.py animatic <dir> [--format id]` → `output/review/storyboard_<fmt>.png` (3 columns, shot order
as in `animatic_<fmt>.json`), `animatic_<fmt>.mp4` (flat style, 12 fps, estimated or generated
timing). Review story clarity, order, durations, entrances/exits, framing, reaction time. **Get
user approval**, then set statuses to `approved` and `kage.py approve <dir> all --by <name>`.

## Stage 11 — Production run
`kage.py plan <dir>` (writes `build/tasks.json`) then `kage.py run <dir>`. Order is enforced by
the task graph: validate → identity → voice:* → timeline → compile:* → render:* → shotqa:* →
continuity:* → mix → assemble:* → exportqa:* → deliver.

## Stage 12 — Review and repair
Look at `output/review/contact_<fmt>.png` and frames from the films; record reviews with
`kage.py review`. Repair per [qa-and-repair.md](qa-and-repair.md); re-run (only affected work runs).

## Stage 13 — Delivery
See [delivery.md](delivery.md). Report deliverables with evidence and explicit limitations.
