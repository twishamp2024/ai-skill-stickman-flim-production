# Project Kage — stickman film production skill

An Agent Skill plus a C++/OpenGL engine that turn a complete story into a coherent, animated
stickman film: story analysis → shot plan → locked character identities → animatic → rendered
shots with voices, sound and music → QA → multi-format delivery.

| Part | Location | What it is |
|---|---|---|
| Agent Skill | [`.claude/skills/stickman-film-production/`](.claude/skills/stickman-film-production/SKILL.md) | workflow, rules, 20 JSON Schemas, component library, `kage.py` tools, templates, reference docs |
| Engine | [`engine/`](engine/) | `kage_render`: C++17, deterministic rig/motion/tessellation, OpenGL 3.3 + software backends |
| Example film | [`examples/train-journey/`](examples/train-journey/) | "The Evening Train": mother and child on a train, 16:9 + 9:16 |
| Tests | [`tests/`](tests/), [`engine/tests/`](engine/tests/) | Python unittest suite, engine unit tests |
| Status | [`STATUS.md`](STATUS.md) | what is implemented, tested, and not, with evidence |

## Requirements
Python ≥ 3.9 (stdlib only) · CMake ≥ 3.16 + a C++17 compiler · FFmpeg ≥ 7 with libx264, AAC,
FFV1 · optional GLFW ≥ 3.3 for the OpenGL backend · optional macOS `say` for the bundled voice
adapter. Verified on macOS 26.6 (Apple M4, Apple clang 21, CMake 4.4, FFmpeg 9.0.2, GLFW 3.5.1).

## Install
```bash
./install.sh
```
This builds and tests the engine. The skill is discoverable automatically when Claude Code runs in this
repository (project skill). To use it in every project:
```bash
./install.sh --user
```
That command symlinks the skill into `~/.claude/skills/`. Other agents: point them at `SKILL.md`; all tooling is
`python3 .claude/skills/stickman-film-production/scripts/kage.py <command>`.

## Invoke
Ask the agent, e.g. *"Make a 40-second stickman film of this story, 9:16 for Reels: …"*. The skill
is selected by its description. You can also say "use the stickman-film-production skill".

## Reproduce the example
```bash
K=.claude/skills/stickman-film-production/scripts/kage.py
python3 $K doctor examples/train-journey
python3 $K validate examples/train-journey
python3 $K animatic examples/train-journey
python3 $K run examples/train-journey
```
The run writes `examples/train-journey/output/the_evening_train_{landscape,vertical}.mp4`,
`output/delivery.json`, `qa/**`. Once built, the full run takes about 25 s on an M4; a re-run with no changes takes 0.03 s.

## Tests
```bash
cmake --build engine/build && ctest --test-dir engine/build --output-on-failure
cd tests && python3 -m unittest -v
```
