# Shared environment helpers sourced by setup.sh, the runners, and evaluate.sh.
# Defines repository paths, pinned toolchain versions, provider routing, and
# preflight helpers. Override the documented environment variables to use a
# different installation layout; source this file from Bash.

# shellcheck shell=bash

if [ -z "${GB_ROOT:-}" ]; then
  GB_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
fi
export GB_ROOT
export GB_EVALSYS="$GB_ROOT/eval/evalsys"
export GB_BENCH="$GB_EVALSYS/bin/bench"

# Python: the venv that setup.sh creates, else whatever python3 is on PATH.
# An explicitly exported GB_PYTHON is remembered so gb_require_venv can honour it.
_gb_python_preset="${GB_PYTHON:-}"
export GB_VENV="${GB_VENV:-$GB_ROOT/.venv}"
if [ -x "$GB_VENV/bin/python3" ]; then
  GB_PYTHON="$GB_VENV/bin/python3"
else
  GB_PYTHON="${GB_PYTHON:-python3}"
fi
export GB_PYTHON

# Godot 4.5.1 (the corpus engine). Code paths default to /opt/godot451-bin/godot and
# honour GODOT_BIN; setup.sh installs into GODOT_PREFIX and keeps the symlink alive.
export GODOT_PREFIX="${GODOT_PREFIX:-/opt/godot-4.5.1}"
export GODOT_BIN="${GODOT_BIN:-/opt/godot451-bin/godot}"

# Proxy-free CLI shims (claude, codex) and other helper binaries land in GB_TOOLS_BIN;
# the pinned npm packages they exec live in GB_TOOLS_ROOT/node_modules (a private npm
# prefix, never `npm -g`). Both must be outside /data2 and /tmp: the coding-agent
# sandbox overmounts both.
export GB_TOOLS_ROOT="${GB_TOOLS_ROOT:-/opt/gamebench}"
export GB_TOOLS_BIN="${GB_TOOLS_BIN:-$GB_TOOLS_ROOT/bin}"
case ":$PATH:" in
  *":$GB_TOOLS_BIN:"*) ;;
  *) [ -d "$GB_TOOLS_BIN" ] && export PATH="$GB_TOOLS_BIN:$PATH" ;;
esac

# Toolchain versions installed by setup.sh and expected by the agent adapters.
# The Godot archive is verified against the SHA-256 below.
export GB_CLAUDE_CODE_VERSION="${GB_CLAUDE_CODE_VERSION:-2.1.222}"
export GB_CODEX_VERSION="${GB_CODEX_VERSION:-0.153.4}"
export GB_GODOT_VERSION="4.5.1"
export GB_GODOT_SHA256="db07cae7de644278a1884d4552bdf2bca3f5d30131b18faf3a0c4d730080b199"
export GB_GODOT_URL="${GB_GODOT_URL:-https://github.com/godotengine/godot/releases/download/4.5.1-stable/Godot_v4.5.1-stable_linux.x86_64.zip}"
# Image built by docker/build.sh. A remote Docker endpoint needs this image in
# its own store; set GB_SANDBOX_IMAGE or --docker-image to a reachable registry
# tag when the image was built elsewhere. Docker runs remain non-formal.
export GB_SANDBOX_IMAGE="${GB_SANDBOX_IMAGE:-gamebench-agent:godot-4.5.1}"

# Mode 5 (port) submits a Unity project, so its cells need the Unity toolchain
# instead of the Godot image above. Built by docker/build.sh; override when the
# tag lives in a registry this host can reach.
export GB_UNITY_SANDBOX_IMAGE="${GB_UNITY_SANDBOX_IMAGE:-gamebench-agent:unity-6000.3.23f1}"

# Which image a docker cell runs in. Both runners call this so the preflight
# probes the very tag the cells use, and so the mode-to-engine rule lives once.
gb_sandbox_image_for_mode() { # mode [explicit_tag]
  if [ -n "${2:-}" ]; then printf '%s' "$2"; return 0; fi
  if [ "$1" = port ]; then printf '%s' "$GB_UNITY_SANDBOX_IMAGE"; else printf '%s' "$GB_SANDBOX_IMAGE"; fi
}

