#!/usr/bin/env bash
# Build the Kage engine and (optionally) make the skill available to Claude Code globally.
#   ./install.sh            build + test the engine; the skill is already discoverable in this repo
#   ./install.sh --user     also symlink the skill into ~/.claude/skills/
#   ./install.sh --no-gl    build the software rasterizer only
set -euo pipefail
here="$(cd "$(dirname "$0")" && pwd)"
skill="$here/.claude/skills/stickman-film-production"
args=()
link_user=0
for a in "$@"; do
  case "$a" in
    --user) link_user=1 ;;
    --no-gl) args+=(--no-gl) ;;
    *) echo "unknown option $a" >&2; exit 1 ;;
  esac
done
command -v python3 >/dev/null || { echo "python3 is required" >&2; exit 1; }
command -v cmake >/dev/null || { echo "cmake is required" >&2; exit 1; }
command -v ffmpeg >/dev/null || echo "warning: ffmpeg not found (needed for encoding, mixing and QA)" >&2
python3 "$skill/scripts/kage.py" engine-build ${args[@]+"${args[@]}"}
if [ "$link_user" = 1 ]; then
  mkdir -p "$HOME/.claude/skills"
  target="$HOME/.claude/skills/stickman-film-production"
  if [ -e "$target" ] && [ ! -L "$target" ]; then
    echo "refusing to replace existing non-symlink $target" >&2; exit 1
  fi
  ln -sfn "$skill" "$target"
  echo "linked $target -> $skill"
fi
python3 "$skill/scripts/kage.py" doctor
