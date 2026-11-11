#!/usr/bin/env bash
# Install the Linux dependencies used by task generation, coding-agent runs,
# and submission evaluation. Re-running checks existing installations and repairs
# missing dependencies. Paths and pinned versions come from eval/tools/gb_env.sh.
#
#   sudo ./setup.sh                 install / repair everything
#   ./setup.sh --check              print the dependency table, change nothing (no root needed)
#   ./setup.sh --check-auth         one non-billed probe per key in the api env file (no root)
#   sudo ./setup.sh --unity         also print the (manual) Unity Mode-5 editor + licence steps
#
# Options
#   --check              report only
#   --check-auth         probe each configured route (GET /models; POST /responses for a
#                        Codex gateway; GET /v1/models for Claude); prints ok / 401
#                        unauthorized / no Responses API / unreachable per harness and
#                        `codex login status` when OPENAI_API_KEY is empty. Exit 1 on any failure.
#   --unity              describe the optional Unity editor setup (not automated)
#   --skip-apt           do not touch apt (already provisioned / no root)
#   --skip-godot         do not download Godot (GODOT_BIN must already be 4.5.1)
#   --skip-cli           do not (re)install Claude Code / Codex via npm
#   --skip-lfs           do not configure or pull Git LFS
#   --lfs-include GLOB   pull matching LFS objects now (repeatable); without this
#                        setup configures LFS for remaining repository artifacts.
#                        Reference games and videos are downloaded from Hugging Face.
#   --godot-prefix DIR   where the Godot zip is unpacked (default /opt/godot-4.5.1)
#   --venv DIR           python venv location (default <repo>/.venv)
#
# Exit status: 0 when every required item is present after the run, 1 when
# something required is still missing (the table names it), 2 on usage errors.
#
# Environment (all optional; see eval/tools/gb_env.sh for the full list)
#   GODOT_BIN, GODOT_PREFIX, GB_VENV, GB_TOOLS_BIN, GB_TOOLS_ROOT, GB_API_ENV,
#   GB_SCRATCH_ROOT, GB_CLAUDE_CODE_VERSION, GB_CODEX_VERSION, GB_GODOT_URL
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
export GB_ROOT="$HERE"
# shellcheck source=eval/tools/gb_env.sh
. "$HERE/eval/tools/gb_env.sh"

CHECK=0 CHECK_AUTH=0 UNITY=0 SKIP_APT=0 SKIP_GODOT=0 SKIP_CLI=0 SKIP_LFS=0
LFS_INCLUDE=()
while [ $# -gt 0 ]; do
  case "$1" in
    --check) CHECK=1 ;;
    --check-auth) CHECK_AUTH=1 ;;
    --unity) UNITY=1 ;;
    --skip-apt) SKIP_APT=1 ;;
    --skip-godot) SKIP_GODOT=1 ;;
    --skip-cli) SKIP_CLI=1 ;;
    --skip-lfs) SKIP_LFS=1 ;;
    --lfs-include) LFS_INCLUDE+=("$2"); shift ;;
    --godot-prefix) export GODOT_PREFIX="$2"; shift ;;
    --venv) export GB_VENV="$2"; shift ;;
    -h|--help) sed -n '2,33p' "$0"; exit 0 ;;
    *) gb_die "unknown option $1 (try --help)" ;;
  esac
  shift
done

SUDO=""
if [ "$(id -u)" -ne 0 ]; then
  if command -v sudo >/dev/null 2>&1; then SUDO="sudo"; else SUDO="__no_root__"; fi
fi
as_root() {
  if [ -z "$SUDO" ]; then "$@"
  elif [ "$SUDO" = "__no_root__" ]; then
    gb_note "  (skipped, needs root: $*)"; return 1
  else sudo "$@"; fi
}

step() { printf '\n== %s\n' "$*"; }
ok() { printf '   ok   %s\n' "$*"; }
skip() { printf '   skip %s\n' "$*"; }

# ------------------------------------------------------------------------------
# Dependency table (--check). Each probe prints: name | status | version | path/note
# ------------------------------------------------------------------------------
ROWS=()
MISSING=0
MISSING_NAMES=()
TODO=0
# status values: found | missing (required, counted) | todo (manual step, not
# counted) | lazy (fetched on demand by run_benchmark.sh) | optional
row() { # name status version detail
  ROWS+=("$(printf '%s\t%s\t%s\t%s' "$1" "$2" "$3" "$4")")
  if [ "$2" = "missing" ]; then MISSING=$((MISSING + 1)); MISSING_NAMES+=("$1"); fi
  [ "$2" = "todo" ] && TODO=$((TODO + 1)) || true
}
first_line() { head -n 1 | tr -d '\r'; }
probe_cmd() { # name command version-args...
  local name="$1" cmd="$2"; shift 2
  local path
  path="$(command -v "$cmd" 2>/dev/null || true)"
  if [ -z "$path" ]; then row "$name" missing "-" "not on PATH"; return; fi
  local ver
  ver="$("$path" "$@" 2>&1 </dev/null | first_line || true)"
  row "$name" found "${ver:-?}" "$path"
}