# Gateway keys live OUTSIDE the repo. Search order: $GB_API_ENV, the per-user path,
# then the reference host's path. `.gb_api.env.example` at the repo root is the template.
gb_api_env_path() {
  if [ -n "${GB_API_ENV:-}" ]; then
    printf '%s\n' "$GB_API_ENV"
  elif [ -f "${XDG_CONFIG_HOME:-$HOME/.config}/gamebench/gb_api.env" ]; then
    printf '%s\n' "${XDG_CONFIG_HOME:-$HOME/.config}/gamebench/gb_api.env"
  elif [ -f /tmp/swe-game/.gb_api.env ]; then
    printf '%s\n' /tmp/swe-game/.gb_api.env
  else
    printf '%s\n' "${XDG_CONFIG_HOME:-$HOME/.config}/gamebench/gb_api.env"
  fi
}

# Export every KEY=VALUE from the api env file (it may also `unset` proxy variables).
gb_load_api_env() {
  local path
  path="$(gb_api_env_path)"
  if [ ! -f "$path" ]; then
    return 1
  fi
  set -a
  # shellcheck disable=SC1090
  . "$path"
  set +a
  export GB_API_ENV="$path"
  return 0
}

# Which endpoint and which key VARIABLE a harness gets, decided from the api env
# file (call gb_load_api_env first). `provider` is run_benchmark.sh's --provider:
#   auto   – the env file decides: OPENAI_BASE_URL / ANTHROPIC_BASE_URL set → that
#            gateway; empty → the official endpoint (Codex: native provider, key
#            OPENAI_API_KEY or the ChatGPT login). Legacy names are aliases:
#            GB_CODEX_BASE_URL / GB_CODEX_KEY_ENV / GB_CLAUDE_BASE_URL /
#            GB_CLAUDE_KEY_ENV, and MICU_API_KEY (with OPENAI_API_KEY empty it
#            selects the MICU gateway, as the pre-auto default did). Claude Code:
#            ANTHROPIC_API_KEY, or ANTHROPIC_AUTH_TOKEN (Bearer) when only that is set.
#   micu   – the historical defaults: MICU gateway + MICU_API_KEY / ANTHROPIC_API_KEY
#            unless GB_*_BASE_URL / GB_*_KEY_ENV override them.
#   openai – codex only: Codex's native openai provider (OPENAI_API_KEY or auth.json).
# Sets GB_ROUTE_KIND (custom = explicit provider block with a base URL; openai =
# Codex native), GB_ROUTE_BASE_URL and GB_ROUTE_KEY_ENV. Only names, never values.
# A MICU-style key left unused by the resolved route gets a one-line stderr hint.
gb_resolve_agent_route() { # harness provider
  local harness="$1" provider="${2:-auto}"
  GB_ROUTE_KIND=custom GB_ROUTE_BASE_URL="" GB_ROUTE_KEY_ENV=""
  if [ "$harness" = codex ]; then
    case "$provider" in
      openai)
        GB_ROUTE_KIND=openai GB_ROUTE_KEY_ENV=OPENAI_API_KEY ;;
      micu)
        GB_ROUTE_BASE_URL="${GB_CODEX_BASE_URL:-https://www.micuapi.ai/v1}"
        GB_ROUTE_KEY_ENV="${GB_CODEX_KEY_ENV:-MICU_API_KEY}" ;;
      *)
        GB_ROUTE_BASE_URL="${OPENAI_BASE_URL:-${GB_CODEX_BASE_URL:-}}"
        GB_ROUTE_KEY_ENV="${GB_CODEX_KEY_ENV:-}"
        if [ -z "$GB_ROUTE_KEY_ENV" ]; then
          if [ -z "${OPENAI_API_KEY:-}" ] && [ -n "${MICU_API_KEY:-}" ]; then
            GB_ROUTE_KEY_ENV=MICU_API_KEY
          else
            GB_ROUTE_KEY_ENV=OPENAI_API_KEY
          fi
        fi
        if [ -z "$GB_ROUTE_BASE_URL" ]; then
          if [ "$GB_ROUTE_KEY_ENV" = MICU_API_KEY ]; then
            GB_ROUTE_BASE_URL=https://www.micuapi.ai/v1
          else
            GB_ROUTE_KIND=openai GB_ROUTE_KEY_ENV=OPENAI_API_KEY
          fi
        fi ;;
    esac
  else
    if [ "$provider" = anthropic ]; then
      GB_ROUTE_KIND=claude
      GB_ROUTE_BASE_URL=""
      GB_ROUTE_KEY_ENV=""
      export GB_ROUTE_KIND GB_ROUTE_BASE_URL GB_ROUTE_KEY_ENV
      return 0
    fi
    GB_ROUTE_KEY_ENV="${GB_CLAUDE_KEY_ENV:-}"
    if [ -z "$GB_ROUTE_KEY_ENV" ]; then
      # ANTHROPIC_AUTH_TOKEN (Bearer; Moonshot's Anthropic-compatible endpoint documents
      # it) is Claude Code's other native credential name; it is the route's key
      # variable only while ANTHROPIC_API_KEY is empty.
      if [ -z "${ANTHROPIC_API_KEY:-}" ] && [ -n "${ANTHROPIC_AUTH_TOKEN:-}" ]; then
        GB_ROUTE_KEY_ENV=ANTHROPIC_AUTH_TOKEN
      else
        GB_ROUTE_KEY_ENV=ANTHROPIC_API_KEY
      fi
    fi
    if [ "$provider" = micu ]; then
      GB_ROUTE_BASE_URL="${GB_CLAUDE_BASE_URL:-https://www.micuapi.ai}"
    else
      GB_ROUTE_BASE_URL="${ANTHROPIC_BASE_URL:-${GB_CLAUDE_BASE_URL:-https://api.anthropic.com}}"
    fi
  fi
  export GB_ROUTE_KIND GB_ROUTE_BASE_URL GB_ROUTE_KEY_ENV
  _gb_route_hints "$harness" "$provider"
}

