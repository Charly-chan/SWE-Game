# Unity 6.3 evaluator environment

This directory builds and certifies the execution environment used by the
Godot-to-Unity Porting task. Start here when reproducing the VM, changing
the Unity version, or diagnosing build, license, display, sandbox, or artifact
export failures.

For the benchmark task, package layout, unified Mode 1--5 CLI, evaluator code
map, and readiness terminology, start with
[`docs/reference/UNITY_MODE5.md`](../../../docs/reference/UNITY_MODE5.md). 

Mode 5 imports candidate Unity projects, so a normal checkout, Windows Unity
Editor, container, or WSL distribution is not an acceptable trust boundary.
Import, compilation, Linux Player build, execution, input injection, capture,
and report export all run inside a disposable Ubuntu VM.

## Frozen profile

The authoritative environment record is
`profiles/ubuntu2404-unity6000.3.23f1.json`. It currently identifies:

- Ubuntu 24.04.4 x86_64, kernel 6.8.0-138;
- Unity Editor 6000.3.23f1, changeset 09d2ecc7fb28;
- the Linux IL2CPP module and pinned package locks;
- QEMU 11.1 with the WHPX backend;
- Xvfb at 960x540 with Mesa llvmpipe;
- no candidate network and loopback-only controller IPC;
- CPU, memory, disk, process, and host-wall limits;
- the certified base-image, scaffold, and evidence digests.

Do not edit `score_eligible`, digests, or certification state by hand. A new
Unity patch, module, kernel, Mesa version, image, renderer, or package lock is a
new profile and must repeat the certification sequence.

The base and license-state images are intentionally outside Git. They may
contain machine-bound Unity entitlement state and must remain ACL-restricted.
Never commit an image, `.ulf`, browser profile, Unity Hub profile, keyring, or
license log.

## Directory map

```text
eval/infra/unity/
  profiles/       frozen environment contract
  build_image/    Ubuntu bootstrap and Unity installation scripts
  host_runner/    disposable QEMU/WHPX lifecycle and offline export
  guest_runner/   in-VM namespace, cgroup, AppArmor and controller setup
  fixtures/       evaluator-owned blank, protocol, observer and mechanics tests
  evidence/       compact committed certification records and digests
```

The evaluator-facing environment parser and fail-closed policy live in:

```text
eval/evalsys/evalsys/taskgen/unity/unity_environment.py
eval/evalsys/evalsys/taskgen/unity/unity_vm_contract.py
eval/evalsys/evalsys/taskgen/unity/unity_runtime.py
```

The injected runtime and controller are implemented in
`unity_runtime_probe.cs`, `unity_controller.py`, `unity_observer.py`, and
`unity_probe.py` in the same package.

## Prerequisites

The checked-in local host runner expects:

- Windows x86_64 with QEMU 11.1 and WHPX available;
- PowerShell and `tar.exe`;
- WSL Ubuntu only for trusted disk/ISO preparation utilities;
- enough local space for a 64 GiB sparse qcow2 overlay and output disk;
- a verified Ubuntu 24.04 cloud image;
- Unity 6000.3.23f1 Linux Editor and Linux IL2CPP archives;
- an interactive Unity Personal entitlement provisioned before any candidate
  disk is attached.

WSL is tooling support only. It must not import or execute an untrusted Unity
submission and can never produce a score-eligible result.

## Build the reusable image

Run commands from the repository root. The scripts require explicit paths so a
caller cannot accidentally target a checkout, user directory, or host drive.

1. `build_image/Initialize-LocalQemuUnityImage.ps1` verifies the Ubuntu release
   hashes, creates the qcow2 disk and NoCloud seed, and boots the builder VM.
2. `build_image/Install-UnityIntoBuilderVm.ps1` verifies the official Unity
   archives on both host and guest, installs Editor 6000.3.23f1 plus Linux
   IL2CPP, and records package versions.
3. Remove the build-time network adapter and privileged builder access before
   treating the image as an evaluation base.
4. Compare every measured field and digest with the profile. A mismatch is a
   failed preflight, not a reason to update the expected value automatically.

`build_image/guest-install-unity.sh` and `build_image/cloud-init/` are the
guest side of this process.

## Unity Personal licensing

Unity Personal does not support the Enterprise/Industry manual `.alf` to
`.ulf` offline activation workflow. The `Export-UnityLicenseRequest.ps1` and
`Import-UnityLicenseIntoDisposableClone.ps1` helpers remain useful only for
license types that Unity permits to use that workflow.

For the current Personal profile, provision entitlement through Unity Hub in a
separate networked clone with no candidate disk attached. After sign-in:

1. remove Hub, browser, cookie/cache, and keyring session data;
2. flatten the resulting license state into an ACL-restricted image;
3. destroy the networked source overlay;
4. boot a fresh child with `-nic none` and verify only `lo` exists;
5. verify Unity can import and build a trusted blank project without Hub or a
   browser process;
6. never reconnect that image after attaching candidate input.

The license-state image is sensitive infrastructure, not a clean base image.
Revalidation failure is infrastructure inconclusive and must not be attributed
to the candidate.

## Certification sequence