# Unity editor, resolved the way unity_runtime.unity_available does it (no venv needed).
unity_editor_path() {
  local c
  if [ -n "${UNITY_BIN:-}" ]; then
    if [ -f "$UNITY_BIN" ] && [ -x "$UNITY_BIN" ]; then readlink -f "$UNITY_BIN"; else command -v "$UNITY_BIN" 2>/dev/null || true; fi
    return 0
  fi
  for c in unity-editor Unity; do
    if command -v "$c" >/dev/null 2>&1; then readlink -f "$(command -v "$c")"; return 0; fi
  done
  for c in /opt/Unity/Editor/Unity /opt/unity/Editor/Unity; do
    if [ -f "$c" ] && [ -x "$c" ]; then readlink -f "$c"; return 0; fi
  done
}
# Licence verdict from a headless editor start (the evaluator build uses the same
# flags). Unlicensed editors print "No valid Unity Editor license" and exit in a
# few seconds; licensed ones create the throwaway project and exit 0.
unity_licence_status() { # editor -> "ok" | "MISSING (...)" | "unclear (...)"
  local tmp log rc=0
  tmp="$(mktemp -d)"; log="$tmp/editor.log"
  timeout 90 "$1" -batchmode -nographics -quit -createProject "$tmp/project" -logFile - >"$log" 2>&1 || rc=$?
  if grep -qiE 'No valid Unity Editor license|Failed to activate/update license|license is invalid' "$log"; then
    printf 'MISSING (No valid Unity Editor license; activate Personal via Unity Hub login, see setup.sh --unity)'
  elif [ "$rc" -eq 0 ]; then printf 'ok'
  elif [ "$rc" -eq 124 ]; then printf 'unclear (probe timed out after 90 s)'
  else printf 'unclear (editor exit %s)' "$rc"; fi
  rm -rf "$tmp"
}

