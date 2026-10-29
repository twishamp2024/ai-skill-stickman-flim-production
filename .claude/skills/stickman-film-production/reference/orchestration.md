# Multi-agent orchestration

## Logical roles (combine freely)
1 Director/story planner · 2 Visual-bible & continuity owner · 3 Character & asset designer ·
4 Environment designer · 5 Cinematography/shot planner · 6 Motion/timeline planner · 7 Audio
director · 8 Dialogue/voice worker · 9 Sound-design worker · 10 Rendering worker · 11 QA & repair ·
12 Assembly & delivery. For a short film one agent does all of them; for long films split by
**file ownership**, not by role count.

## Do not assume capabilities
Check what the host offers (subagents, background tasks, MCP servers, GPU). If delegation is not
available, run the same task list sequentially. Rendering/encoding parallelism comes from
`kage.py run --gpu N --cpu M`, not from agents.

## Delegation contract
Use [../templates/delegation_task.json](../templates/delegation_task.json). Required: `task_id`,
`role`, `inputs` (files + their `version`s + schema versions), `allowed_writes` (exact paths),
`forbidden` (always: bible, other characters, engine, library unless assigned), `outputs`,
`dependencies`, `validation` (command that must exit 0), `retry_policy`, `timeout`, and the
worker's structured `result` (status, files changed, validation output, open questions).

Integration: after each handoff the orchestrator runs `kage.py validate <dir>` (and
`--only identity` for character work). Rejected handoffs go back with the validator output.

## Safe parallelism
| Phase | Parallel units | Shared files (single owner) |
|---|---|---|
| Planning after the brief | one worker per scene's shot files | `production.json`, brief (director) |
| Design before lock | one worker per character / location | bible, library (continuity owner) |
| Audio | voices per line (automatic tasks), sound design | `audio/plan.json` (audio director) |
| Production | render tasks (scheduler) | none — workers do not edit canonical files |
| Repair | one issue per worker on the owning file | as above |

Never let two workers write one file. Workers never redefine characters, palette, style rules or
the renderer; they request changes from the owner, which bumps versions and re-approves.

## Scheduler (implemented in `kagelib/scheduler.py`)
DAG with cycle detection → ready tasks dispatched within per-resource limits (`gpu`, `cpu`; GPU
tasks queue instead of oversubscribing) → content-hash cache (skip when inputs unchanged and
outputs exist) → bounded retries for transient failures (crash, timeout, exit 75) with backoff →
deterministic failures fail fast → dependents marked `blocked` → state checkpointed after every
task (`build/state.json`) → `kage.py run` again resumes. `--only` selects tasks (dependencies
included); `--force` re-runs matches. Reports: `build/run_report.json`, `kage.py status`.

## Resource management
Measure, don't assume: `kage_render --stats` prints frames/s per backend (Apple M4, 1280×720:
≈160 fps GL, ≈110 fps software for the smoke scene). Each render task opens its own GL context;
default GPU concurrency is 2 (`render.json` `concurrency`). On headless/CI machines use
`--backend soft` and raise `--cpu`.
