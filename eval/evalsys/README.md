# Python evaluator

`bin/bench` provides fixed task downloads, agent execution, and submission evaluation.

```bash
bin/bench --help
bin/bench selfcheck
python -m pytest tests -q
```

Run tests with `PYTHONPATH=.` from this directory after installing
`requirements-harbor.txt`. Engine tests additionally need Godot 4.5.1; Unity
runtime checks require the licensed environment described in
[`Mode 5 Community Docker`](../../docs/reference/MODE5_RELEASE.md).

The historical module name `evalsys.taskgen` is retained for compatibility. Its
public `generate_task` function downloads fixed release files.