Certification is ordered and fail-closed:

1. Run `host_runner/Invoke-UnityBlankFixtureCertification.ps1` for 20 fresh
   overlays. Every run imports/builds with the Linux Editor, launches the Linux
   Player under Xvfb/llvmpipe, validates a 960x540 PNG, exports logs/results,
   shuts down, and destroys the overlay.
2. Run `host_runner/Invoke-UnitySandboxCertification.ps1`. It validates the VM
   contract, mounts candidate and controller inputs separately and read-only,
   creates a fresh output disk, and exercises protocol, observer, resource,
   secrecy, and mechanics positive/negative fixtures.
3. Confirm the controller, not the candidate, produced the report and artifact
   manifest. Read the output disk only after shutdown.
4. Verify the compact records in `evidence/` and the profile digests.

The sandbox runner places candidate Unity under an unprivileged `unity-runner`
identity and the hidden controller under `gb-controller`. UID permissions,
mount/PID namespaces, Yama, AppArmor, cgroup v2 limits, a host wall deadline,
and `-nic none` enforce the boundary. Candidate input never contains hidden
policies or expected verdicts.

The `behavior_causality` fixture is one generic evaluator calibration project;
it is not a Unity reference answer and is never copied per benchmark game. Its
variants exercise auto-win, ignored input, non-causal enemy disappearance,
false telemetry, witness overfit, and visual-only behavior. Historical `cat-*`
binary/artifact identifiers are retained only so committed certification
digests remain attributable to the runs that produced them.

## Fixed task suites

Task suites and their readiness attestations are delivered in the fixed task packages.
Use `hidden/unity/behavior/calibration_status.json` to inspect readiness.
40 suites are calibrated; `shadow_walker` remains pending.


## Evaluate an Agent submission

On the certified Windows/QEMU host, `bench eval-task --engine auto` delegates a
Mode 5 package to `host_runner/Invoke-UnityCandidateEvaluation.ps1`. Use
`--unity-vm on` to require this path and fail if the infrastructure is missing;
use `--unity-vm off` only for static development diagnostics.

The production runner is game-independent. It creates a fresh overlay, a
read-only candidate ISO containing only `submission/`, a separate read-only
controller ISO containing the task package/hidden suite and evaluator, and a
new output disk. The guest runs Unity Editor and every Player process as
`unity-runner` under AppArmor, while the trusted evaluator retains the hidden
package and authors the report. QEMU has `-nic none`; the host accepts only a
guest whose sole interface is `lo`. After guest shutdown, the host reads the
output disk offline and verifies every artifact against the controller-authored
manifest before deleting all per-run disks. The default host wall deadline is
60 minutes (bounded to at most 120 minutes when explicitly overridden). This
is compatibility headroom for diagnostic routes; score-bearing packages should
compile 3--6 short behavior obligations rather than replay a full playthrough.

```powershell
$PY = 'C:\path\to\python.exe'
$env:PYTHONPATH = (Resolve-Path eval/evalsys).Path
& $PY eval/evalsys/bin/bench eval-task `
  --package D:\runs\cat_defense\port\package `
  --submission D:\runs\cat_defense\port\workspace\submission `
  --out D:\runs\cat_defense\port\evaluation `
  --engine auto --unity-vm on --visual-judge none
```

The output directory must be new. `report.json` is the normal task report;
`artifact-manifest.json` attests its producer and digests; the compressed
artifact bundle contains build, Player, controller and capture evidence.
Candidate failure is still exported as a valid report. Missing VM/image/license,
guest timeout, missing report, or an artifact digest mismatch is infrastructure
failure and never silently becomes an Agent failure.

For infrastructure debugging, call the host runner directly with
`-DiagnosticSmoke`. It performs the same isolated build, one short Player run,
shutdown, offline extraction, and digest verification, but intentionally skips
all counterfactual and hidden runs. Its report is always `inconclusive` and must
never enter a benchmark score.

## Tests and diagnostics

Use the repository Python environment and put `eval/evalsys` on `PYTHONPATH`:

```powershell
$PY = 'C:\path\to\python.exe'
$env:PYTHONPATH = (Resolve-Path eval/evalsys).Path
& $PY -m pytest `
  eval/evalsys/tests/test_unity_environment.py `
  eval/evalsys/tests/test_unity_vm_contract.py `
  eval/evalsys/tests/test_unity_candidate_runner.py `
  eval/evalsys/tests/test_unity_runtime.py `
  eval/evalsys/tests/test_unity_controller.py `
  eval/evalsys/tests/test_unity_observer.py -q
```

Useful failure ownership rules:

- missing image, license, Xvfb, renderer, hypervisor, output channel, or profile
  evidence is infrastructure inconclusive;
- compilation and player failure after a passing environment preflight can be
  candidate-attributed;
- missing or malformed controller observations are protocol/observation
  failures, never inferred gameplay success;
- a timeout is enforced by host wall time; guest monotonic time is diagnostic;
- a capture without a valid PNG and digest is unobservable, not fidelity pass.

When changing the environment, update code, profile, tests and evidence in one
reviewable change. Never infer certification from platform detection or an
environment variable.
