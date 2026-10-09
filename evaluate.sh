#!/usr/bin/env bash
# Evaluate a generated task package and its corresponding saved submission.
# Runs the shared evaluator and writes scores and evidence under --out.
# See docs/quickstart.md for engine, Community Docker, and visual-judge options.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
export GB_ROOT="$HERE"
# shellcheck source=eval/tools/gb_env.sh
. "$HERE/eval/tools/gb_env.sh"

usage() {
  cat <<'EOF'
usage: ./evaluate.sh <package_dir> <submission_dir> [--out DIR]
                     [--visual-judge vlm|local|none] [--score-against-source]
                     [--registry VERSION]
       ./evaluate.sh --rescore <report.json> [rescore.py options]
       ./evaluate.sh --judge-visuals <report.json> --out DIR [--record-missing]
                     [--registry VERSION] [--package DIR] [--submission DIR]

The normal form runs `bench eval-task --engine auto` and writes report.json and
report.md. Visual judging defaults to none. Use --visual-judge vlm for paid API
calls (requires a configured key), or local for image
diagnostics without a VLM. GB_VISUAL_JUDGE overrides the default.

Evidence domains: Objective Behavioral Evaluation / Perceptual Quality Assessment.
Modes 1-3 default to 2026-09-19.mode1-vlm1 / mode2-vlm1 / mode3-vlm1:
85 objective points plus 15 game-specific VLM points. The judge sees evaluator
recordings, the task's GT reference frames and provided asset examples. Item
scores are continuous 0-1 with caps; q^3 is diagnostic only. Missing VLM readings
leave an objective contribution and a null composite. Mode 4 defaults to
2026-09-15.mode4-redesign1; Mode 5 uses the fixed 2026-10.mode5-evidence-five-visual1 registry; older Mode 5 registries are rejected.
Historical registries remain available with --registry.
--score-against-source turns on Objective Behavioral Evaluation for a package
that was generated without it; full Godot headline scoring needs those objective
channels too. It changes which channels are measured and can move
weighted_total/coverage, so it is never implied by --visual-judge.
The rescore form recomputes only the scorecard from stored verdicts;
it does not rerun Godot or call a VLM.
The --judge-visuals form judges saved recordings and preserves a modeN-vlm1
report's registry. For historical reports, choose the target mode's registry
explicitly to upgrade its visual protocol. --record-missing records a retained
Godot project when needed; neither form reruns the coding agent. Visual scores
are not independently calibrated.
EOF
}

if [ "${1:-}" = --help ] || [ "${1:-}" = -h ]; then
  usage
  exit 0
fi

gb_require_venv

if [ "${1:-}" = --rescore ]; then
  [ $# -ge 2 ] || { usage >&2; exit 2; }
  shift
  export PYTHONPATH="$GB_EVALSYS${PYTHONPATH:+:$PYTHONPATH}"
  exec "$GB_PYTHON" "$HERE/eval/tools/rescore.py" "$@"
fi

if [ "${1:-}" = --judge-visuals ]; then
  [ $# -ge 2 ] || { usage >&2; exit 2; }
  shift
  gb_python_env
  gb_load_api_env || true
  exec "$GB_PYTHON" "$HERE/eval/tools/judge_task_visuals.py" "$@"
fi

[ $# -ge 2 ] || { usage >&2; exit 2; }
PACKAGE="$1"; SUBMISSION="$2"; shift 2
OUT=""
VISUAL_JUDGE="${GB_VISUAL_JUDGE:-none}"
SCORE_AGAINST_SOURCE=0
SCORE_REGISTRY=""
while [ $# -gt 0 ]; do
  case "$1" in
    --out) [ $# -ge 2 ] || gb_die "--out needs a directory"; OUT="$2"; shift ;;
    --visual-judge) [ $# -ge 2 ] || gb_die "--visual-judge needs vlm, local, or none"; VISUAL_JUDGE="$2"; shift ;;
    --score-against-source) SCORE_AGAINST_SOURCE=1 ;;
    --registry) [ $# -ge 2 ] || gb_die "--registry needs a version"; SCORE_REGISTRY="$2"; shift ;;
    -h|--help) usage; exit 0 ;;
    *) gb_die "unknown option $1 (try --help)" ;;
  esac
  shift
done
case "$VISUAL_JUDGE" in
  vlm|local|none) ;;
  *) gb_die "--visual-judge must be vlm, local, or none" ;;
