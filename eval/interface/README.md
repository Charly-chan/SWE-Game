# Submission interfaces

Use [the Godot contract](SUBMISSION_INTERFACE.md) or
[the Unity contract](UNITY_PORT_CONTRACT.md) for candidate submissions.

- `contract.v2.json`: Interface v2 contract.
- `gb_interface.schema.json`: Godot interface schema.
- `examples/`: supported Godot interface examples.
- `unity_gb_interface.example.json`: Unity interface example.
- `unity_observation_contract.json`: Unity observable fields and semantics.

The evaluator loads these contracts through `evalsys/interface/`.
