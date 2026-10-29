# Quality assurance and repair

Deterministic checks run automatically; each writes a `kage.qa_report/1` under `qa/`. A QA error
fails its task, which blocks delivery. Subjective review is recorded separately and truthfully.

| Stage | Report | Checks |
|---|---|---|
| A Planning | `qa/planning.json` | schemas, IDs, aspect/resolution, story coverage, structure, references (assets, marks, targets, voices, palette), component slots, approvals/version locks, timing (estimated), audio licenses |
| B Asset/identity | `qa/identity.json` + `output/review/lineup_*` | rig height vs profile, palette, silhouette IoU, feature differences, small-screen preview |
| C Motion | `qa/<fmt>/<shot>.json` | per-frame head jumps (pose pops), hand jumps, feet below ground, floating feet; engine tests cover foot sliding, IK reach, sit transitions |
| D Render | same | frame count, dimensions, blank frames, palette conformity, invalid geometry (engine rejects) |
| E Continuity | `qa/<fmt>/continuity.json` | facing, posture, position, appearance hash, undeclared disappearance, left-right order across cuts |
| F Audio | `qa/audio.json` | lines present, clipping, loudness/true peak, stems, measured ducking, voice pitch consistency/distinctness, timeline errors, provider approximations |
| G Final | `qa/<fmt>/final.json` | container, streams, codec, pixel format, resolution, aspect, fps, exact frame count, duration, audio/video duration match, subtitles, full decode, per-shot frame match against an independent engine render (PSNR ≥ 30 dB), dialogue onset audible |

## Repair loop
1. Read the issue (`ref` gives shot@frame, line, asset or task).
2. Find the cause and owner: `kage.py impact <dir> <id>`; inspect frames with
   `kage.py render-frames <dir> --shot S --format F --frames A:B`.
3. Smallest safe fix **in the canonical file** (never in `build/`).
4. Re-run `kage.py run <dir>` — only dependent tasks re-run.
5. Confirm the report now passes; note the repair in your final report.

| Symptom | Usual fix |
|---|---|
| subject cut off in one aspect | add/adjust `camera.variants["9:16"]`, change framing or subjects |
| feet below ground / pops | adjust action timing/blends; custom `keyframes`; report engine bug if systematic |
| continuity facing/position | align the next shot's `cast.start` with the previous shot's end state |
| line too long for shot | allow `timing.flex`/`max_s`, move `offset_s`, or (with user approval) shorten text |
| loudness off target | check stem gains; mastering handles −16 LUFS with a limiter |
| music masks speech | raise `duck_under_dialogue_db` or lower cue `gain_db` |
| identity too similar | change a silhouette component, build or height (needs approval) |
| frame mismatch in final QA | assembly/timeline bug: compare `build/timeline.json` with shot frames |

Do not auto-approve creative changes (story meaning, dialogue text, canonical designs). Stop after
repeated failures of the same task (the scheduler bounds retries) and report the blocker with the
failing report path and the proposed fix.

## Recording subjective review
```
kage.py review <dir> --stage final:landscape --kind visual --verdict approved --by "<who>" --notes "…" --artifacts …
```
Use the real reviewer: a human name, or the model identity if an image-capable model inspected the
images. If nobody listened to the audio, record `--by nobody --kind listening --verdict changes_requested`.
