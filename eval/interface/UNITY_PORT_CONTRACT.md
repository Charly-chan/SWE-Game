# Unity GameBenchmark port contract v3

This contract is the model-visible interface for Mode 5 Godot-to-Unity ports.
It fixes observable addresses, not the candidate's internal architecture.
The first published track is frozen to Unity Editor `6000.3.23f1`; a different
editor version is a different execution environment and fails the static M5
contract rather than becoming an evaluator-side version lottery.

## Submission layout

Start from the supplied `target_unity/` project. The evaluator-owned files in
`Assets/GameBenchmarkSDK/`, `Packages/manifest.json`, `Packages/packages-lock.json`,
and `ProjectSettings/ProjectVersion.txt` are SHA-256 locked. Candidate gameplay
belongs under `Assets/Game/`; fill in `Assets/GameBenchmark/gb_interface.json`.
The official Input System package tarball is included as a locked local
dependency so the certified VM never needs registry access.
The evaluator enables the built-in `com.unity.modules.audio` module in its
private build scratch, so candidate gameplay may use standard `AudioClip` and
`AudioSource` APIs without changing the locked package files.

```text
submission/
  Assets/
    GameBenchmark/gb_interface.json
  Packages/
  ProjectSettings/ProjectVersion.txt
  ops.json
  BUILD.md
```

`gb_interface.json` follows `unity_gb_interface.example.json`. Schema v3 names
the editor, entry scene, level scenes, supported and required action channels,
quantized analog axes,
semantic roles, numeric slots, outcome capabilities, and the published scaffold
profile/digest. Scene addresses are project-relative `Assets/**/*.unity` paths.
The package generator fills this manifest from the evaluator-owned task contract;
the candidate must preserve the declared roles, numeric slots, capabilities and
actions rather than using the file to define a new scoring interface.
`entry_scene` may name either a declared gameplay level or a separate bootstrap
scene. A separate bootstrap scene is built and launched first but is not itself
subject to the per-level `GBEntity(role="gb_player")` requirement.
The evaluator discovers `GBEntity` markers and consumes declared `GBTelemetry`;
v3 does not expose hierarchy paths or component/member reflection addresses.
The v1/v2 parsers remain available only as migration adapters.

`unity_observation_contract.json` is the evaluator-owned observation contract.
It is copied into every published Unity package and is not candidate-authored.
The injected probe resolves the complete subtree below each active `GBEntity`:
enabled Collider/Collider2D components are preferred; enabled Renderer bounds
are the deterministic fallback for games that implement their own logical
collision model. Marker roots therefore need not carry a collider or renderer
themselves, but every entity used by a scored `overlap`, `contact`, or
`standing_on` predicate must expose one of those geometry sources in its marker
subtree. The probe computes overlap/contact and stable entity identity; a
candidate boolean or candidate score is never accepted as the observation.

Per-entity boolean, enum-like text and finite numeric state use the locked
`GBObservableState` component on or below the corresponding `GBEntity`. Gameplay
code may update it through `SetBoolean`, `SetText`, and `SetNumeric`; candidates
must not invent a reflection manifest or replacement state interface. The probe
serializes these fields into the stable device entry. A state field is a
standardized candidate signal, not truth by declaration: score-bearing use must
be preregistered and corroborated by evaluator-observed geometry, lifecycle,
input effects, or downstream consequences.

The fixed evaluator-owned InputActions asset defines eight canonical actions:

- `gb_left`, `gb_right`, `gb_up`, `gb_down`
- `gb_jump`, `gb_action`, `gb_attack`, `gb_dash`

The asset makes all eight actions injectable. The compiler adds the task's
source-derived extended actions and analog axes to the frozen per-game manifest
and operation table; candidates implement those channels in an InputActions
asset under mutable `Assets/Game/**`. Mode 5 does not invent a new
mechanic merely to make every action consequential. `gb_pause` and `gb_reset`
belong to the Godot corpus contract and are not Unity Mode-5 canonical actions.
Extended action ids use 2–32 lowercase identifier characters and cannot use the
reserved `gb_` prefix. An `analog_axes` entry freezes `id`, `why`, `min`, `max`
and `steps`; the evaluator quantizes submitted values to that grid. At most 16
extended actions and four axes are accepted. The runtime can dispatch chords
and axis state and subjects those channels to the same null/mash controls.

Every declared level must expose at least one active `GBEntity` with role
`gb_player` at runtime. Outcomes are emitted through `GBOutcome`; v2 does not
require distinct ending scenes. Object roles form an open, syntactically
checked namespace: a role is `gb_` followed by 1–32 lowercase identifier
characters (for example `gb_alert_cone`). Numeric slot names are 1–32 lowercase
identifier characters (for example `stealth_meter`) and are emitted through
`GBTelemetry`. In both cases,
the first identifier character must be a letter and the remainder may also use
digits or underscores. These task-specific marker roles and numeric signals are
resolved by the runtime observer, but only exact identifiers required by
the evaluator-owned audited rubric can satisfy a scored obligation. Merely
declaring more roles or slots earns no credit. The current rubric catalog uses
the standard object vocabulary and the numeric slots `progress`, `health`,
`timer`, and `score`; other valid identifiers are therefore diagnostic in the
current track. Unknown top-level manifest fields are ignored and non-scoring.
The manifest is package-seeded metadata, not a second scoring interface: task
roles, stable selectors, numeric slots, observation capabilities, and scored
predicates are owned by the evaluator's task contract.

Every `required_actions` entry must be in `supported_actions`; any action or
axis absent from the compiler-generated per-game table is rejected. Candidate
gameplay must bind canonical `Player/gb_*` actions and implement the declared
task-specific channels, without replacing the locked SDK asset.

The evaluator may inject a probe assembly that resolves these candidate-owned
addresses, sends canonical operations and records state, pixels and audio. The
submission does not provide its own score, certificate, route result or visual
evidence.

`ops.json` must use the evaluator-published `op_table.json`, must reach the real
success ending through player-caused state transitions, and must not depend on
a bundled Godot executable or project. `BUILD.md` records the Unity editor
version, Linux batch-build command and output location; it is a reproducibility
recipe, not executable evaluator authority.

## Current implementation boundary

`bench validate-unity PROJECT` validates the project layout and v1/v2/v3 manifest.
`bench eval-task --mode port` additionally validates `ops.json`, `BUILD.md`,
task-specific obligations and forbidden evaluator/Godot bundles, then uses an
evaluator-owned Editor method for the Linux batch build. It never executes the
candidate recipe. That build injects a generic probe; the evaluator then cold-
starts the submitted witness, a matched-horizon null intervention and hidden
positive routes. The observer writes raw scene/entity/numeric/input/outcome/error samples
and evaluator-owned PNGs only; hidden targets and pass/fail decisions stay in
Python. The evaluator can encode a sampled MP4 and run the existing frame-based
local/VLM S-card. It can also extract native reference-video frames and run a
separately reported GT-conditioned paired-frame fidelity judge. Static/build
PASS alone is not a resolved Mode-5 result; hidden behavior calibration remains
a separate fail-closed publication gate.
