"""Small paired-effect statistics shared by ReviewX experiments and views."""

from __future__ import annotations

from math import comb
from typing import Any, Sequence


def exact_mcnemar_p_value(corrected: int, regressed: int) -> float:
    """Return the two-sided exact McNemar p-value for discordant pairs."""

    if corrected < 0 or regressed < 0:
        raise ValueError("Discordant-pair counts must be non-negative.")
    discordant = corrected + regressed
    if discordant == 0:
        return 1.0
    lower_tail = sum(
        comb(discordant, index) for index in range(min(corrected, regressed) + 1)
    ) / (2**discordant)
    return min(1.0, 2.0 * lower_tail)


def interval_effect_status(ci95_low: float, ci95_high: float) -> str:
    """Classify an improvement-oriented confidence interval without overclaiming."""

    if ci95_low > 0:
        return "significant_improvement"
    if ci95_high < 0:
        return "significant_regression"
    return "inconclusive"


def paired_transition_audit(
    labels: Sequence[int | float],
    before_predictions: Sequence[int | bool],
    after_predictions: Sequence[int | bool],
) -> dict[str, Any]:
    """Describe exactly which paired decisions were corrected or regressed."""

    if not (
        len(labels) == len(before_predictions) == len(after_predictions)
    ):
        raise ValueError("Labels and paired predictions must have equal lengths.")
    if len(labels) == 0:
        raise ValueError("At least one paired prediction is required.")

    normalized_labels = [int(value) for value in labels]
    before = [int(value) for value in before_predictions]
    after = [int(value) for value in after_predictions]
    if any(value not in {0, 1} for value in normalized_labels + before + after):
        raise ValueError("Paired transition audit supports binary labels only.")

    before_correct = [prediction == label for prediction, label in zip(before, normalized_labels)]
    after_correct = [prediction == label for prediction, label in zip(after, normalized_labels)]
    corrected = sum(not first and second for first, second in zip(before_correct, after_correct))
    regressed = sum(first and not second for first, second in zip(before_correct, after_correct))
    correct_both = sum(first and second for first, second in zip(before_correct, after_correct))
    wrong_both = sum(not first and not second for first, second in zip(before_correct, after_correct))
    changed = sum(first != second for first, second in zip(before, after))
    before_correct_count = sum(before_correct)
    after_correct_count = sum(after_correct)

    per_class: dict[str, Any] = {}
    for label in (0, 1):
        indices = [index for index, value in enumerate(normalized_labels) if value == label]
        class_corrected = sum(
            not before_correct[index] and after_correct[index] for index in indices
        )
        class_regressed = sum(
            before_correct[index] and not after_correct[index] for index in indices
        )
        per_class[str(label)] = {
            "samples": len(indices),
            "corrected": class_corrected,
            "regressed": class_regressed,
            "netCorrect": class_corrected - class_regressed,
        }

    return {
        "samples": len(normalized_labels),
        "correctBoth": correct_both,
        "wrongToRight": corrected,
        "rightToWrong": regressed,
        "wrongBoth": wrong_both,
        "changedDecisions": changed,
        "netCorrect": corrected - regressed,
        "beforeAccuracy": before_correct_count / len(normalized_labels),
        "afterAccuracy": after_correct_count / len(normalized_labels),
        "correctionPrecision": corrected / changed if changed else None,
        "exactMcNemarPValue": exact_mcnemar_p_value(corrected, regressed),
        "perClass": per_class,
    }
