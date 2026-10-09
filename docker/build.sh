#!/usr/bin/env bash
set -euo pipefail

docker_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
repo_dir=$(cd -- "$docker_dir/.." && pwd)
target=${1:-all}
if [[ $# -gt 0 ]]; then shift; fi
unity_archives=
include_claude=1
while [[ $# -gt 0 ]]; do
    case "$1" in
        --unity-archives) unity_archives=$(cd -- "${2:?directory required}" && pwd); shift 2 ;;
        --redistributable) include_claude=0; shift ;;
        *) printf 'Unknown option: %s\n' "$1" >&2; exit 2 ;;
    esac
done
case "$target" in godot|unity|all) ;; *) printf 'Usage: %s [godot|unity|all] [--unity-archives DIR] [--redistributable]\n' "$0" >&2; exit 2 ;; esac
if [[ "$include_claude" == 0 && "$target" != godot ]]; then
    printf '%s\n' '--redistributable is available only for the Godot image' >&2
    exit 2
fi

build_context=
cleanup() { [[ -z "$build_context" ]] || rm -rf -- "$build_context"; }
trap cleanup EXIT

if [[ "$target" == godot || "$target" == all ]]; then
    build_context=$(mktemp -d)
    cp "$docker_dir/Dockerfile.godot" "$build_context/Dockerfile"
    cp "$repo_dir/eval/evalsys/requirements.txt" "$build_context/requirements.txt"
    docker build --network host --platform linux/amd64 --build-arg HTTP_PROXY --build-arg HTTPS_PROXY --build-arg NO_PROXY --build-arg "INCLUDE_CLAUDE=$include_claude" -t gamebench-agent:godot-4.5.1 "$build_context"
    cleanup; build_context=
fi
if [[ "$target" == unity || "$target" == all ]]; then
    unity_args=(mode5 setup)
    [[ -z "$unity_archives" ]] || unity_args+=(--unity-archives "$unity_archives" --no-download)
    "$repo_dir/gb" "${unity_args[@]}"
fi
