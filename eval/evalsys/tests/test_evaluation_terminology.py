
from pathlib import Path

import pytest

from evalsys.report.cards import Card, ChannelRecord
from evalsys.report.render_md import card_markdown, main_table
from evalsys.report.terminology import OBJECTIVE_EVALUATION, PERCEPTUAL_ASSESSMENT, display_terms
from evalsys.verdict import score_items, passed
from evalsys.weights import CHANNELS

ROOT = Path(__file__).resolve().parents[3]


@pytest.mark.parametrize("name", [
    "README.md", "docs/running.md", "docs/evaluation.md",
    "docs/reference/OUTPUT_CONTRACT.md",
])
def test_four_entry_documents_use_the_same_paper_terms(name):
    text = (ROOT / name).read_text(encoding="utf-8")
    assert OBJECTIVE_EVALUATION in text and PERCEPTUAL_ASSESSMENT in text
    if name != "README.md":
        assert "客观行为评测" in text and "感知质量评审" in text
        assert "`ocard`" in text and "`scard`" in text
    assert "O-card" not in text and "S-card" not in text


def test_display_mapping_leaves_wire_identifiers_untouched():
    identifiers = ("ocard scard o_card s_card scorecard calibrated_total calibrated_surface "
                   "O1 O9 S1 S4 S4_replay objective_and_calibrated_perceptual")
    assert display_terms(identifiers) == identifiers
    assert display_terms("O-card / S-card") == f"{OBJECTIVE_EVALUATION} / {PERCEPTUAL_ASSESSMENT}"


def test_markdown_uses_new_labels_but_card_serialization_is_unchanged():
    interval = score_items([passed("fixture")])
    card = Card(submission="fixture", task_id="fixture", tier="D3", ocard=interval,
                scard=interval, scard_state="calibrated",
                channels=[ChannelRecord(key, interval, value.weight) for key, value in CHANNELS.items()],
                notes=["S-card calibrated; O-card measured"])
    before = card.to_dict()
    before.pop("generated_at")
    markdown = card_markdown(card)
    ranked = main_table([card], spearman=1.0)
    after = card.to_dict()
    after.pop("generated_at")
    assert after == before
    assert "ocard" in after and "scard" in after
    assert "o_card" not in after and "s_card" not in after
    assert after["notes"] == ["S-card calibrated; O-card measured"]
    assert OBJECTIVE_EVALUATION in markdown and PERCEPTUAL_ASSESSMENT in markdown
    assert OBJECTIVE_EVALUATION in ranked and PERCEPTUAL_ASSESSMENT in ranked
    assert "O-card" not in markdown and "S-card" not in markdown
