#!/usr/bin/env bash
set -euo pipefail

if [ $# -lt 1 ]; then
  echo "usage: $0 <file.blend> [output.svg] [-w STROKE_WIDTH]" >&2
  exit 1
fi

BLEND_FILE="$1"
shift
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BLENDER="${BLENDER:-/Applications/Blender.app/Contents/MacOS/Blender}"

"$BLENDER" "$BLEND_FILE" --background --python "$SCRIPT_DIR/blender_to_svg.py" -- "$@"
