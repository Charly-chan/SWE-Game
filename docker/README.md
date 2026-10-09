# Coding-agent toolchain images

These Linux x86-64 images provide the tools used to generate games and run the
coding agent's own checks. Task inputs and submissions are mounted at runtime.

## Directory contents

| File | Purpose |
| --- | --- |
| [`Dockerfile.godot`](Dockerfile.godot) | Build the pinned Godot and agent CLI toolchain |
| [`Dockerfile.unity-local`](Dockerfile.unity-local) | Build the Unity image from archives already available on the host |
| [`build.sh`](build.sh) | Build either image or both, using downloads or supplied Unity archives |
| [`smoke.sh`](smoke.sh) | Check installed tools and render a Godot frame inside the images |

## Images

| Image | Modes | Tools |
| --- | --- | --- |
| `ghcr.io/charly-chan/swe-game-public:godot-4.5.1` | 1–4, Codex | Godot 4.5.1, Python dependencies, Node 22.22.0, Codex 0.153.4, FFmpeg, Xvfb, Mesa |
| `gamebench-agent:godot-4.5.1` (local build) | 1–4 | Godot 4.5.1, Python 3.10 and `eval/evalsys/requirements.txt`, Node 22.22.0, Codex 0.153.4, Claude Code 2.1.222, FFmpeg, Xvfb, Mesa |
| `gamebench-mode5-agent:6000.3.23f1-v1` | 5 | The Godot toolchain plus locally installed Unity Editor and Linux Player build support (Mono) |
| `gamebench-mode5-evaluator:6000.3.23f1-v1` | 5 | The independent offline evaluator toolchain |

Toolchain containers default to `agent` (UID 1000), with `/workspace` as the working
directory. The benchmark runner overrides the container UID/GID with the host
operator's UID/GID, so a root operator also runs as root inside the container.
`godot`, `python`, `python3`, `codex`, `ffmpeg`, and `xvfb-run` are on
`PATH`; local agent builds also include `claude`. `GODOT_BIN` and `GB_PYTHON` point to the installed tools. The Unity image
also sets `UNITY_BIN` and provides `unity` on `PATH`.

## Pull published images

The public Godot image is published to `ghcr.io/charly-chan/swe-game-public`
and includes Codex. For Claude Code, build the local image as described below;
the build installs the pinned CLI directly from its official npm packages.
Mode 5 images are built locally by `gb mode5 setup`.

```bash
docker pull ghcr.io/charly-chan/swe-game-public:godot-4.5.1
docker tag ghcr.io/charly-chan/swe-game-public:godot-4.5.1 gamebench-agent:godot-4.5.1
```

The local aliases match the runner defaults. Alternatively, pass the full GHCR
image name with `--docker-image`. Published images also have an engine tag
suffixed with the 12-character source revision for reproducible selection.
For example, the `v1.0.0` publication is available as
`ghcr.io/charly-chan/swe-game-public:godot-4.5.1-eb1cad45eef3`.
For a recorded run, select that revision tag (or its registry digest) explicitly
with `--docker-image` and save `docker image inspect --format '{{.Id}}' IMAGE`
alongside the evaluator commit. The unsuffixed engine tag can be republished.
Local Mode 5 builds use the image IDs recorded in
`~/.cache/gamebench/mode5/image-lock.json` (or the selected `--state-dir`);
preserve this lock with the run's provenance, without including license files.

The runner uses an image already available to the selected Docker daemon and
pulls only when it is missing. Rebuild or pull explicitly to update a cached tag.
For a remote daemon, build or pull on that daemon, or select an accessible
registry tag with `--docker-image` (`GB_SANDBOX_IMAGE` for Godot and
the Community image lock for Unity). A local build on another daemon is not shared
automatically.

The [publish workflow](../.github/workflows/publish-container-images.yml) builds
the Godot image with `--redistributable`, checks installed tools and a rendered
frame, verifies that Claude Code is absent, then publishes
using the repository's GitHub Actions token. It runs on relevant changes to
`main`, `toolchain-*` tags, or manual dispatch. After a local build, run
`./docker/smoke.sh godot`. To reproduce the published variant, run
`./docker/build.sh godot --redistributable` followed by
`./docker/smoke.sh godot --redistributable`.

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
./gb mode5 setup
```

Community setup downloads the fixed official archives from the
[Unity 6000.3.23f1 release](https://unity.com/releases/editor/whats-new/6000.3.23f1).
Existing downloads can be reused:

```bash
./gb mode5 setup --unity-archives /path/to/unity-downloads --no-download
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
two archives and the public Mode 5 helper. No repository checkout, reference game, hidden evaluation input,
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

Mode 5 uses the [Community Docker workflow](../docs/reference/MODE5_RELEASE.md).
`./gb mode5 setup` builds locally from official Unity archives, and doctor checks
license, compilation, Linux Player build and offline runtime/capture before any
agent calls. The independent evaluator uses Unity 6000.3.23f1 / StandaloneLinux64 /
Mono. Setup records digest-pinned images in private local state.

Use `gb mode5 run` to retain the original submission and evaluate it in a fresh
offline container. `gb mode5 evaluate` accepts existing submissions; rejudge
uses retained captures. Unity activation and model credentials stay outside
public images and task inputs.

## Use tools directly

Inspect installed versions without contacting a model provider:

```bash
docker run --rm gamebench-agent:godot-4.5.1 bash -c \
  'godot --headless --version && node --version && codex --version'
docker run --rm gamebench-mode5-agent:6000.3.23f1-v1 unity -version
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
are checked by doctor on the actual runtime environment. Complete Mode 5 scores
also require runtime and fidelity measurements; see the Community guide.
