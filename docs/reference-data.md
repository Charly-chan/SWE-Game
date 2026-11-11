# Fixed tasks and reference data

The paper's benchmark contains 247 tasks over 41 games and five task types,
including 83 Bug Repair cases. The current downloadable manifest contains 246
tasks and 82 repair cases; the remaining paper case is not yet represented in
the manifest. See [version information](releasing.md#published-results) before
comparing a downloaded repair run with the paper. The release provides 451
fixed package variants: the supported playtest-kit choices and the Mode-1
reference-video switch, plus one recording per game.

## Download a task

Task archives and recordings are supplied separately from this code snapshot.
Browse the files on the [anonymous dataset page](https://anonymous-hf.com/a/uvnp85fetrne/).
The runner downloads from its anonymous file endpoint by default. No login or
access token is required. Set `SWE_GAME_DATA_BASE_URL` to override the mirror.
An override must be a direct download root serving the paths in
[`data/task-data.json`](../data/task-data.json). The runner verifies each
download against the recorded size and SHA-256. To inspect a package:

```bash
eval/evalsys/bin/bench gen-task --game wizard_chase --mode brief --out results/task
```

`gen-task` installs existing release files. For video-conditioned construction and
porting tasks, it then freezes the evaluator’s current visual rubric and response
contract under `hidden/vlm/`. Record the evaluator commit and rubric version with
the dataset revision. Choose a repair task with `--case-id`.
Give a coding agent only `visible/`; retain the entire package for evaluation.
The available repair IDs are listed in [`data/task-data.json`](../data/task-data.json).

The review data contains each task's `visible/` and `hidden/` materials,
including the faulty project for Bug Repair and the source project and target
scaffold for Cross-Engine Porting. The independent reference project tree is
omitted. To prefetch recordings separately:

```bash
python scripts/fetch_reference_data.py --game kindle_relay --videos-only
python scripts/fetch_reference_data.py --game all --videos-only
```

Data preparation supports Linux and
macOS; the runner's engine and sandbox requirements are listed in [quick start](quickstart.md).

## Version and integrity

[`data/task-data.json`](../data/task-data.json) identifies a fixed set of artifacts by content.
It records each bundle and video's SHA-256 and byte size. Each bundle contains
an index and content-addressed files shared by its task variants. The installer
copies the selected fixed files after checking their hashes; it does not change
the task requirements. Videos download separately when the chosen variant needs them.

## Storage and recovery

Downloads are cached in `~/.cache/swe-game/<dataset-revision>/`. Set
`SWE_GAME_CACHE=/path/to/cache` to use another disk. The manifest records exact
compressed bundle and video sizes. Installed task variants can use substantially
more space than the shared download cache.

Interrupted downloads resume when the server supports byte ranges. Installation
rejects archive links, duplicate paths, directory traversal, and mismatched hashes.
Existing nonempty task directories are preserved.
Move an existing directory aside to install a pristine copy.

## Attribution

Task materials retain their applicable component license and credit files.
Attribution records are indexed in [third-party notices](../THIRD_PARTY_NOTICES.md).
Their existing terms remain applicable to the distributed materials.
