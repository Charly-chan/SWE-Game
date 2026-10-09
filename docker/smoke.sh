#!/usr/bin/env bash
set -euo pipefail
target=${1:-all}
if [[ $# -gt 0 ]]; then shift; fi
include_claude=1
while [[ $# -gt 0 ]]; do
    case "$1" in
        --redistributable) include_claude=0; shift ;;
        *) printf 'Unknown option: %s\n' "$1" >&2; exit 2 ;;
    esac
done
case "$target" in godot|unity|all) ;; *) printf 'Usage: %s [godot|unity|all] [--redistributable]\n' "$0" >&2; exit 2 ;; esac
if [[ "$include_claude" == 0 && "$target" != godot ]]; then
    printf '%s\n' '--redistributable is available only for the Godot image' >&2
    exit 2
fi

# No model calls or Unity license activation. Exercise the published toolchain.
if [[ "$target" == godot || "$target" == all ]]; then
docker run --rm --env "INCLUDE_CLAUDE=$include_claude" --entrypoint bash gamebench-agent:godot-4.5.1 -euc '
    godot --headless --version
    node --version
    codex --version
    if [[ "$INCLUDE_CLAUDE" == 1 ]]; then
        claude --version
    else
        if command -v claude >/dev/null || command -v unity >/dev/null; then
            printf '%s\n' 'Unexpected CLI in public Godot image' >&2
            exit 1
        fi
        test ! -e /opt/gamebench-tools/node_modules/@anthropic-ai/claude-code
        test ! -e /opt/gamebench-tools/node_modules/@anthropic-ai/claude-code-linux-x64
        test ! -d /opt/unity
    fi
    ffmpeg -version | head -n 1
    python -c "import numpy, PIL, jsonschema, certifi, pytest, openai"
    smoke_dir=$(mktemp -d)
    cd "$smoke_dir"
    printf "config_version=5\n" > project.godot
    cat > smoke.gd <<'"'"'GDSCRIPT'"'"'
extends SceneTree

func _initialize():
    root.size = Vector2i(128, 128)
    var panel = ColorRect.new()
    panel.size = Vector2(128, 128)
    panel.color = Color(0.2, 0.6, 0.9)
    root.add_child(panel)
    panel.set_anchors_and_offsets_preset(Control.PRESET_FULL_RECT)
    capture.call_deferred()

func capture():
    await process_frame
    await RenderingServer.frame_post_draw
    var picture = root.get_texture().get_image()
    if picture.is_empty() or picture.save_png("render.png") != OK:
        quit(1)
        return
    print("GODOT_RENDER_OK")
    quit(0)
GDSCRIPT
    timeout 60 xvfb-run -a godot --path "$smoke_dir" --rendering-method gl_compatibility --audio-driver Dummy --script smoke.gd
    python -c "from PIL import Image; im=Image.open(\"render.png\"); assert im.width > 0 and im.height > 0; pixel=im.convert(\"RGB\").getpixel((im.width//2,im.height//2)); assert pixel[2] > pixel[1] > pixel[0]; print(\"PNG_OK\", im.size, pixel)"
'
fi
if [[ "$target" == unity || "$target" == all ]]; then
unity_image=${GB_MODE5_IMAGE:-gamebench-mode5-agent:6000.3.23f1-v1}
docker run --rm --entrypoint bash "$unity_image" -euc '
    codex --version
    claude --version
    unity -version
    gb-unity --help >/dev/null
    test -s /opt/gamebench/mode5/GBCommunityBuild.cs
    test -d /opt/unity/6000.3.23f1/Editor/Data/PlaybackEngines/LinuxStandaloneSupport/Variations/linux64_player_nondevelopment_mono
    printf "UNITY_MONO_AND_SELFCHECK_OK\n"
'
fi
