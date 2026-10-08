"""Maintainer-only preparation of Mode 5 fixed HF task packages."""

import json
from pathlib import Path
from ..package import TaskPackage, write_json

def prepare_scaffold(package: TaskPackage) -> None:
    """Adapt the released port scaffold without rebuilding the frozen task."""
    import json
    from ..unity.unity_sdk import (
        build_scaffold_digest_manifest, enable_community_engine_modules,
        immutable_scaffold_paths, scaffold_manifest_digest,
    )
    from ..package import sha256_file
    project = package.visible / 'target_unity'
    enable_community_engine_modules(project)
    files = build_scaffold_digest_manifest(project, immutable_scaffold_paths(project))
    digest = scaffold_manifest_digest(files)
    interface_path = project / 'Assets/GameBenchmark/gb_interface.json'
    interface = json.loads(interface_path.read_text(encoding='utf-8'))
    interface['scaffold_digest'] = digest
    write_json(interface_path, interface)
    integrity_path = package.hidden / 'unity/scaffold_integrity.json'
    integrity = json.loads(integrity_path.read_text(encoding='utf-8'))
    integrity.update(profile_id='mode5-community-docker-v1', files=files, scaffold_digest=digest)
    write_json(integrity_path, integrity)
    lock_path = package.visible / 'environment.lock.json'
    _write_mode5_environment_declaration(package.visible, community_scaffold=True)
    package.manifest['environment_lock'] = {
        'path': 'visible/environment.lock.json', 'sha256': sha256_file(lock_path),
    }
    package.manifest['mode5_scaffold'] = 'community-builtins-v2'
    prompt = package.visible / 'PROMPT.md'
    note = '\n\nRead `ENVIRONMENT.md` for the licensed Community toolchain and required `gb-unity` self-checks.\n'
    if note.strip() not in prompt.read_text(encoding='utf-8'):
        with prompt.open('a', encoding='utf-8') as stream:
            stream.write(note)
    package.write_manifest()


def _write_mode5_environment_declaration(
    visible: Path, *, community_scaffold: bool = False,
) -> None:
    """Tell the Agent what Unity exists at runtime without bundling the Editor."""
    from .community_profile import load_default_profile, task_environment_lock
    profile = load_default_profile()
    write_json(visible / "environment.lock.json", task_environment_lock(profile))
    community_note = (
        "The Community scaffold enables the Editor's built-in uGUI 2.0.0 and "
        "common Tilemap/audio/animation/particle/navigation/terrain modules; "
        "its dependency versions and SDK interface source are protected. "
        "Unity-generated .meta and package-lock metadata changes are allowed; "
        "JSON formatting changes are not a submission error. "
        "Put game scripts in the default Assembly-CSharp rather "
        "than adding an `Assets/Game` asmdef that cannot reference the scaffold "
        "SDK's Assembly-CSharp types. "
    ) if community_scaffold else ""
    (visible / "ENVIRONMENT.md").write_text(
        "# Mode 5 build environment\n\n"
        f"This task runs in `{profile.profile_id}` with Unity "
        f"`{profile.unity_editor}` (`{profile.unity_changeset}`) available as "
        "`$UNITY_BIN` and `unity`. The task package intentionally does not contain "
        "the Unity Editor or a license.\n\n"
        "Before submission, import and compile `target_unity/` and build the Linux "
        f"Player for `{profile.build_target}` using `{profile.scripting_backend}`. "
        "The starting scaffold is an SDK and dependency skeleton, not a playable "
        "game: `gb-unity import` can check it immediately, but `gb-unity check` "
        "requires you to create at least one buildable scene first. "
        "Run the public, non-scoring helper from the task workspace:\n\n"
        "```bash\n"
        "gb-unity check \"$GB_TASK_WORKSPACE/target_unity\" \\\n"
        "  --out \"$GB_TASK_WORKSPACE/.selfcheck/build\" --timeout 900\n"
        "```\n\n"
        "`gb-unity import`, `gb-unity build`, and `gb-unity smoke` are also available. "
        "A first Unity import or Linux Player build may take several minutes. "
        "If your coding tool has a shorter per-command timeout, raise it above the "
        "helper timeout or run the check asynchronously and poll for its actual "
        "exit code and `.selfcheck/status.json`. A tool timeout or truncated log "
        "is not a C# compiler result. "
        "The helper records import, C# compile, Linux Player build, and smoke status in "
        "`.selfcheck/status.json`; it contains no hidden predicates or scoring logic. "
        "Treat a nonzero exit as a blocking failure and fix it before copying the project "
        "to `submission/`. Once a complete submission passes the required self-check, "
        "reserve time to finish your Agent turn before its deadline: the independent "
        "evaluator starts only after the Agent exits. Optional repeated probes must "
        "not consume the whole Agent budget. "
        + community_note +
        "The benchmark performs an independent clean rebuild and runtime suite in "
        "a separate offline evaluator container. Do not copy license state, model "
        "credentials, evaluator files, or host paths into the submission.\n",
        encoding="utf-8",
    )
