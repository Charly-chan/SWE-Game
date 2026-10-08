# Fixed tasks and reference data

The [SWE-Game dataset](https://huggingface.co/datasets/Charly-chan/SWE-Game)
contains 246 tasks over 41 games and five task types. The release provides 451
fixed package variants: the supported playtest-kit choices and the Mode-1
reference-video switch. Each game also has a reference project and one recording.

## Download a task

The runner downloads the selected task automatically. To inspect a package:

```bash
eval/evalsys/bin/bench gen-task --game wizard_chase --mode brief --out results/task
```

`gen-task` installs existing release files. Choose a repair task with `--case-id`.
Give a coding agent only `visible/`; retain the entire package for evaluation.
The available repair IDs are listed in [`data/task-data.json`](../data/task-data.json).

To download a reference game separately:

```bash
python scripts/fetch_reference_data.py --game kindle_relay --with-videos
python scripts/fetch_reference_data.py --game all --with-videos
```

Public downloads need no account or token. Data preparation supports Linux and
macOS; the runner's engine and sandbox requirements are listed in [quick start](quickstart.md).

## Version and integrity

[`data/task-data.json`](../data/task-data.json) pins an immutable dataset commit.
It records each bundle and video's SHA-256 and byte size. Each bundle contains
an index and content-addressed files shared by its task variants. The installer
copies the selected fixed files after checking their hashes; it does not change
the task requirements. Videos download separately when the chosen variant needs them.

## Storage and recovery

Downloads are cached in `~/.cache/swe-game/<dataset-commit>/`. Set
`SWE_GAME_CACHE=/path/to/cache` to use another disk. The manifest records exact
compressed bundle and video sizes. Installed task variants can use substantially
more space than the shared download cache.

Interrupted downloads resume when the server supports byte ranges. Installation
rejects archive links, duplicate paths, directory traversal, and mismatched hashes.
Existing nonempty task directories and existing reference projects are preserved.
Move an existing directory aside to install a pristine copy.

## Attribution

Reference projects retain their applicable component license and credit files.
Attribution records are indexed in [third-party notices](../THIRD_PARTY_NOTICES.md).
Their existing terms remain applicable to the distributed materials.