# Hints for the one configuration that keeps surprising runners: a MICU-style key in
# the env file (MICU_API_KEY, or the legacy GB_*_KEY_ENV aliases) while the harness's
# base URL is empty, so the route resolves to the official endpoint and the MICU key
# is never used. Printed to stderr once per harness per process; names only.
_gb_route_hints() { # harness provider
  local harness="$1" provider="$2" micu_style=""
  if [ -n "${MICU_API_KEY:-}" ] || [ -n "${GB_CODEX_KEY_ENV:-}" ] || [ -n "${GB_CLAUDE_KEY_ENV:-}" ]; then
    micu_style=1
  fi
  [ -n "$micu_style" ] || return 0
  [ "$provider" != micu ] || return 0
  case " ${_GB_ROUTE_HINTED:-} " in *" $harness "*) return 0 ;; esac
  if [ "$harness" = codex ] && [ "$GB_ROUTE_KIND" = openai ]; then
    _GB_ROUTE_HINTED="${_GB_ROUTE_HINTED:-} $harness"
    gb_note "hint: Codex route resolves to Codex's native provider (OPENAI_API_KEY, or the ChatGPT login when it is empty); the MICU-style key is not used. To use the MICU gateway set OPENAI_BASE_URL=https://www.micuapi.ai/v1 in .gb_api.env (with OPENAI_API_KEY empty), or pass --provider micu."
  elif [ "$harness" != codex ] && [ "$GB_ROUTE_BASE_URL" = https://api.anthropic.com ]; then
    _GB_ROUTE_HINTED="${_GB_ROUTE_HINTED:-} $harness"
    gb_note "hint: Claude route resolves to the official endpoint (api.anthropic.com). To use the MICU gateway set ANTHROPIC_BASE_URL=https://www.micuapi.ai in .gb_api.env, or pass --provider micu."
  fi
}

