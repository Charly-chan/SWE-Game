# Experimental navigation harness

This directory contains the time-aware navigation experiment used to study
whether a generic controller can establish playthrough completeness. It is not
part of the four scoring faces and is never loaded by the normal evaluation
pipeline.

Enable it only in controlled research runs with `GB_NAV_PLANNER=1`. A truncated
search must report a budget-limited result: if any cost, air-time, expansion, or
wall-clock cap prunes the search, it may not report `frontier_exhausted`.

The current benchmark scores only reviewed, RT-1-certified routes. The planner
must agree with every gold reference before it can be considered for scoring.

Run only its contract test from `eval/evalsys/`:

```bash
PYTHONPATH=. python3 -m pytest -q tests/test_routes.py::NavPlannerContract
```
