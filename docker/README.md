# Coding-agent toolchain images

These Linux x86-64 images provide the tools used to generate games and run the
coding agent's own checks. Task inputs and submissions are mounted at runtime.

## Directory contents

| File | Purpose |
| --- | --- |
| [`Dockerfile.godot`](Dockerfile.godot) | Build the pinned Godot and agent CLI toolchain |
| [`Dockerfile.unity`](Dockerfile.unity) | Add the Unity editor and Linux build support to the Godot image |
| [`Dockerfile.unity-local`](Dockerfile.unity-local) | Build the Unity image from archives already available on the host |
| [`build.sh`](build.sh) | Build either image or both, using downloads or supplied Unity archives |
| [`smoke.sh`](smoke.sh) | Check installed tools and render a Godot frame inside the images |

## Images

| Image | Modes | Tools |
| --- | --- | --- |
| `gamebench-agent:godot-4.5.1` | 1–4 | Godot 4.5.1, Python 3.10 and `eval/evalsys/requirements.txt`, Node 22.22.0, Codex 0.153.4, Claude Code 2.1.222, FFmpeg, Xvfb, Mesa |
| `gamebench-agent:unity-6000.3.23f1` | 5 | The Godot image plus Unity Editor 6000.3.23f1 and Linux IL2CPP build support |

Both images default to `agent` (UID 1000), with `/workspace` as the working
directory. The benchmark runner overrides the container UID/GID with the host
operator's UID/GID, so a root operator also runs as root inside the container.
`godot`, `python`, `python3`, `codex`, `claude`, `ffmpeg`, and `xvfb-run` are on
`PATH`. `GODOT_BIN` and `GB_PYTHON` point to the installed tools. The Unity image
also sets `UNITY_BIN` and provides `unity` on `PATH`.

## Pull published images

Images are published to `ghcr.io/charly-chan/swe-game` with engine-specific tags.
For a private package, authenticate with GitHub Packages before pulling.

```bash
docker pull ghcr.io/charly-chan/swe-game:godot-4.5.1
docker pull ghcr.io/charly-chan/swe-game:unity-6000.3.23f1
docker tag ghcr.io/charly-chan/swe-game:godot-4.5.1 gamebench-agent:godot-4.5.1
docker tag ghcr.io/charly-chan/swe-game:unity-6000.3.23f1 gamebench-agent:unity-6000.3.23f1
```

The local aliases match the runner defaults. Alternatively, pass the full GHCR
image name with `--docker-image`. Published images also have an engine tag
suffixed with the 12-character source revision for reproducible selection.

The runner uses an image already available to the selected Docker daemon and
pulls only when it is missing. Rebuild or pull explicitly to update a cached tag.
For a remote daemon, build or pull on that daemon, or select an accessible
registry tag with `--docker-image` (`GB_SANDBOX_IMAGE` for Godot and
`GB_UNITY_SANDBOX_IMAGE` for Unity). A local build on another daemon is not shared
automatically.

The [publish workflow](../.github/workflows/publish-container-images.yml) builds
both images, checks installed tools and a rendered Godot frame, then publishes
using the repository's GitHub Actions token. It runs on relevant changes to
`main`, `toolchain-*` tags, or manual dispatch. Run `./docker/smoke.sh` locally
after building to execute the same toolchain checks.

## Build

From the repository root, with Docker running:

```bash
./docker/build.sh all
```

To build only the Godot image:

```bash
./docker/build.sh godot
```

To build Unity after the Godot image exists:

```bash
./docker/build.sh unity
```