esac

[ -d "$PACKAGE" ] || gb_die "package directory does not exist: $PACKAGE"
[ -d "$SUBMISSION" ] || gb_die "submission directory does not exist: $SUBMISSION"
PACKAGE="$(readlink -f "$PACKAGE")"
SUBMISSION="$(readlink -f "$SUBMISSION")"
if [ -z "$OUT" ]; then
  OUT="$GB_RUNS_ROOT/evaluation_$(basename "$PACKAGE")_$(date -u +%Y%m%dT%H%M%SZ)"
fi
OUT="$(readlink -m "$OUT")"
[ ! -e "$OUT" ] || gb_die "output already exists: $OUT"

gb_python_env
if [ "$VISUAL_JUDGE" = vlm ]; then
  # Load the same external configuration used by scripts/run_coding.sh. An already
  # exported key also works, so the config file itself is not required.
  gb_load_api_env || true
  "$GB_PYTHON" - "$SCORE_REGISTRY" "$PACKAGE" <<'PY'
import json
import os
import sys
from pathlib import Path
from evalsys.scard.judge import vlm_scard_judge_from_env
from evalsys.taskgen.scorecard import VISUAL_REGISTRY_VERSION, default_registry_for_mode, registry_policy

try:
    manifest = json.loads((Path(sys.argv[2]) / "manifest.json").read_text())
    mode = manifest["mode"]
    selected = sys.argv[1] or default_registry_for_mode(mode)
    policy = registry_policy(selected, mode=mode)
    if policy.mode5_capability_only:
        raise ValueError("Mode 5 Community evaluation does not use a VLM judge")
    if policy.task_visual and (policy.mode1_redesign or policy.progressive_redesign_mode):
        from evalsys.scard.rubric_judge import rubric_judge_from_env
        rubric_judge_from_env().validate_configuration()
    elif selected == VISUAL_REGISTRY_VERSION:
        if not (os.environ.get("GAMECRAFT_BENCH_JUDGE_OPENAI_API_KEY") or os.environ.get("OPENAI_API_KEY")):
            raise ValueError("visual1 needs GAMECRAFT_BENCH_JUDGE_OPENAI_API_KEY or OPENAI_API_KEY")
    elif not vlm_scard_judge_from_env().available:
        raise ValueError("VLM evaluation needs a key in the selected GAMEBENCH_VLM_KEY_ENV or provider key variable")
except (OSError, KeyError, ValueError, RuntimeError) as exc:
    print(f"error: {exc}\nConfigure GB_API_ENV (see docs/quickstart.md), or run without a VLM:\n"
          "  ./evaluate.sh <package_dir> <submission_dir> --visual-judge none", file=sys.stderr)
    sys.exit(2)
PY
fi
gb_note "visual_judge=$VISUAL_JUDGE score_against_source=$SCORE_AGAINST_SOURCE"
VISUAL_ARGS=(--visual-judge "$VISUAL_JUDGE")
if [ "$SCORE_AGAINST_SOURCE" = 1 ]; then
  VISUAL_ARGS+=(--score-against-source)
fi
if [ -n "$SCORE_REGISTRY" ]; then
  VISUAL_ARGS+=(--registry "$SCORE_REGISTRY")
fi
mkdir -p "$(dirname "$OUT")"
set +e
"$GB_PYTHON" "$GB_BENCH" eval-task \
  --package "$PACKAGE" --submission "$SUBMISSION" --out "$OUT" --engine auto \
  "${VISUAL_ARGS[@]}"
BENCH_RC=$?
set -e