check_all() {
  row "os" found "$(. /etc/os-release 2>/dev/null && printf '%s' "${PRETTY_NAME:-?}")" "$(uname -m)"
  # python
  if command -v "$GB_PYTHON" >/dev/null 2>&1 || [ -x "$GB_PYTHON" ]; then
    local pyver; pyver="$("$GB_PYTHON" --version 2>&1 | first_line)"
    row "python3 (>=3.10)" found "$pyver" "$GB_PYTHON"
  else
    row "python3 (>=3.10)" missing "-" "GB_PYTHON=$GB_PYTHON"
  fi
  # The venv is required: every entry script resolves GB_PYTHON to it, and the
  # py:* rows below are probed with the venv interpreter, not the system one.
  local venv_py="$GB_VENV/bin/python3"
  if [ -x "$venv_py" ]; then row "venv" found "$("$venv_py" --version 2>&1 | first_line)" "$GB_VENV"
  else row "venv" missing "-" "$GB_VENV (setup.sh creates it and installs eval/evalsys/requirements.txt)"; fi
  local mod
  for mod in numpy PIL jsonschema certifi pytest; do
    local v=""
    if [ -x "$venv_py" ]; then
      v="$("$venv_py" - "$mod" <<'PY' 2>/dev/null || true
import importlib, sys
m = importlib.import_module(sys.argv[1])
print(getattr(m, "__version__", "?"))
PY
)"
    fi
    if [ -n "$v" ]; then row "py:$mod" found "$v" "$("$venv_py" -c "import $mod,os;print(os.path.dirname($mod.__file__))" 2>/dev/null)"; else row "py:$mod" missing "-" "$venv_py -m pip install -r eval/evalsys/requirements.txt"; fi
  done
  # CA store
  local castore
  castore="$("$GB_PYTHON" -c 'import ssl;print(ssl.create_default_context().cert_store_stats().get("x509_ca",0))' 2>/dev/null || echo 0)"
  if [ "${castore:-0}" -gt 0 ]; then row "python CA store" found "$castore CAs" "${SSL_CERT_FILE:-openssl default}"
  else
    gb_export_ca_bundle
    if [ -n "${SSL_CERT_FILE:-}" ]; then row "python CA store" found "via SSL_CERT_FILE" "$SSL_CERT_FILE (default store is empty; gb_env.sh exports this)"
    else row "python CA store" missing "-" "openssl default store empty and no bundle found (apt install ca-certificates)"; fi
  fi
  # godot
  if [ -x "$GODOT_BIN" ]; then
    local gv godot_sha; gv="$("$GODOT_BIN" --version 2>/dev/null | first_line || true)"
    case "$gv" in
      4.5.1*) row "godot 4.5.1" found "$gv" "$GODOT_BIN$( [ -L "$GODOT_BIN" ] && printf ' -> %s' "$(readlink -f "$GODOT_BIN")")" ;;
      *) row "godot 4.5.1" missing "$gv" "$GODOT_BIN is not 4.5.1 (set GODOT_BIN or run setup.sh)" ;;
    esac
    godot_sha="$(sha256sum "$GODOT_BIN" 2>/dev/null | awk '{print $1}')"
    if [ "$godot_sha" = "$GB_GODOT_SHA256" ]; then
      row "godot binary sha256" found "${godot_sha:0:16}…" "matches pinned official 4.5.1 binary"
    else
      row "godot binary sha256" missing "${godot_sha:--}" "expected $GB_GODOT_SHA256"
    fi
  else
    row "godot 4.5.1" missing "-" "GODOT_BIN=$GODOT_BIN not executable"
    row "godot binary sha256" missing "-" "binary unavailable"
  fi
  local xvfb_version
  xvfb_version="$(dpkg-query -W -f='${Version}' xvfb 2>/dev/null || true)"
  if command -v Xvfb >/dev/null 2>&1; then row "Xvfb" found "${xvfb_version:-?}" "$(command -v Xvfb)"; else row "Xvfb" missing "-" "not on PATH"; fi
  if command -v xvfb-run >/dev/null 2>&1; then row "xvfb-run" found "${xvfb_version:-?}" "$(command -v xvfb-run)"; else row "xvfb-run" missing "-" "not on PATH"; fi
  probe_cmd "ffmpeg" ffmpeg -version
  probe_cmd "ffprobe" ffprobe -version
  probe_cmd "jq" jq --version
  probe_cmd "git" git --version
  probe_cmd "git-lfs" git-lfs version
  # `git lfs install --local` writes the smudge/clean filters into .git/config;
  # without them `git lfs pull` cannot replace pointers, so this is required.
  if command -v git-lfs >/dev/null 2>&1; then
    if [ -n "$(git -C "$GB_ROOT" config --get filter.lfs.smudge 2>/dev/null)" ]; then
      row "git-lfs filters" found "-" "$(git -C "$GB_ROOT" config --show-origin --get filter.lfs.smudge 2>/dev/null | awk '{print $1}' | sed 's/^file://')"
    else
      row "git-lfs filters" missing "-" "git -C $GB_ROOT lfs install --local (setup.sh does this)"
    fi
  fi
  if git -C "$GB_ROOT" lfs ls-files >/dev/null 2>&1; then
    local total pulled
    total="$(git -C "$GB_ROOT" lfs ls-files 2>/dev/null | wc -l | tr -d ' ')"
    pulled="$(git -C "$GB_ROOT" lfs ls-files 2>/dev/null | grep -c ' \* ' || true)"
    row "repository LFS objects" lazy "$pulled/$total pulled" "reference games/videos are downloaded from Hugging Face per game"
  fi
  probe_cmd "unshare" unshare --version
  if unshare --user --map-root-user --mount --pid --fork --mount-proc true >/dev/null 2>&1; then
    row "user namespaces" found "-" "unshare --user --map-root-user --mount --pid works (needed by --agent-sandbox unshare)"
  else
    row "user namespaces" missing "-" "unshare --user failed (a capability bounding set without CAP_SYS_ADMIN can never regain it); use scripts/run_coding.sh --sandbox docker (not formal) or enable kernel.unprivileged_userns_clone"
  fi
  # Fallback path for hosts where the row above is missing: the agent then runs
  # in a container instead, so only the daemon and the image matter.
  if ! command -v docker >/dev/null 2>&1; then
    row "docker sandbox (optional)" optional "-" "no docker client; only --sandbox docker needs it"
  elif ! timeout 60 docker version >/dev/null 2>&1; then
    row "docker sandbox (optional)" optional "-" "client present but daemon unreachable (DOCKER_HOST=${DOCKER_HOST:-<unset>})"
  elif timeout 120 docker image inspect "$GB_SANDBOX_IMAGE" >/dev/null 2>&1; then
    row "docker sandbox (optional)" found "-" "$GB_SANDBOX_IMAGE cached; --sandbox docker usable (never formal: unpinned toolchain)"
  else
    row "docker sandbox (optional)" optional "-" "daemon ok; $GB_SANDBOX_IMAGE not cached (build with ./docker/build.sh or select --docker-image)"
  fi
  probe_cmd "timeout (GNU)" timeout --version
  probe_cmd "node (>=20)" node --version
  local npm_path npm_ver
  npm_path="$(command -v npm 2>/dev/null || true)"
  npm_ver="$([ -n "$npm_path" ] && npm --version 2>&1 | tail -n 1 || true)"
  if [ -n "$npm_path" ]; then row "npm" found "${npm_ver:-?}" "$npm_path"; else row "npm" missing "-" "not on PATH"; fi
  # CLIs: both the pin and WHICH file PATH resolves to matter (site wrappers broke live0904).
  local cli cli_want cli_path cli_ver
  for cli in claude codex; do
    if [ "$cli" = claude ]; then cli_want="$GB_CLAUDE_CODE_VERSION"; else cli_want="$GB_CODEX_VERSION"; fi
    cli_path="$(command -v "$cli" 2>/dev/null || true)"
    cli_ver="$([ -n "$cli_path" ] && "$cli_path" --version 2>&1 </dev/null | grep -F -m1 "$cli_want" || true)"
    if [ "$cli" = claude ] && [ "$cli_path" = /usr/local/bin/claude ]; then
      row "$cli CLI (pinned $cli_want)" missing "${cli_ver:--}" "$cli_path is the reference host's broken forced-proxy wrapper; run setup.sh for the neutral shim"
    elif [ -n "$cli_path" ] && [ -n "$cli_ver" ]; then
      row "$cli CLI (pinned $cli_want)" found "$cli_ver" "$cli_path"
    elif [ -n "$cli_path" ]; then
      local cli_err
      cli_err="$("$cli_path" --version 2>&1 </dev/null | grep -v -e UNDICI -e trace-warnings | first_line || true)"
      row "$cli CLI (pinned $cli_want)" missing "-" "$cli_path does not report $cli_want (${cli_err:-no output}); setup.sh reinstalls it under $GB_TOOLS_ROOT"
    else
      row "$cli CLI (pinned $cli_want)" missing "-" "not on PATH; setup.sh installs it under $GB_TOOLS_ROOT with a shim in $GB_TOOLS_BIN"
    fi
  done
  if [ -d /usr/share/fonts/truetype/dejavu ]; then row "fonts (DejaVu)" found "-" "/usr/share/fonts/truetype/dejavu"; else row "fonts (DejaVu)" missing "-" "apt install fonts-dejavu-core fonts-liberation"; fi
  [ -d /data2 ] && row "/data2 (sandbox overmounts it)" found "-" "/data2" || row "/data2 (sandbox overmounts it)" missing "-" "mkdir -p /data2 (setup.sh does this)"
  [ -d /workspace ] && row "/workspace (sandbox bind target)" found "-" "/workspace" || row "/workspace (sandbox bind target)" missing "-" "created on first run (needs root or writable /)"
  if mkdir -p "$GB_SCRATCH_ROOT" 2>/dev/null && [ -w "$GB_SCRATCH_ROOT" ]; then
    row "scratch root" found "$(df -h "$GB_SCRATCH_ROOT" 2>/dev/null | awk 'NR==2{print $4" free"}')" "$GB_SCRATCH_ROOT"
  else
    row "scratch root" missing "-" "$GB_SCRATCH_ROOT not writable (set GB_SCRATCH_ROOT)"
  fi
  local api; api="$(gb_api_env_path)"
  if [ -f "$api" ]; then
    local keys
    keys="$(grep -oE '^(export +)?[A-Z_]+_API_KEY=' "$api" | sed -E 's/^(export +)?//; s/=$//' | tr '\n' ' ')"
    # Secrets cannot be installed by a script: an unfilled template is a manual
    # step (todo), not a missing dependency. Dry-runs need no key; Codex can also
    # run on `codex login` with every key line empty.
    local filled
    filled="$(grep -E '^(export +)?[A-Z_]+_API_KEY=.+' "$api" | grep -vc REPLACE_ME || true)"
    if [ "${filled:-0}" -eq 0 ]; then row "api env file (keys)" todo "no key filled" "$api: every *_API_KEY line is empty or REPLACE_ME; fill it, then ./setup.sh --check-auth"
    else row "api env file (keys)" found "${keys:-no *_API_KEY lines}" "$api (values not shown; ./setup.sh --check-auth probes them)"; fi
  else
    row "api env file (keys)" todo "-" "$api absent (setup.sh writes the template there; cp .gb_api.env.example $api)"
  fi
  # optional: Unity editor + licence (only --mode port uses them; run_benchmark.sh
  # repeats this probe before a live port run). Same resolution order as the
  # evaluator: UNITY_BIN, PATH, /opt/Unity/Editor/Unity, /opt/unity/Editor/Unity.
  local unity_bin="" unity_ver="" unity_lic
  unity_bin="$(unity_editor_path)"
  if [ -n "$unity_bin" ]; then
    unity_ver="$(timeout 30 "$unity_bin" -version 2>/dev/null | first_line || true)"
    unity_lic="$(unity_licence_status "$unity_bin")"
    if [ "$unity_ver" != 6000.3.23f1 ]; then
      row "unity (optional, Mode 5)" optional "${unity_ver:-?}" "$unity_bin is not 6000.3.23f1 (port packages target it; other editors evaluate as inconclusive); licence: $unity_lic"
    else
      row "unity (optional, Mode 5)" optional "$unity_ver" "$unity_bin; licence: $unity_lic${UNITY_BIN:+ (UNITY_BIN set)}"
    fi
  else
    row "unity (optional, Mode 5)" optional "-" "not installed; only --mode port needs it (setup.sh --unity)"
  fi

  printf '\n%-36s %-9s %-34s %s\n' dependency status version path/note
  printf '%-36s %-9s %-34s %s\n' ------------------------------------ --------- ---------------------------------- ---------
  local r
  for r in "${ROWS[@]}"; do
    IFS=$'\t' read -r n s v d <<<"$r"
    printf '%-36s %-9s %-34s %s\n' "$n" "$s" "${v:0:34}" "$d"
  done
  printf '\n%d required dependency(ies) missing.' "$MISSING"
  [ "$MISSING" -gt 0 ] && printf ' missing: %s' "$(IFS=,; printf '%s' "${MISSING_NAMES[*]}")"
  printf '\n'
  [ "$TODO" -gt 0 ] && printf '%d manual step(s) marked todo (API keys; not needed for --dry-run).\n' "$TODO"
  return 0
}
reset_rows() { ROWS=(); MISSING=0; MISSING_NAMES=(); TODO=0; }

