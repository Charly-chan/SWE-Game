#!/usr/bin/env bash
set -euo pipefail

readonly root='/var/lib/gamebench/sandbox-certification/candidate'
readonly project="$root/project"
readonly build="$root/build"
readonly runtime="$root/runtime"
readonly logs="$root/logs"
readonly protocol_project="$root/protocol-project"
readonly protocol_build="$root/protocol-build"
readonly protocol_logs="$root/protocol-logs"
readonly cat_project="$root/cat-project"
readonly cat_build="$root/cat-build"
readonly cat_logs="$root/cat-logs"
readonly target_project="$root/target-project"
readonly target_logs="$root/target-logs"
readonly observer_build="$root/observer-build"
readonly observer_logs="$root/observer-logs"

rm -rf "$project" "$build" "$runtime" "$logs"
rm -rf "$protocol_project" "$protocol_build" "$protocol_logs"
rm -rf "$cat_project" "$cat_build" "$cat_logs"
rm -rf "$target_project" "$target_logs" "$observer_build" "$observer_logs"
install -d -m 0700 "$project" "$build" "$runtime" "$logs" \
    "$protocol_project" "$protocol_build" "$protocol_logs" \
    "$cat_project" "$cat_build" "$cat_logs" "$target_project" "$target_logs" \
    "$observer_build" "$observer_logs"
tar -xzf "$root/blank-fixture.tar.gz" -C "$project"

export DISPLAY=:98
export GB_FIXTURE_BUILD_DIR="$build"
Xvfb :98 -screen 0 960x540x24 -nolisten tcp -ac >"$logs/xvfb.log" 2>&1 &
xvfb_pid=$!
trap 'kill "$xvfb_pid" 2>/dev/null || true' EXIT
for _ in $(seq 1 50); do
    xdpyinfo -display :98 >/dev/null 2>&1 && break
    sleep 0.2
done
xdpyinfo -display :98 >/dev/null

tar -xzf "$root/target-scaffold.tar.gz" -C "$target_project"
tar -xzf "$root/observer-fixture.tar.gz" -C "$target_project"
install -d "$target_project/Assets/GameBenchmarkEvaluator"
cp "$root/unity-runtime-observer.cs" \
    "$target_project/Assets/GameBenchmarkEvaluator/GameBenchmarkEvaluatorProbe.cs"
export GB_FIXTURE_BUILD_DIR="$observer_build"
/opt/unity/current/Editor/Unity \
    -batchmode -nographics \
    -projectPath "$target_project" \
    -executeMethod GameBench.Fixtures.Observer.Editor.BuildObserverFixture.BuildLinuxPlayer \
    -quit -logFile "$observer_logs/editor.log"
cp "$observer_logs/editor.log" "$target_logs/editor.log"
printf 'target_scaffold_import=passed\n'
printf 'observer_editor_build=passed\n'
test -x "$observer_build/ObserverFixture.x86_64"
"$observer_build/ObserverFixture.x86_64" \
    -screen-fullscreen 0 -screen-width 960 -screen-height 540 -screen-refresh-rate 60 \
    -logFile "$observer_logs/player.log" \
    --gb-controller-port="$GB_OBSERVER_CONTROLLER_PORT" \
    --gb-run-id=m5-104-observer \
    --gb-nonce="$GB_PROTOCOL_NONCE" \
    --gb-build-digest=trusted-observer-fixture \
    --gb-start-scene=Assets/Game/Scenes/ObserverFixture.unity \
    --gb-level-count=1 \
    --gb-required-roles=gb_player,gb_enemy,gb_goal,gb_projectile \
    --gb-numeric-slots=health,score,input_performed \
    --gb-supported-actions=gb_action
printf 'observer_player=passed\n'

export GB_FIXTURE_BUILD_DIR="$build"
/opt/unity/current/Editor/Unity \
    -batchmode -nographics \
    -projectPath "$project" \
    -executeMethod GameBench.Fixtures.Editor.BuildBlankFixture.BuildLinuxPlayer \
    -quit -logFile "$logs/editor.log"
