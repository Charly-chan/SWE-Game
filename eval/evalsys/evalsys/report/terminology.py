

OBJECTIVE_EVALUATION = "Objective Behavioral Evaluation"
PERCEPTUAL_ASSESSMENT = "Perceptual Quality Assessment"


def display_terms(text: str) -> str:

    for old, new in (
        ("O-card", OBJECTIVE_EVALUATION),
        ("S-card", PERCEPTUAL_ASSESSMENT),
        ("subjective card", PERCEPTUAL_ASSESSMENT),
        ("O 卡", "客观行为评测"),
        ("S 卡", "感知质量评审"),
        ("O卡", "客观行为评测"),
        ("S卡", "感知质量评审"),
    ):
        text = text.replace(old, new)
    return text
