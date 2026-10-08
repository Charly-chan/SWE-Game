#!/usr/bin/env bash
set -euo pipefail

docker_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
repo_dir=$(cd -- "$docker_dir/.." && pwd)
target=${1:-all}
if [[ $# -gt 0 ]]; then shift; fi
unity_archives=
while [[ $# -gt 0 ]]; do
    case "$1" in
        --unity-archives) unity_archives=$(cd -- "${2:?directory required}" && pwd); shift 2 ;;
        *) printf 'Unknown option: %s\n' "$1" >&2; exit 2 ;;
    esac
done
case "$target" in godot|unity|all) ;; *) printf 'Usage: %s [godot|unity|all] [--unity-archives DIR]\n' "$0" >&2; exit 2 ;; esac

build_context=
cleanup() { [[ -z "$build_context" ]] || rm -rf -- "$build_context"; }
trap cleanup EXIT

if [[ "$target" == godot || "$target" == all ]]; then
    build_context=$(mktemp -d)
    cp "$docker_dir/Dockerfile.godot" "$build_context/Dockerfile"
    cp "$repo_dir/eval/evalsys/requirements.txt" "$build_context/requirements.txt"
    docker build --network host --platform linux/amd64 --build-arg HTTP_PROXY --build-arg HTTPS_PROXY --build-arg NO_PROXY -t gamebench-agent:godot-4.5.1 "$build_context"
    cleanup; build_context=
fi
if [[ "$target" == unity || "$target" == all ]]; then
    if [[ -n "$unity_archives" ]]; then
        # Same filesystem as the archives: staging uses hard links, not multi-GB copies.
        build_context=$(mktemp -d "$unity_archives/.gamebench-docker.XXXXXX")
        cp "$docker_dir/Dockerfile.unity-local" "$build_context/Dockerfile"
        ln "$unity_archives/Unity-6000.3.23f1.tar.xz" "$build_context/"
        ln "$unity_archives/UnitySetup-Linux-IL2CPP-Support-for-Editor-6000.3.23f1.tar.xz" "$build_context/"
    else
        build_context=$(mktemp -d)
        cp "$docker_dir/Dockerfile.unity" "$build_context/Dockerfile"
    fi
    docker build --network host --platform linux/amd64 --build-arg HTTP_PROXY --build-arg HTTPS_PROXY --build-arg NO_PROXY -t gamebench-agent:unity-6000.3.23f1 "$build_context"
fi
