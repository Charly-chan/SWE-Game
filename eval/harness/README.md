# Godot runtime instruments

The evaluator injects these scripts into temporary projects to observe state,
replay inputs and capture evidence. Candidate submissions must not include them.

- `gb_probe.gd`, `gb_truth_driver.gd`: objective state observations.
- `gb_route_driver.gd`: fixed input route replay.
- `gb_capture_probe.gd`: visual evidence capture.

Evaluation reports record the SHA-256 of the runtime instruments.

## Recorded instrument identities

`released-identities.json` maps recorded SHA-256 identifiers to the corresponding
published source in `versions/`. These copies remove comments while preserving
executable statements and line positions. The recorded hashes still identify
the original evidence; each published copy has its own checksum. The reader
accepts only mappings and matching source bytes committed in the checkout.
