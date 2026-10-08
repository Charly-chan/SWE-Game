#!/usr/bin/env bash
# gb_docker_sweep.sh — list or remove leftover --agent-sandbox docker containers.
#
#   bash eval/tools/gb_docker_sweep.sh            # report only (default)
#   bash eval/tools/gb_docker_sweep.sh --remove   # remove the dead ones
#
# A SIGKILL'd harness leaves its keepalive container running. Removal is gated on
# the owning harness PID being gone, so a sweep never kills a live run.
#
# Every query filters on label=gamebench.sandbox. The Docker endpoint may be
# shared with other tenants, so an unfiltered `docker ps -q` here would be a
# cross-tenant incident, not a cleanup.
set -euo pipefail

MODE=report
while [ $# -gt 0 ]; do
  case "$1" in
    --report) MODE=report ;;
    --remove) MODE=remove ;;
    -h|--help) sed -n '2,12p' "$0"; exit 0 ;;
    *) printf 'error: unknown option %s (try --help)\n' "$1" >&2; exit 2 ;;
  esac
  shift
done

command -v docker >/dev/null 2>&1 || { printf 'no docker client on PATH\n'; exit 0; }
timeout 60 docker version >/dev/null 2>&1 || {
  printf 'docker daemon unreachable (DOCKER_HOST=%s)\n' "${DOCKER_HOST:-<unset>}"; exit 0; }

ids="$(docker ps -aq --filter "label=gamebench.sandbox" 2>/dev/null || true)"
[ -n "$ids" ] || { printf 'no gamebench sandbox containers\n'; exit 0; }

swept=0 kept=0
for id in $ids; do
  meta="$(docker inspect --format \
    '{{.Name}}	{{index .Config.Labels "gamebench.pid"}}	{{index .Config.Labels "gamebench.cell"}}	{{.State.Status}}' \
    "$id" 2>/dev/null || true)"
  [ -n "$meta" ] || continue
  IFS=$'\t' read -r name pid cell status <<<"$meta"
  if [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null; then
    printf 'live    %s (%s) pid %s cell %s\n' "$name" "$status" "$pid" "$cell"
    kept=$((kept + 1))
    continue
  fi
  if [ "$MODE" = remove ]; then
    docker rm -f -v "$id" >/dev/null 2>&1 && printf 'removed %s (cell %s)\n' "$name" "$cell"
  else
    printf 'orphan  %s (%s) pid %s cell %s\n' "$name" "$status" "${pid:-unknown}" "$cell"
  fi
  swept=$((swept + 1))
done
printf '%s: %d orphan(s), %d live\n' "$MODE" "$swept" "$kept"