# --check-auth: load the api env file, resolve each harness's route exactly as
# run_benchmark.sh --provider auto does, and let eval/tools/check_auth.py send one
# non-billed probe per filled key. Only variable NAMES and URLs cross into argv/output.
check_auth() {
  local api; api="$(gb_api_env_path)"
  if gb_load_api_env; then
    printf 'api env file: %s\n' "$api"
  else
    printf 'api env file: %s absent — every key reads as not set (cp .gb_api.env.example %s)\n' "$api" "$api"
  fi
  gb_resolve_agent_route codex auto
  export GB_AUTH_CODEX_KIND="$GB_ROUTE_KIND" GB_AUTH_CODEX_BASE_URL="$GB_ROUTE_BASE_URL" GB_AUTH_CODEX_KEY_ENV="$GB_ROUTE_KEY_ENV"
  gb_resolve_agent_route claude auto
  export GB_AUTH_CLAUDE_BASE_URL="$GB_ROUTE_BASE_URL" GB_AUTH_CLAUDE_KEY_ENV="$GB_ROUTE_KEY_ENV"
  export PATH="$GB_TOOLS_BIN:$PATH"
  gb_python_env 2>/dev/null || true
  "$GB_PYTHON" "$GB_ROOT/eval/tools/check_auth.py"
}

