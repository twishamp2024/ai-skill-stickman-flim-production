# Troubleshooting

| Problem | Cause / fix |
|---|---|
| `kage_render not found` | `kage.py engine-build`, or set `KAGE_ENGINE=/path/to/kage_render` |
| `OpenGL unavailable … using software rasterizer` | no display/GL 3.3 context (SSH, CI, VM). Output is still correct; use `--backend soft` explicitly and raise `--cpu` |
| `exit 3: OpenGL backend unavailable` | `--backend gl` was forced; use `auto` or `soft` |
| `invalid scene: scene.characters[0]…: unknown joint 'x'` | compiled scene is wrong → fix the authored file the path points to; run `kage.py validate` |
| `polygon outline self-intersects` | fix the asset's points (no crossing edges; holes inside the outline) |
| `approved content changed without a version bump` | bump `version` in that file, review, `kage.py approve <dir> <id> --by …` |
| `line … ends at … but shot is …` | set `timing.flex` + `max_s`, move `offset_s`, or shorten (with approval) |
| shot QA `cut off` | add a camera variant for that aspect or widen framing; see qa-and-repair.md |
| `Unrecognized option 'filter_complex_script'` | FFmpeg ≥ 8 removed it; tools use `-/filter_complex` automatically (needs FFmpeg ≥ 7) |
| voice tasks fail on Linux/Windows | the bundled `macos_say` adapter is macOS-only; provide recorded WAVs (`source.type: file`) or add an adapter |
| a task keeps failing after retries | `kage.py status <dir>`; read `retry_history` in `build/state.json`; fix the cause, rerun |
| results look stale after upgrading tools | caches key on data + engine binary, not tool code: `kage.py run <dir> --force "<glob>"` |
| want to inspect a moment | `kage.py render-frames <dir> --shot S --format F --frames 100:110` |
| need to know what a change affects | `kage.py impact <dir> <id>` |