# Scratch: the evaluator copies projects and replays routes here (several GB per run).
# Default follows the reference host when /tmp/swe-game is writable, else
# a repo-local directory that .gitignore already excludes via `.scratch/`.
if [ -z "${GB_SCRATCH_ROOT:-}" ]; then
  if [ -d /tmp/swe-game ] && [ -w /tmp/swe-game ]; then
    GB_SCRATCH_ROOT=/tmp/swe-game/gb_taskgen_scratch
  else
    GB_SCRATCH_ROOT="$GB_ROOT/.scratch/gb_taskgen_scratch"
  fi
fi
export GB_SCRATCH_ROOT
export GB_ROUTES_SCRATCH="${GB_ROUTES_SCRATCH:-$GB_SCRATCH_ROOT/gb_routes_scratch}"
# Where scripts/run_coding.sh / evaluate.sh put their outputs unless --out is given.
export GB_RUNS_ROOT="${GB_RUNS_ROOT:-$(dirname "$GB_SCRATCH_ROOT")}"

# CA certificates: this host's Python was built against an OpenSSL whose default
# store is empty, so urllib HTTPS (VLM judge, downloads through python) fails unless
# SSL_CERT_FILE points at a bundle. Prefer the system bundle, then certifi's.
gb_export_ca_bundle() {
  if [ -n "${SSL_CERT_FILE:-}" ] && [ -f "$SSL_CERT_FILE" ]; then
    return 0
  fi
  local empty
  empty="$("$GB_PYTHON" - <<'PY' 2>/dev/null || echo yes
import ssl
print("yes" if ssl.create_default_context().cert_store_stats().get("x509_ca", 0) == 0 else "no")
PY
)"
  if [ "$empty" = yes ]; then
    if [ -f /etc/ssl/certs/ca-certificates.crt ]; then
      export SSL_CERT_FILE=/etc/ssl/certs/ca-certificates.crt
    else
      local certifi
      certifi="$("$GB_PYTHON" -c 'import certifi; print(certifi.where())' 2>/dev/null || true)"
      [ -n "$certifi" ] && [ -f "$certifi" ] && export SSL_CERT_FILE="$certifi"
    fi
  fi
}

gb_python_env() {
  export PYTHONPATH="$GB_EVALSYS${PYTHONPATH:+:$PYTHONPATH}"
  export PYTHONIOENCODING=utf-8 PYTHONUTF8=1
  gb_export_ca_bundle
}

gb_die() { printf 'error: %s\n' "$*" >&2; exit 2; }
gb_note() { printf '%s\n' "$*" >&2; }

# Entry scripts (run_benchmark.sh, scripts/run_coding.sh, evaluate.sh) run only on the venv
# setup.sh created, so results never depend on whatever the host's python3 carries.
# An interpreter named explicitly through GB_PYTHON is the one deliberate exception.
gb_require_venv() {
  if [ -x "$GB_VENV/bin/python3" ]; then
    GB_PYTHON="$GB_VENV/bin/python3"
  elif [ -n "$_gb_python_preset" ]; then
    GB_PYTHON="$_gb_python_preset"
  else
    gb_die "python venv missing at $GB_VENV — run ./setup.sh first (or set GB_VENV to an existing venv, or GB_PYTHON to an interpreter)"
  fi
  export GB_PYTHON
}

# True when the file is still a Git LFS pointer (the ~130-byte stub a clone gets
# before `git lfs pull`), false for real content or a missing file.
gb_is_lfs_pointer() {
  [ -f "$1" ] && [ "$(stat -c %s "$1" 2>/dev/null || echo 0)" -lt 1024 ] \
    && head -c 40 "$1" 2>/dev/null | grep -q '^version https://git-lfs'
}

# Fetch reference projects and recordings from the pinned Hugging Face dataset.
gb_ensure_reference_project() { # game_id
  "$GB_PYTHON" "$GB_ROOT/scripts/fetch_reference_data.py" --game "$1" \
    || gb_die "reference project download failed: $1"
}

gb_ensure_reference_film() { # game_id
  "$GB_PYTHON" "$GB_ROOT/scripts/fetch_reference_data.py" --game "$1" --with-videos \
    || gb_die "reference data download failed: $1"
}