# ------------------------------------------------------------------------------
# Install steps
# ------------------------------------------------------------------------------
install_apt() {
  step "apt packages"
  if [ "$SKIP_APT" = 1 ]; then skip "--skip-apt"; return; fi
  if ! command -v apt-get >/dev/null 2>&1; then skip "no apt-get on this system"; return; fi
  # Refresh before selecting packages: minimal images may have no apt index.
  # Ubuntu 24.04 renamed ALSA's runtime package for the time_t transition.
  as_root env DEBIAN_FRONTEND=noninteractive apt-get update -qq || return 1
  local alsa_package=libasound2
  if apt-cache show libasound2t64 >/dev/null 2>&1; then
    alsa_package=libasound2t64
  fi
  local pkgs=(
    ca-certificates curl unzip git git-lfs jq
    python3 python3-venv python3-pip
    xvfb ffmpeg
    fonts-dejavu-core fonts-liberation
    util-linux coreutils
    # runtime libraries the official Godot Linux binary dlopens (X modes under Xvfb
    # need Mesa's software GL; headless runs need only a subset of these)
    libgl1 libgl1-mesa-dri libglu1-mesa libx11-6 libxcursor1 libxinerama1 libxrandr2
    libxi6 libxkbcommon0 "$alsa_package" libpulse0 libfontconfig1 libdbus-1-3 libudev1
  )
  local need=()
  local p
  for p in "${pkgs[@]}"; do
    dpkg-query -W -f='${Status}' "$p" 2>/dev/null | grep -q "install ok installed" || need+=("$p")
  done
  if [ ${#need[@]} -eq 0 ]; then ok "all ${#pkgs[@]} packages present"; return; fi
  as_root env DEBIAN_FRONTEND=noninteractive apt-get install -y -qq --no-install-recommends "${need[@]}"
  ok "installed: ${need[*]}"
  as_root update-ca-certificates >/dev/null 2>&1 || true
}

install_node() {
  step "node >= 20 (for Claude Code / Codex CLI)"
  local major=0
  if command -v node >/dev/null 2>&1; then
    major="$(node --version | sed -E 's/^v([0-9]+).*/\1/')"
  fi
  if [ "${major:-0}" -ge 20 ]; then ok "node $(node --version) at $(command -v node)"; return; fi
  if [ "$SKIP_APT" = 1 ]; then gb_note "   node missing and --skip-apt given: install Node 22 yourself"; return; fi
  # Ubuntu 22.04's apt nodejs is v12; use NodeSource for 22.x.
  as_root bash -c 'curl -fsSL https://deb.nodesource.com/setup_22.x | bash -' || return 0
  as_root env DEBIAN_FRONTEND=noninteractive apt-get install -y -qq nodejs
  ok "node $(node --version)"
}

install_venv() {
  step "python venv $GB_VENV + eval/evalsys/requirements.txt"
  command -v python3 >/dev/null 2>&1 || gb_die "python3 not on PATH (apt install python3 python3-venv, or re-run without --skip-apt)"
  # Isolated on purpose (no --system-site-packages): the entry scripts all resolve
  # GB_PYTHON to this interpreter, so what runs is exactly what requirements.txt
  # pins, independent of whatever the host's python3 happens to carry.
  if [ ! -x "$GB_VENV/bin/python3" ]; then
    python3 -m venv "$GB_VENV" || gb_die "python3 -m venv failed (apt install python3-venv)"
    ok "created $GB_VENV"
  else
    ok "venv exists $GB_VENV"
  fi
  "$GB_VENV/bin/python3" -m pip install -q --disable-pip-version-check --upgrade pip >/dev/null 2>&1 || true
  "$GB_VENV/bin/python3" -m pip install -q --disable-pip-version-check -r "$GB_EVALSYS/requirements.txt" 2> >(grep -v '^DEPRECATION' >&2) \
    || gb_die "pip install -r eval/evalsys/requirements.txt failed in $GB_VENV"
  ok "requirements satisfied ($("$GB_VENV/bin/python3" -c 'import numpy,PIL,jsonschema,certifi,pytest;from importlib.metadata import version as v;print("numpy",v("numpy"),"pillow",v("Pillow"),"jsonschema",v("jsonschema"),"pytest",v("pytest"))'))"
  GB_PYTHON="$GB_VENV/bin/python3"
}

install_godot() {
  step "Godot $GB_GODOT_VERSION headless Linux binary"
  if [ "$SKIP_GODOT" = 1 ]; then skip "--skip-godot"; return; fi
  local want_bin="$GODOT_PREFIX/Godot_v4.5.1-stable_linux.x86_64"
  # Reuse: GODOT_BIN already reports 4.5.1 → only make sure the default path resolves.
  if [ -x "$GODOT_BIN" ] && "$GODOT_BIN" --version 2>/dev/null | grep -q '^4\.5\.1'; then
    ok "GODOT_BIN=$GODOT_BIN reports $("$GODOT_BIN" --version 2>/dev/null | first_line)"
  else
    if [ ! -x "$want_bin" ]; then
      as_root mkdir -p "$GODOT_PREFIX" || return 0
      local tmp; tmp="$(mktemp -d)"
      gb_note "   downloading $GB_GODOT_URL"
      curl -fL --retry 3 -o "$tmp/godot.zip" "$GB_GODOT_URL"
      unzip -q -o "$tmp/godot.zip" -d "$tmp"
      as_root install -m 0755 "$tmp/Godot_v4.5.1-stable_linux.x86_64" "$want_bin"
      rm -rf "$tmp"
    fi
    local got; got="$(sha256sum "$want_bin" | awk '{print $1}')"
    if [ "$got" != "$GB_GODOT_SHA256" ]; then
      gb_die "Godot binary sha256 mismatch: got $got want $GB_GODOT_SHA256 ($want_bin)"
    fi
    ok "sha256 verified $want_bin"
    export GODOT_BIN="$want_bin"
  fi
  # Keep the path the code defaults to resolvable, whatever GODOT_BIN is.
  if [ ! -e /opt/godot451-bin/godot ]; then
    as_root mkdir -p /opt/godot451-bin && as_root ln -sfn "$(readlink -f "$GODOT_BIN")" /opt/godot451-bin/godot \
      && ok "symlink /opt/godot451-bin/godot -> $(readlink -f "$GODOT_BIN")" || gb_note "   could not create /opt/godot451-bin/godot; export GODOT_BIN=$GODOT_BIN before running bench"
  else
    ok "/opt/godot451-bin/godot -> $(readlink -f /opt/godot451-bin/godot)"
  fi
}

install_lfs() {
  step "git lfs"
  if [ "$SKIP_LFS" = 1 ]; then skip "--skip-lfs (reference games/videos use Hugging Face)"; return; fi
  command -v git-lfs >/dev/null 2>&1 || { gb_note "   git-lfs not installed (apt install git-lfs)"; return 0; }
  # --local keeps the filters in this clone's .git/config; nothing user-wide changes.
  if git -C "$GB_ROOT" lfs install --local >/dev/null 2>&1 || git -C "$GB_ROOT" lfs install --local --force >/dev/null 2>&1; then
    ok "lfs filters installed in $(git -C "$GB_ROOT" rev-parse --git-dir)/config"
  else
    gb_note "   git lfs install --local failed"
  fi
  if [ ${#LFS_INCLUDE[@]} -eq 0 ]; then
    skip "reference games/videos use Hugging Face; run_benchmark.sh downloads per selected game"
    gb_note "   to prefetch now: python3 scripts/fetch_reference_data.py --game <id> --with-videos"
    return
  fi
  # lfs.activitytimeout=0: the default 30 s idle timeout aborts large objects on slow
  # proxies with "batch response: ... i/o timeout"; 0 disables it for this command.
  local args=(-c lfs.activitytimeout=0 lfs pull)
  local inc
  for inc in "${LFS_INCLUDE[@]-}"; do [ -n "$inc" ] && args+=(--include "$inc"); done
  gb_note "   git ${args[*]}"
  git -C "$GB_ROOT" "${args[@]}"
  ok "lfs pull done ($(git -C "$GB_ROOT" lfs ls-files | grep -c ' \* ' || true) objects present)"
}

node_platform_suffix() {
  case "$(uname -m)" in
    x86_64|amd64) printf 'linux-x64' ;;
    aarch64|arm64) printf 'linux-arm64' ;;
    *) gb_die "unsupported CPU for the pinned Claude Code / Codex binaries: $(uname -m)" ;;
  esac
}

pkg_version_in() { # prefix package -> installed version or ""
  node -e 'try{console.log(require(process.argv[1]+"/node_modules/"+process.argv[2]+"/package.json").version)}catch(e){console.log("")}' "$1" "$2" 2>/dev/null
}

install_clis() {
  step "Claude Code $GB_CLAUDE_CODE_VERSION and Codex CLI $GB_CODEX_VERSION (npm, pinned, private prefix $GB_TOOLS_ROOT)"
  if [ "$SKIP_CLI" = 1 ]; then skip "--skip-cli"; return; fi
  if ! command -v npm >/dev/null 2>&1 || ! command -v node >/dev/null 2>&1; then gb_note "   npm/node missing; install node first"; return 0; fi
  local plat; plat="$(node_platform_suffix)"
  # Why a private prefix instead of `npm install -g`: both CLIs are thin npm
  # launchers whose real binary lives in a per-platform optional package. The
  # reference host's global @openai/codex lost its platform package ("Missing
  # optional dependency @openai/codex-linux-x64", exit 1) and broke every codex on
  # PATH (ablation0907 worked around it with a native binary copy). Installing the
  # launcher AND its platform package explicitly into a directory nobody else
  # touches keeps the pinned build working whatever happens to the host's node.
  # For Codex the platform package is an alias: @openai/codex@<ver>-<platform>.
  local want=(
    "@anthropic-ai/claude-code@$GB_CLAUDE_CODE_VERSION"
    "@anthropic-ai/claude-code-$plat@$GB_CLAUDE_CODE_VERSION"
    "@openai/codex@$GB_CODEX_VERSION"
    "@openai/codex-$plat@npm:@openai/codex@$GB_CODEX_VERSION-$plat"
  )
  local have_claude have_claude_bin have_codex have_codex_bin
  have_claude="$(pkg_version_in "$GB_TOOLS_ROOT" @anthropic-ai/claude-code)"
  have_claude_bin="$(pkg_version_in "$GB_TOOLS_ROOT" "@anthropic-ai/claude-code-$plat")"
  have_codex="$(pkg_version_in "$GB_TOOLS_ROOT" @openai/codex)"
  have_codex_bin="$(pkg_version_in "$GB_TOOLS_ROOT" "@openai/codex-$plat")"
  # The codex alias package reports "<ver>-<platform>" as its version.
  if [ "$have_claude" = "$GB_CLAUDE_CODE_VERSION" ] && [ "$have_claude_bin" = "$GB_CLAUDE_CODE_VERSION" ] \
     && [ "$have_codex" = "$GB_CODEX_VERSION" ] && [ "$have_codex_bin" = "$GB_CODEX_VERSION-$plat" ]; then
    ok "already at claude-code $have_claude (+$plat), codex $have_codex (+$plat) in $GB_TOOLS_ROOT/node_modules"
  else
    as_root mkdir -p "$GB_TOOLS_ROOT" || return 0
    # sudo resets PATH (secure_path), which hides an nvm node/npm; pass ours through.
    gb_note "   npm install --prefix $GB_TOOLS_ROOT ${want[*]}"
    as_root env PATH="$PATH" HOME="${HOME:-/root}" "$(command -v npm)" install --prefix "$GB_TOOLS_ROOT" \
      --no-fund --no-audit --no-save --omit=dev --loglevel=error "${want[@]}" \
      || gb_die "npm install of the pinned CLIs failed (see above)"
    ok "installed ${want[*]}"
  fi
  # Proxy-neutral shims. Why: on the reference host /usr/local/bin/claude was a site
  # wrapper that forced HTTPS_PROXY to a dead local bridge, so every Claude cell died
  # with ConnectionRefused (live0904). The harness resolves `claude`/`codex` with
  # shutil.which on PATH, so scripts/run_coding.sh prepends GB_TOOLS_BIN and these shims exec
  # the pinned build directly with whatever proxy env the api env file left.
  as_root mkdir -p "$GB_TOOLS_BIN" || return 0
  local node_bin mods; node_bin="$(command -v node)"; mods="$GB_TOOLS_ROOT/node_modules"
  local claude_entry="$mods/@anthropic-ai/claude-code/cli.js"
  # Newer builds ship cli-wrapper.cjs (spawns the native binary from the platform
  # package, as `npm install -g` would); use it when present.
  [ -f "$mods/@anthropic-ai/claude-code/cli-wrapper.cjs" ] && claude_entry="$mods/@anthropic-ai/claude-code/cli-wrapper.cjs"
  local shim
  shim="$(mktemp)"
  cat >"$shim" <<EOF
#!/usr/bin/env bash
# GameBench shim (setup.sh): run the pinned Claude Code $GB_CLAUDE_CODE_VERSION build directly,
# bypassing any site wrapper on PATH. Proxy variables are left exactly as the caller set them.
exec "$node_bin" "$claude_entry" "\$@"
EOF
  as_root install -m 0755 "$shim" "$GB_TOOLS_BIN/claude"
  cat >"$shim" <<EOF
#!/usr/bin/env bash
# GameBench shim (setup.sh): run the pinned Codex CLI $GB_CODEX_VERSION launcher, which finds its
# native binary in the @openai/codex-$plat package installed beside it.
exec "$node_bin" "$mods/@openai/codex/bin/codex.js" "\$@"
EOF
  as_root install -m 0755 "$shim" "$GB_TOOLS_BIN/codex"
  rm -f "$shim"
  local cv xv
  cv="$("$GB_TOOLS_BIN/claude" --version 2>/dev/null </dev/null | first_line || true)"
  xv="$("$GB_TOOLS_BIN/codex" --version 2>/dev/null </dev/null | first_line || true)"
  ok "shims: $GB_TOOLS_BIN/claude (${cv:-BROKEN}), $GB_TOOLS_BIN/codex (${xv:-BROKEN})"
}

install_dirs() {
  step "directories the harness assumes"
  # The unshare sandbox does `mount -t tmpfs tmpfs /data2` and binds the workspace at
  # /workspace; both must exist on the host or the wrapper's `set -e` kills the agent.
  local d
  for d in /data2 /workspace; do
    if [ -d "$d" ]; then ok "$d"; else as_root mkdir -p "$d" && ok "created $d" || gb_note "   $d missing and not creatable"; fi
  done
  mkdir -p "$GB_SCRATCH_ROOT" "$GB_ROUTES_SCRATCH" && ok "scratch $GB_SCRATCH_ROOT"
  local api; api="$(gb_api_env_path)"
  if [ ! -f "$api" ]; then
    mkdir -p "$(dirname "$api")"
    install -m 0600 "$GB_ROOT/.gb_api.env.example" "$api"
    ok "wrote key template to $api  <-- fill OPENAI_* / ANTHROPIC_*, then ./setup.sh --check-auth"
  else
    ok "api env file present at $api"
  fi
}

selfcheck() {
  step "bench selfcheck (scoring kernel goes green and red)"
  ( cd "$GB_EVALSYS" && gb_python_env && "$GB_PYTHON" bin/bench selfcheck >/dev/null ) && ok "selfcheck passed" || gb_note "   selfcheck FAILED — run: cd eval/evalsys && PYTHONPATH=. $GB_PYTHON bin/bench selfcheck"
}

print_unity() {
  cat <<'EOF'

== Unity (optional; only Mode 5 `port` needs it) — manual steps, not automated
  1. Install Unity Hub (Linux .deb / AppImage from unity.com/download) and, through
     the Hub, editor 6000.3.23f1 with the Linux Build Support module. The reference
     host has it at /opt/unity/6000.3.23f1/Editor/Unity; any path works.
  2. Licence: sign in to Unity Hub (a Unity ID) on this host as the same user that
     will run run_benchmark.sh and let it activate a Personal licence. The Hub needs a
     display; on a headless box run it under Xvfb/x11vnc or noVNC. The manual
     .alf/.ulf route (license.unity3d.com/manual) is not offered for Personal
     licences, so do not plan on -createManualActivationFile. Pro/Enterprise serials
     activate with `Unity -batchmode -nographics -quit -serial <key> -username <u> -password <p>`.
  3. Export UNITY_BIN=/path/to/6000.3.23f1/Editor/Unity (or `source /opt/unity/unity_env.sh`
     on the reference host) for run_benchmark.sh / evaluate.sh. `./setup.sh --check`
     row "unity (optional, Mode 5)" reports the editor version and the licence verdict
     from a headless start; run_benchmark.sh --mode port repeats that probe and refuses
     to launch agents while it says MISSING. `bench validate-unity <project>` checks the
     static layout without a licence. Xvfb + ffmpeg (installed above) run the player
     probe. Without a licensed editor eval-task marks every Unity runtime item
     inconclusive (unity_build "licensing infrastructure prevented the evaluator build").
EOF
}

# ------------------------------------------------------------------------------
if [ "$CHECK_AUTH" = 1 ]; then
  if check_auth; then exit 0; else exit 1; fi
fi
if [ "$CHECK" = 1 ]; then
  gb_python_env 2>/dev/null || true
  check_all
  [ "$UNITY" = 1 ] && print_unity
  [ "$MISSING" -eq 0 ] && exit 0 || exit 1
fi

printf 'GameBench setup — repo %s\n' "$GB_ROOT"
[ "$SUDO" = "__no_root__" ] && gb_note "warning: not root and no sudo; system-level steps will be skipped"
install_apt
install_node
install_venv
install_godot
install_lfs
install_clis
install_dirs
selfcheck
[ "$UNITY" = 1 ] && print_unity

# The run is only a success if the same probe `--check` uses finds nothing missing.
step "verification (same table as --check)"
reset_rows
gb_python_env 2>/dev/null || true
check_all
if [ "$MISSING" -gt 0 ]; then
  printf '\nsetup.sh: FAILED — %d required item(s) still missing after install: %s\n' "$MISSING" "$(IFS=,; printf '%s' "${MISSING_NAMES[*]}")" >&2
  printf '   the path/note column above says what each one needs; re-run ./setup.sh after fixing it\n' >&2
  exit 1
fi
printf '\n== done: 0 required items missing. Re-check any time with: ./setup.sh --check\n'
printf '   next: ./run_benchmark.sh --game <id> --mode <brief|gdd|skeleton|bugfix|port> --harness <codex|claude> --model <id> --dry-run\n'
printf '   live runs also need keys in %s (fill, then ./setup.sh --check-auth)\n' "$(gb_api_env_path)"
