#!/usr/bin/env bash
# Invoke the Harbor adapter for exporting tasks and launching Harbor runs.
# Pass arguments through to evalsys.harbor; see docs/harbor.md for usage.
set -euo pipefail
repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
export PYTHONPATH="$repo_root/eval/evalsys${PYTHONPATH:+:$PYTHONPATH}"
exec "${HARBOR_PYTHON:-python3}" -m evalsys.harbor "$@"