The default Unity build streams the official archives from the
[Unity 6000.3.23f1 release](https://unity.com/releases/editor/whats-new/6000.3.23f1).
Existing downloads can be reused:

```bash
./docker/build.sh all --unity-archives /path/to/unity-downloads
```

That directory must contain these two files:

- `Unity-6000.3.23f1.tar.xz`
- `UnitySetup-Linux-IL2CPP-Support-for-Editor-6000.3.23f1.tar.xz`

The local build creates a temporary directory beside the archives and stages
hard links, then removes that directory when it exits. Docker `ADD` extracts the
archives directly into the image. The directory must be writable. No extra
multi-gigabyte archive copy is made in the repository, and the compressed
archives are not retained in an image layer. The Docker daemon still receives
the archive data as build context.

The Godot build context contains only its Dockerfile and the Python dependency
file; the Unity context contains only its Dockerfile and, for local builds, the
two archives. No repository checkout, reference game, hidden evaluation input,
model credential, or Unity license is included. Standard `HTTP_PROXY`,
`HTTPS_PROXY`, and `NO_PROXY` values are forwarded as Docker's predefined build
arguments. They are not configured as image environment variables.

## Run the benchmark

Configure the model provider as in the main [running guide](../docs/running.md),
then set `MODEL_ID` to the model you intend to run. Modes 1–3 use `brief`, `gdd`,
and `skeleton` respectively:

```bash
for mode in brief gdd skeleton; do
  ./run_benchmark.sh --game shadow_walker --mode "$mode" \
    --harness codex --model "$MODEL_ID" --sandbox docker --eval off \
    --out "results/docker-$mode"
done
```

Mode 4 requires an active bug case; `canopy_dash` provides one:

```bash
./run_benchmark.sh --game canopy_dash --mode bugfix \
  --harness codex --model "$MODEL_ID" --sandbox docker --eval off \
  --out results/docker-bugfix
```

Mode 5 selects the Unity image. If you already have a license file prepared
for the container environment, expose its host path through the operator's
environment or the run's agent env file:

```bash
export GB_UNITY_LICENSE_FILE=/absolute/path/to/operator-license.ulf
```

The Docker port runner accepts `.ulf` or `.xml` and installs the file in the
container's private user configuration. It keeps the license outside the task
workspace and image; the runtime metadata records only whether one was supplied
and its format. This supplies an existing license; it does not activate one.
The actual Unity startup or build determines whether it is valid. See Unity's
[license file locations](https://docs.unity3d.com/6000.3/Documentation/Manual/ActivationFAQ.html#licensefilefolders)
and [manual activation scope](https://docs.unity3d.com/6000.3/Documentation/Manual/ManualActivationGuide.html).

```bash
./run_benchmark.sh --game shadow_walker --mode port \
  --harness codex --model "$MODEL_ID" --sandbox docker --eval off \
  --out results/docker-port
```

`--docker-image TAG` overrides the default image. The lower-level coding entry
accepts the same `--sandbox docker`, `--docker-image`, and `--eval` options.
To select Docker for the existing experiment matrix:

```bash
AGENT_SANDBOX=docker EVAL=off ./scripts/experiments/run_all.sh main
```

For Codex, the runner automatically selects Docker's `seccomp=unconfined` and
`apparmor=unconfined` security options so its bundled bubblewrap can create the
inner sandbox. The container still receives only the task workspace mounts, and
Codex retains its `workspace-write` sandbox. No manual Docker profile setup is
needed.

These examples retain submissions and agent logs with evaluation deferred.
Append `--dry-run` to an individual `run_benchmark.sh` command to inspect its
planned run before making model calls.

For formal Mode 5 evaluation, transfer the complete cell `package/` and final
`submission/` to the configured Windows/QEMU host, which starts the prescribed
Ubuntu guest. On that Windows host, with the repository as the working
directory and an activated VM already configured:

```powershell
$PY = 'C:\path\to\python.exe'
$env:PYTHONPATH = (Resolve-Path eval/evalsys).Path
& $PY eval/evalsys/bin/bench eval-task `
  --package 'D:\runs\CELL\package' `
  --submission 'D:\runs\CELL\submission' `
  --out 'D:\runs\CELL\evaluation' `
  --engine on --unity-vm on --visual-judge none
```

The output directory must be new. See the [VM instructions](../eval/infra/unity/README.md)
for provisioning and non-default host paths. Read the resulting report's score,
`ranking_eligible`, and unresolved status; container self-checks do not establish
VM calibration or scoring readiness.

## Use tools directly

Inspect installed versions without contacting a model provider:

```bash
docker run --rm gamebench-agent:godot-4.5.1 bash -c \
  'godot --headless --version && node --version && codex --version && claude --version'
docker run --rm gamebench-agent:unity-6000.3.23f1 unity -version
```

For an interactive task workspace, mount the task's public input/output
directory, writable by UID 1000:

```bash
docker run --rm -it \
  --mount type=bind,src=/absolute/path/to/task-workspace,dst=/workspace \
  gamebench-agent:godot-4.5.1
```

Run a Godot project with a virtual display and software OpenGL:

```bash
xvfb-run -a godot --path /workspace/project --rendering-method gl_compatibility
```

Use the Unity image for porting and Unity self-checks. Runtime model credentials
and a Unity licensing configuration must be supplied by the caller. Keep these
outside the image and the task submission. Unity editor startup and builds
require a valid license for the container's runtime user; an editor version
check alone does not establish that licensing works.

## Limitations

The images target Linux x86-64. Godot export templates are not installed; direct
editor import and project execution are available. Unity license activation is
not performed during the build. Supplying a `.ulf` or `.xml` is for supported
license types already prepared for this environment; this does not provide a
Personal or Hub license migration flow. Valid-license import and Player build
still need to be exercised on the actual runtime environment. Mode 5's formal evaluation uses its prescribed
VM environment; these containers provide generation and self-check tools and
do not make a run eligible for an official Unity score.
