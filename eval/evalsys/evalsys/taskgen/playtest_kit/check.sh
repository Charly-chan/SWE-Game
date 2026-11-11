#!/usr/bin/env bash
# check.sh — static conformance check of a playtest submission against its task package.
#
# Usage: check.sh [<package_root>] <submission_root>
#
# With one argument the package root is the directory this kit lives in
# (playtest/..), which is the workspace root when the kit is used as shipped.
#
# Runs seven checks (implemented in check_conformance.py next to this script):
#   1. gb_levels.json  — located in <submission_root>/game/ (then <submission_root>/),
#                        warns if both exist and differ, validated against the
#                        gb_interface schema via evalsys.interface.loader (falls back
#                        to jsonschema against visible/interface/gb_interface.schema.json).
#   2. ops.json        — every op name is in visible/interface/op_table.json,
#                        gb_pause / gb_reset are absent, frame values are in range.
#   3. extended_actions — each has a `why`, none use the reserved `gb_*` prefix,
#                        each is bound in project.godot [input] and referenced in a .gd.
#   4. endings         — names come from the canonical vocabulary
#                        (gb_interface.schema.json / contract.v2.json); at least one
#                        success ending is declared.
#   5. levels          — every declared level `res://` path exists in the submission.
#   6. groups          — `gb_player` plus every `gb_[a-z_]+` group literally named in
#                        visible/statement.md, visible/PROMPT.md or the Task GDD
#                        (only files under visible/) is present in some scene/script.
#   7. action count    — if the visible text says "at least N extended actions",
#                        the submission declares at least N.
#
# Prints one line per failure, then a final `CHECK_VERDICT pass=<bool> failures=<n>`.
# Exit status is 0 on pass, 1 on any failure, 2 on usage error.
set -euo pipefail

case "$#" in
  1) PACKAGE_ROOT="$(cd "$(dirname "$0")/.." && pwd)"; SUBMISSION_ROOT="$1" ;;
  2) PACKAGE_ROOT="$1"; SUBMISSION_ROOT="$2" ;;
  *) echo "usage: $(basename "$0") [<package_root>] <submission_root>" >&2; exit 2 ;;
esac

if [ ! -d "$PACKAGE_ROOT" ]; then
  echo "check.sh: package_root not a directory: $PACKAGE_ROOT" >&2
  exit 2
fi
if [ ! -d "$SUBMISSION_ROOT" ]; then
  echo "check.sh: submission_root not a directory: $SUBMISSION_ROOT" >&2
  exit 2
fi

exec python3 "$(dirname "$0")/check_conformance.py" "$PACKAGE_ROOT" "$SUBMISSION_ROOT"
