


from __future__ import annotations

from typing import Sequence

from ..verdict import LOW_COVERAGE_THRESHOLD
from ..weights import CHANNELS, REGISTRY_VERSION, RETIRED_CHANNELS
from .cards import Card, rank
from .terminology import OBJECTIVE_EVALUATION, PERCEPTUAL_ASSESSMENT, display_terms


def _iv(lo: float, hi: float) -> str:
    return f"{lo:.3f}" if abs(hi - lo) < 1e-9 else f"{lo:.3f} [{lo:.3f}, {hi:.3f}]"


def channel_table(card: Card) -> str:
    rows = [
        "| channel | what it reads | w | score | coverage | mode | note |",
        "|---|---|---:|---|---:|:---:|---|",
    ]
    for rec in sorted(card.channels, key=lambda r: r.channel):
        ch = CHANNELS.get(rec.channel)
        if ch:
            name = ch.name
        elif rec.channel in RETIRED_CHANNELS:


            name = f"{rec.channel} (retired, not scored)"
        else:
            name = rec.channel
        if not rec.measured:
            score, cov = "not measured", "-"
        else:
            score = _iv(rec.interval.lo, rec.interval.hi)
            cov = f"{rec.interval.coverage:.2f}"
            if rec.interval.low_coverage:
                cov += " !"
        rows.append(
            f"| {rec.channel} | {name} | {rec.weight:.3f} | {score} | {cov} | "
            f"{rec.mode_used or '-'} | {rec.note} |"
        )
    return display_terms("\n".join(rows))


def card_markdown(card: Card) -> str:
    out: list[str] = []
    out.append(f"# {card.submission} — {card.task_id} @ {card.tier}/{card.modality}")
    out.append("")
    out.append(f"Weight registry `{REGISTRY_VERSION}`.")
    out.append("")

    out.append("## Score")
    out.append("")
    out.append("| | value |")
    out.append("|---|---|")
    out.append(f"| ranked score (`score_lo`) | **{card.combined_lo:.3f}** |")
    out.append(f"| interval | [{card.combined_lo:.3f}, {card.combined_hi:.3f}] |")
    out.append(f"| {OBJECTIVE_EVALUATION} (objective only) | {card.objective_lo:.3f} |")
    which = (
        "restricted ceiling, over the channels that reported"
        if card.is_partial and card.restricted_ceiling
        else f"ceiling({card.tier},{card.modality})"
    )
    out.append(
        f"| normalised (`/ {which} = "
        f"{card.normalising_ceiling:.3f}`) | {card.normalised_lo:.3f} |"
    )
    out.append(f"| coverage | {card.coverage:.2f} |")
    out.append(
        f"| share of registered weight actually measured | "
        f"{card.measured_weight_share:.2f} |"
    )
    if card.flags:
        out.append(f"| flags | {', '.join(card.flags)} |")
    out.append("")

    eligible, refusal = card.main_table_eligible
    if not eligible:
        out.append(f"> **Not eligible for a cross-model table.** {refusal}")
        out.append("")

    if card.combined_hi - card.combined_lo > 1e-9:
        out.append(
            f"The interval is {card.combined_hi - card.combined_lo:.3f} wide. That width "
            "is the part of the submission we did not manage to measure, and it is "
            "counted against the score rather than around it: ranking uses the low "
            "end, so a check that was skipped costs exactly as much as a check that "
            "failed."
        )
        out.append("")

    out.append(f"## {OBJECTIVE_EVALUATION}")
    out.append("")
    out.append(channel_table(card))
    out.append("")

    out.append("## Reported separately, never folded into the score")
    out.append("")
    out.append("| reading | value | why it is not in the score |")
    out.append("|---|---|---|")
    out.append(
        f"| `shortcut_detected` | {card.shortcut_detected} | A categorical fact, not a "
        "magnitude. Any number of points docked for it would be the wrong number. |"
    )
    out.append(
        f"| `unmeasurable_channels` | {', '.join(card.unmeasurable_channels) or 'none'} | "
        "Distinguishes 'did not do it' from 'we could not read it'. |"
    )
    out.append(
        f"| `inconclusive_rate` | {card.inconclusive_rate:.3f} | Our failures, not the "
        "submission's. Out of the denominator, but public. |"
    )
    out.append(
        f"| `unattributable_error_rate` | {card.unattributable_error_rate:.3f} | Errors "
        "we could not blame on either side. Flagged for human review. |"
    )
    uhr = (
        "n/a" if card.unobservable_hit_rate is None
        else f"{card.unobservable_hit_rate:.3f}"
    )
    out.append(
        f"| `unobservable_hit_rate` | {uhr} | Pass rate on items this tier cannot "
        "convey. Measures the design prior — how much it guessed right with no "
        "information — which separates reproducing from pattern-matching. |"
    )
    out.append(
        f"| `exempt_count` / `exempt_weight` | {card.exempt_count} / "
        f"{card.exempt_weight:.3f} | Genre exemptions that stay in the denominator "
        "(lo=0, hi=full). Interval width records them; they must not vanish. |"
    )
    out.append("")

    if card.exempt_items:
        out.append("## Genre exemptions")
        out.append("")
        for e in card.exempt_items:
            ch = e.get("channel") or e.get("family") or "?"
            out.append(
                f"- `{e.get('id')}` ({ch}, w={float(e.get('weight', 0)):.3f}): "
                f"{e.get('detail', '')[:160]}"
            )
        out.append("")

    out.extend([f"## {PERCEPTUAL_ASSESSMENT}", "",
                f"Assessment state: `{card.scard_state}`.", ""])
    if card.scard_state != "calibrated":
        out.append(
            f"The subjective card is `{card.scard_state}` and contributes nothing to "
            "the total. An uncalibrated judge in a score is a number with no shown "
            "relationship to anything it claims to measure."
        )
        out.append("")

    if card.notes:
        out.append("## Notes")
        out.append("")
        out.extend(f"- {n}" for n in card.notes)
        out.append("")
    return display_terms("\n".join(out))