REPORT="$OUT/report.json"
[ -f "$REPORT" ] || gb_die "evaluation exited $BENCH_RC without writing $REPORT"
"$GB_PYTHON" - "$REPORT" <<'PY'
import json
import sys
from pathlib import Path
from evalsys.report.terminology import OBJECTIVE_EVALUATION, PERCEPTUAL_ASSESSMENT, display_terms

path = Path(sys.argv[1])
data = json.loads(path.read_text(encoding="utf-8"))
card = data.get("scorecard") or {}
(path.parent / "card.json").write_text(
    json.dumps(card, indent=2, ensure_ascii=False) + "\n",
    encoding="utf-8",
)
weighted = card.get("weighted_total") or {}
strict = card.get("strict") or {}
score = weighted.get("score")
print(f"Evidence domains: {OBJECTIVE_EVALUATION} / {PERCEPTUAL_ASSESSMENT}")
print(f"weighted_total={'not_measured' if score is None else f'{float(score):.3f}'}")
print(f"headline_ceiling={weighted.get('headline_ceiling', 'not_reported')}")
objective = card.get("objective_total")
if objective is not None:
    print(f"objective_total={objective.get('score')}")
    print(f"objective_ceiling={objective.get('headline_ceiling')}")
    print(f"assessment_status={card.get('assessment_status')}")
    print("objective_scope=objective contribution, not a complete composite score")
print("visual_protocol=" + display_terms(str(card.get("visual_protocol") or "legacy S-card")))
visual = next((i for i in data.get("items", []) if i.get("id") == "task_visual"), None)
if visual:
    visual_credit = visual.get("credit") if visual.get("verdict") == "passed" else None
    print("task_visual=" + ("not_measured" if visual_credit is None else f"{100 * visual_credit:.3f}"))
    print("visual_validation=not_independently_validated")
resolved = data.get("resolved")
print("resolved=" + ("not_measured" if resolved is None else ("yes" if resolved else "no")))
failed = strict.get("failed_required_items") or []
print("failing_items=" + (", ".join(map(str, failed)) if failed else "none"))
print(f"ranking_eligible={str(bool(card.get('ranking_eligible'))).lower()}")
print("ranking_note=" + display_terms(str(card.get("ranking_note") or "not reported")))
reading = data.get("replay_reading") or {}
print("replay_visual_status=" + str(reading.get("status") or "not_measured"))
if reading.get("detail"):
    print("replay_visual_note=" + display_terms(str(reading["detail"])))
demos = data.get("demonstrations") or {}
if demos:
    expected = len(demos.get("expected") or [])
    action_expected = len(demos.get("action_required", demos.get("expected")) or [])
    print(f"demonstration_feature_coverage={len(demos.get('observed') or [])}/{expected}")
    print(f"demonstration_action_caused_coverage={len(demos.get('action_caused') or [])}/{action_expected}")
    print("demonstration_measurement_status=" + ("measured" if demos.get("measured") else "incomplete"))
    print("demonstration_missing_features=" + (", ".join(demos.get("missing") or []) or "none"))
    for segment in demos.get("segments") or []:
        print(f"demonstration[{segment['id']}].feature_coverage={len(segment.get('observed') or [])}/{expected}")
        print(f"demonstration[{segment['id']}].action_caused_coverage={len(segment.get('action_caused') or [])}/{action_expected}")
if not reading and (demos or data.get("demonstration_visuals")):
    print("replay_visual_note=no whole-run reading; independent feature-demo visual results are listed separately")
for segment in data.get("demonstration_visuals") or []:
    segment_id = segment.get("id") or "unassigned"
    visual = segment.get("reading") or {}
    print(f"demonstration[{segment_id}].visual_status={visual.get('status') or 'not_measured'}")
    if visual.get("detail"):
        print(f"demonstration[{segment_id}].visual_note={display_terms(str(visual['detail']))}")
print(f"report_json={path}")
markdown = path.with_suffix(".md")
print(f"report_md={markdown if markdown.is_file() else 'not written'}")
print(f"card_json={path.parent / 'card.json'}")
PY

# A resolved=no submission is a valid measured result; preserve bench's status so
# automation can still distinguish it from resolved=yes.
exit "$BENCH_RC"
