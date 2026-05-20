from __future__ import annotations
from typing import Any
import jsonschema

NUMERIC_TOLERANCE = 0.01


def _fields_equal(a: Any, b: Any) -> bool:
    """Compare two field values: numeric within tolerance, strings strict,
    lists order-insensitive (content-equality on each item)."""
    if isinstance(a, (int, float)) and isinstance(b, (int, float)) and not isinstance(a, bool) and not isinstance(b, bool):
        return abs(a - b) <= NUMERIC_TOLERANCE
    if isinstance(a, list) and isinstance(b, list):
        if len(a) != len(b):
            return False
        unmatched = list(b)
        for item in a:
            match_idx = next(
                (i for i, candidate in enumerate(unmatched) if _fields_equal(item, candidate)),
                None,
            )
            if match_idx is None:
                return False
            unmatched.pop(match_idx)
        return True
    if isinstance(a, dict) and isinstance(b, dict):
        if set(a.keys()) != set(b.keys()):
            return False
        return all(_fields_equal(a[k], b[k]) for k in a)
    return a == b


def field_accuracy(pred: dict[str, Any], ground_truth: dict[str, Any]) -> float:
    """Fraction of ground-truth fields the prediction got right.
    Extra fields in `pred` not in `ground_truth` are ignored.
    Missing fields in `pred` count as wrong."""
    if not ground_truth:
        return 1.0
    correct = sum(
        1
        for key, gt_value in ground_truth.items()
        if key in pred and _fields_equal(pred[key], gt_value)
    )
    return correct / len(ground_truth)


def schema_validity(output: dict[str, Any], schema: dict[str, Any]) -> bool:
    """Returns True iff `output` validates against the JSON schema."""
    try:
        jsonschema.validate(instance=output, schema=schema)
        return True
    except jsonschema.ValidationError:
        return False
