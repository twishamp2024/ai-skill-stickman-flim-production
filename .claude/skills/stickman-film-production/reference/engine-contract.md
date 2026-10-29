# Engine integration (`kage_render`)

Source: repository `engine/` (C++17, CMake ≥ 3.16). Build: `kage.py engine-build` or
`cmake -S engine -B engine/build -DCMAKE_BUILD_TYPE=Release && cmake --build engine/build && ctest --test-dir engine/build`.
The skill finds the binary via `$KAGE_ENGINE`, `engine.local.json` (written by `engine-build`),
the source tree's `engine/build/`, or `PATH`.

## Requirements
- C++17 compiler (tested: Apple clang 21). No third-party code is vendored.
- OpenGL backend (optional): GLFW ≥ 3.3 and an OpenGL 3.3 core context. GL entry points are
  loaded through `glfwGetProcAddress`; no system GL headers or loader library are needed.
  `-DKAGE_WITH_GL=OFF` builds the software rasterizer only.
- FFmpeg (separate process) for encoding; the engine only produces frames.

## CLI
```
kage_render SCENE.json [--info] [--validate] [--backend auto|gl|soft] [--ssaa N] [--msaa N]
            [--style final|flat|silhouette] [--frames A:B | --frame N]
            [--png PATH] [--png-dir DIR --png-every K] [--raw PATH|-]
            [--probe PATH] [--events PATH] [--stats]
```
Exit codes: 0 ok · 1 usage · 2 invalid scene/geometry · 3 requested backend unavailable ·
4 render/IO failure · 75 injected transient fault (`KAGE_FAULT_INJECT_ONCE=<marker>`, tests only).

- `--raw -` streams rgb24 frames for `ffmpeg -f rawvideo -pix_fmt rgb24 -s WxH -r N/D -i -`.
- `--probe` writes per-frame camera, entity pixel bounding boxes, visibility, safe-area flags,
  character root/head/hands/soles, head pixel circle, facing, posture, mouth, prop attachment.
- `--events` writes footstep contacts (time, character, foot, x, kind) and prop attachments.
- `--style silhouette|flat` power identity lineups and the animatic.

## Rendering model
Every frame is a pure function of the scene and the absolute frame time
(`time_offset + frame / fps`): camera (keys + smoothed follow), props (keys, drift, tiling,
animated parts, attachments), characters (state actions, layered overlays, IK, mouth, blink).
Draw order: layer `order` → entity `z` → declaration order → primitive `z`. Fills are triangulated
(ear clipping with hole bridging; concave OK; self-intersection rejected), strokes expanded to
triangles with caps/joins, curves tessellated adaptively (≤0.2 px chord error).

Backends: `soft` (supersampled edge-function rasterizer, bit-exact for a given binary and input,
used for reference frames and headless machines) and `gl` (OpenGL 3.3 core, MSAA up to the
driver limit; 4× on Apple M4). Measured agreement: mean absolute difference 0.29/255 on the test
scene. `auto` uses GL when a context can be created, else falls back to `soft` with a message.

## Determinism
- Same binary + scene + frame → identical bytes with `soft` (tested out of order). GL output is
  deterministic per driver but differs slightly across GPUs/drivers (antialiasing patterns).
- `-ffp-contract=off` avoids FMA-dependent results across optimisation levels; different
  compilers/CPUs may still differ in the last bits of transcendental functions — compare with
  tolerances (export QA uses PSNR ≥ 30 dB), not hashes, across machines.
- No wall-clock time, no random state, no accumulated deltas.

## Extending
New action → `motion.cpp` (+ parser in `scene.cpp`, schema in `action.schema.json`, test). New
primitive → `scene.cpp` + `frame.cpp` + `common.schema.json`. Keep the engine story-agnostic and
bump `KAGE_ENGINE_VERSION` (task caches include the engine binary hash).