printf 'blank_editor_build=passed\n'
test -x "$build/BlankFixture.x86_64"
"$build/BlankFixture.x86_64" \
    -screen-fullscreen 0 -screen-width 960 -screen-height 540 -screen-refresh-rate 60 \
    -logFile "$logs/player.log" --gb-output="$runtime"
printf 'blank_player=passed\n'

tar -xzf "$root/protocol-fixture.tar.gz" -C "$protocol_project"
export GB_FIXTURE_BUILD_DIR="$protocol_build"
/opt/unity/current/Editor/Unity \
    -batchmode -nographics \
    -projectPath "$protocol_project" \
    -executeMethod GameBench.Fixtures.Protocol.Editor.BuildProtocolFixture.BuildLinuxPlayer \
    -quit -logFile "$protocol_logs/editor.log"
printf 'protocol_editor_build=passed\n'
test -x "$protocol_build/ProtocolFixture.x86_64"
"$protocol_build/ProtocolFixture.x86_64" \
    -screen-fullscreen 0 -screen-width 960 -screen-height 540 -screen-refresh-rate 60 \
    -logFile "$protocol_logs/player.log" \
    --gb-controller-port="$GB_PROTOCOL_CONTROLLER_PORT" \
    --gb-run-id=m5-110-protocol \
    --gb-nonce="$GB_PROTOCOL_NONCE" \
    --gb-build-digest=trusted-protocol-fixture
printf 'protocol_player=passed\n'

tar -xzf "$root/target-scaffold.tar.gz" -C "$cat_project"
tar -xzf "$root/behavior-causality-fixture.tar.gz" -C "$cat_project"
install -d "$cat_project/Assets/GameBenchmarkEvaluator"
cp "$root/unity-runtime-observer.cs" \
    "$cat_project/Assets/GameBenchmarkEvaluator/GameBenchmarkEvaluatorProbe.cs"
export GB_FIXTURE_BUILD_DIR="$cat_build"
/opt/unity/current/Editor/Unity \
    -batchmode -nographics \
    -projectPath "$cat_project" \
    -executeMethod GameBench.Fixtures.CatDefense.Editor.BuildCatDefenseFixture.BuildLinuxPlayer \
    -quit -logFile "$cat_logs/editor.log"
printf 'cat_editor_build=passed\n'
test -x "$cat_build/CatDefenseFixture.x86_64"

IFS=',' read -r -a cat_ports <<<"$GB_CAT_CONTROLLER_PORTS"
readonly cat_sessions=(
    positive_01 positive_02 positive_03 positive_04 positive_05
    no_fire_control auto_win input_ignored enemy_auto_disappears
    telemetry_fake witness_candidate witness_hidden visual_only
)
readonly cat_variants=(
    positive positive positive positive positive
    positive auto_win input_ignored enemy_auto_disappears
    telemetry_fake witness_only witness_only visual_only
)
test "${#cat_ports[@]}" -eq "${#cat_sessions[@]}"
run_cat_player() {
    local index="$1"
    session="${cat_sessions[$index]}"
    variant="${cat_variants[$index]}"
    "$cat_build/CatDefenseFixture.x86_64" \
        -screen-fullscreen 0 -screen-width 960 -screen-height 540 -screen-refresh-rate 60 \
        -logFile "$cat_logs/$session.log" \
        --gb-controller-port="${cat_ports[$index]}" \
        --gb-run-id="m5-004-cat-$session" \
        --gb-nonce="$GB_PROTOCOL_NONCE" \
        --gb-build-digest=trusted-cat-defense-fixture \
        --gb-fixture-variant="$variant" \
        --gb-start-scene=Assets/Scenes/CatDefenseFixture.unity \
        --gb-level-count=1 \
        --gb-required-roles=gb_player,gb_enemy,gb_goal,gb_projectile,gb_interactive \
        --gb-numeric-slots=health,score,coins \
        --gb-supported-actions=gb_left,gb_right,gb_up,gb_down,gb_jump,gb_action
    printf 'cat_player_%s=passed\n' "$session"
}

for index in "${!cat_sessions[@]}"; do
    run_cat_player "$index"
done

export GB_UNITY_FIXTURE_ROOT="$root"
exec python3 "$root/candidate_probe.py"
