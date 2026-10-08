# Godot-to-Unity porting

Porting tasks provide a Godot project, a design document, assets, a reference
video, and a Unity starting project. Implement the required gameplay in Unity
6000.3.23f1 and preserve the supplied SDK and package versions.

```bash
eval/evalsys/bin/bench gen-task --game cat_defense --mode port --out results/port-task
./evaluate.sh results/port-task path/to/submission --out results/port-evaluation
```

The submitted Unity interface declares actions, semantic roles, numeric state,
levels, and endings. The evaluator builds the project, executes input and control
runs, captures observations, and computes behavioral and visual scores. Source
layout and engine-specific node hierarchies are not scoring targets.

- [Submission contract](../../eval/interface/UNITY_PORT_CONTRACT.md)
- [Evaluation specification](MODE5_EVALUATION.md)
- [Runtime guide](MODE5_RUNBOOK.md)
- [Unity environment](../../eval/infra/unity/README.md)
- [Domain scoring](MODE5_V2_MDVA_DOMAIN.md)

A complete composite score requires all required objective and VLM evidence.
Missing infrastructure is reported as unmeasured evidence; it does not establish
a candidate failure.
