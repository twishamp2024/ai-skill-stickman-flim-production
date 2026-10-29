# Data contracts

All structured data is JSON validated by JSON Schema 2020-12 files in `../schemas/` (stdlib
validator in `kagelib/schema.py`; cross-checked against the reference `jsonschema` package in
tests), plus semantic checks in `kagelib/validate.py`.

| # | Contract | Schema | File (project) |
|---|---|---|---|
| 1 | Production manifest | `production.schema.json` | `production.json` |
| 2 | Story brief | `story_brief.schema.json` | `story/brief.json` |
| 3 | Scene | `scene.schema.json` | `scenes/scn_*.json` |
| 4 | Shot | `shot.schema.json` | `shots/shot_*.json` |
| 5 | Character identity | `character.schema.json` | `characters/chr_*.json` |
| 6 | Voice identity | `voice.schema.json` | `voices/voc_*.json` |
| 7 | Asset registry | `asset_registry.schema.json` | `assets/registry.json` |
| 8 | Background / environment | `location.schema.json` | `locations/loc_*.json` |
| 9 | Camera | `camera.schema.json` | inside shots |
| 10 | Rig + animation tracks (engine) | `engine_scene.schema.json` | `build/<fmt>/scenes/*.scene.json` |
| 11 | Action / motion event | `action.schema.json` | inside shots and engine scenes |
| 12 | Dialogue lines | `dialogue.schema.json` | `audio/dialogue.json` |
| 13 | Audio assets + tracks | `audio_plan.schema.json` | `audio/plan.json` |
| 14 | Master timeline | `timeline.schema.json` | `build/timeline.json` (generated) |
| 15 | Render configuration | `render_config.schema.json` | `render.json` |
| 16 | Task + dependency graph | `task_graph.schema.json` | `build/tasks.json` (generated) |
| 17 | QA report | `qa_report.schema.json` | `qa/**.json` (generated) |
| 18 | Final delivery manifest | `delivery.schema.json` | `output/delivery.json` (generated) |
| — | Visual bible | `visual_bible.schema.json` | `bible/visual_bible.json` |
| — | Shared definitions | `common.schema.json` | — |

## Units and representations
Time: seconds (float) in authored data; integer frame indices in the timeline; frame rate as a
rational `{num, den}`. Distances: metres, y up. Angles: degrees. Colors: palette names in project
files, `#RRGGBB[AA]` sRGB (straight alpha) in engine scenes. Durations are non-negative.

## IDs
Prefix + `[a-z0-9_]+`: `prj_ chr_ voc_ ast_ loc_ scn_ shot_ line_ evt_ aud_ cue_`, instances
`inst_`/`prop_`. File name equals ID. IDs never change once used; rename = new ID + migration.

## Versions and propagation
Every canonical file: semver `version` + `status`. `kage.py approve` records content hashes in
`build/approvals.json`; validation fails if approved content changes without a version bump.
Every pipeline task is keyed by the hash of its inputs (files + parameters + upstream outputs it
declares relevant). A change therefore re-runs exactly the dependent work:

| Change | Re-runs |
|---|---|
| one dialogue line | that voice → timeline → compile/render of that shot (all formats) → mix → assemble |
| one character | identity lineup → compile/render of shots containing it |
| location / asset | compile/render of shots in that location / using it (`kage.py impact`) |
| visual bible / engine binary | everything visual |
| audio plan only | mix → assemble |

Early cut-off: if a task re-runs but its outputs are byte-identical, dependents stay cached.
Changing the **tool code** (`kagelib`) does not invalidate caches by itself — use
`kage.py run <dir> --force "<glob>"` after upgrading tools.

## Engine scene contract (v1)
The engine reads only `build/<fmt>/scenes/<shot>.scene.json`: output size/fps/frame count,
layers, camera keys/follow, props (resolved primitives + parts + motion), characters (numeric
rig, resolved components, start state, actions, mouth track). No palette names, no marks, no
story text. Version `kage_scene_version: "1.x"`; the engine rejects other majors.
