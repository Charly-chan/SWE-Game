# GitHub automation

This directory defines repository checks and the container publishing workflow.

| File | Purpose |
| --- | --- |
| [`workflows/evalsys-tests.yml`](workflows/evalsys-tests.yml) | Run evaluator tests and Linux integration checks in GitHub Actions |
| [`workflows/publish-container-images.yml`](workflows/publish-container-images.yml) | Build, verify, and publish the Godot image with Codex |
| [`PULL_REQUEST_TEMPLATE.md`](PULL_REQUEST_TEMPLATE.md) | Collect a change summary and validation evidence for review |

See the [container guide](../docker/README.md) for image tags and build instructions.
