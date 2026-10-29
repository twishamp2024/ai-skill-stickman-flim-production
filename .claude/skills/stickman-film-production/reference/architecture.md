# Architecture

```
story prompt ─► [agent + this skill] ─► canonical project JSON ─► kage.py (validate, compile, schedule)
                                                                    │
            ┌───────────────────────────────────────────────────────┼─────────────────────────┐
            ▼                                                       ▼                         ▼
   voice adapter (macOS say)                      engine scene JSON (contract v1)     procedural synth
   → line WAV + envelope                          → kage_render (C++17, OpenGL/soft)   → sfx/ambience/music WAV
            │                                       → raw RGB → FFmpeg FFV1 shots              │
            └──────────────► master timeline ◄──────── probe + events (footsteps) ───────────────┘
                                   │
                         stems → sidechain ducking → limiter → loudnorm → master.wav
                                   │
                    FFmpeg assembly (fades/dissolves, H.264/AAC, mov_text) → export QA → delivery.json
```

## Components and ownership

| Concern | Lives in | Owner role |
|---|---|---|
| Workflow, rules, decisions | `SKILL.md`, `reference/` | director |
| Data contracts | `schemas/*.schema.json` (JSON Schema 2020-12) | visual-bible/continuity owner |
| Reusable appearance parts, age profiles | `library/components.json`, `library/age_profiles.json` | character designer (versioned) |
| Orchestration tools | `scripts/kage.py`, `scripts/kagelib/` | all roles (read), maintainers (write) |
| Rendering engine | repository `engine/` (C++17, CMake) | engine maintainer |
| One film | project directory | production roles per file |

### `kagelib` modules

`project` (load + ID index + impact) · `validate` (planning QA) · `characters` (rig from age
profile, component binding, lineups) · `timeline` (shot timing, audio placement, subtitles) ·
`compile` (shot → engine scene, semantic camera) · `audio` (voice adapter, synth, stems, mastering) ·
`media` (render/encode, assembly, export validation) · `qa` (render/framing/motion/continuity/audio) ·
`scheduler` (DAG, caching, retries, concurrency) · `pipeline` (task graph) · `schema` (stdlib JSON
Schema validator) · `png` (PNG decoder for QA).

### Engine modules (`engine/src`)

`json` (parser) · `geometry` (ear-clipping with holes, stroking, curves) · `rig` (shared skeleton,
FK, 2-bone IK) · `scene` (contract parser + validation) · `motion` (action library, pure function
of time) · `frame` (camera, layers, draw ordering, probes) · `raster_soft` (reference rasterizer) ·
`raster_gl` (OpenGL 3.3 core via GLFW) · `image` (PNG writer) · `main` (CLI).

## Why this split

- The engine never sees story language, palette names or marks: everything it gets is numeric and
  validated, so rendering bugs and planning bugs are separable and each is testable.
- Every expensive step is a cached task keyed by content hashes, so long films are produced and
  repaired incrementally.
- Platform-specific code is confined to `raster_gl.cpp` (context creation) and the voice adapter.

## Cross-platform status

| Platform | Engine build | OpenGL backend | Voice adapter | Tested |
|---|---|---|---|---|
| macOS (arm64) | CMake + Apple clang | GLFW, GL 4.1 core (Metal) | `say` | **yes** (this repository) |
| Windows | CMake + MSVC/clang-cl (code has `_WIN32` paths) | GLFW, needs GL 3.3 driver | none bundled | **no** |
| Linux | CMake + gcc/clang | GLFW (X11/Wayland); headless → `--backend soft` | none bundled | **no** |

Untested platforms are a documented next step, not a claim.