def main_table(cards: Sequence[Card], *, spearman: float | None = None) -> str:


    ranked = rank(cards)
    obj_order = sorted(cards, key=lambda c: -c.objective_lo)
    obj_rank = {c.submission: i + 1 for i, c in enumerate(obj_order)}

    out = [
        f"| # | submission | score_lo | interval | {OBJECTIVE_EVALUATION} | obj. rank | "
        "normalised | coverage | flags |",
        "|---:|---|---:|---|---:|---:|---:|---:|---|",
    ]
    for i, c in enumerate(ranked, 1):
        moved = "" if obj_rank[c.submission] == i else f" ({obj_rank[c.submission]})"
        out.append(
            f"| {i} | {c.submission} | **{c.combined_lo:.3f}** | "
            f"[{c.combined_lo:.3f}, {c.combined_hi:.3f}] | {c.objective_lo:.3f} | "
            f"{obj_rank[c.submission]}{moved} | {c.normalised_lo:.3f} | "
            f"{c.coverage:.2f} | {', '.join(c.flags)} |"
        )

    out.append("")
    if spearman is not None:
        out.append(f"Spearman rho between the combined and objective-only orderings: {spearman:.3f}.")
        moves = [c.submission for i, c in enumerate(ranked, 1) if obj_rank[c.submission] != i]
        if moves:
            out.append(
                "The subjective card changed the position of: " + ", ".join(moves) + "."
            )
        else:
            out.append("The subjective card changed no positions.")
    out.append("")
    out.append(
        f"Ranking is by `score_lo`. Rows flagged `low-coverage` have coverage below "
        f"{LOW_COVERAGE_THRESHOLD}, meaning enough of the measurement is missing that "
        "the score should not be read as a point estimate. `normalised` divides by the "
        "reachable ceiling for this tier and modality and is the only column that may "
        "be compared across tiers."
    )
    return display_terms("\n".join(out))
