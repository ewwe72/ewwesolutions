import pytest
from runner.scoring import field_accuracy, schema_validity


def test_field_accuracy_perfect_match():
    gt = {"a": "x", "b": 1.0}
    pred = {"a": "x", "b": 1.0}
    assert field_accuracy(pred, gt) == 1.0


def test_field_accuracy_one_wrong_string():
    gt = {"a": "x", "b": "y"}
    pred = {"a": "x", "b": "wrong"}
    assert field_accuracy(pred, gt) == 0.5


def test_field_accuracy_numeric_tolerance():
    gt = {"total_gross": 2460.00}
    pred = {"total_gross": 2460.005}  # within 0.01 tolerance
    assert field_accuracy(pred, gt) == 1.0


def test_field_accuracy_missing_field_counts_as_wrong():
    gt = {"a": "x", "b": "y"}
    pred = {"a": "x"}  # missing b
    assert field_accuracy(pred, gt) == 0.5


def test_field_accuracy_line_items_order_insensitive():
    gt = {"line_items": [
        {"description": "A", "quantity": 1},
        {"description": "B", "quantity": 2},
    ]}
    pred = {"line_items": [
        {"description": "B", "quantity": 2},
        {"description": "A", "quantity": 1},
    ]}
    assert field_accuracy(pred, gt) == 1.0


def test_field_accuracy_extra_predicted_fields_ignored():
    gt = {"a": "x"}
    pred = {"a": "x", "extra": "stuff"}
    assert field_accuracy(pred, gt) == 1.0


def test_field_accuracy_nip_string_strict():
    gt = {"seller_nip": "1234567890"}
    pred = {"seller_nip": "123-456-78-90"}  # formatted differently — counts as wrong
    assert field_accuracy(pred, gt) == 0.0


def test_schema_validity_valid_object():
    schema = {
        "type": "object",
        "required": ["nip"],
        "properties": {"nip": {"type": "string", "pattern": "^[0-9]{10}$"}},
    }
    assert schema_validity({"nip": "1234567890"}, schema) is True


def test_schema_validity_missing_required():
    schema = {"type": "object", "required": ["nip"], "properties": {"nip": {"type": "string"}}}
    assert schema_validity({}, schema) is False


def test_schema_validity_bad_pattern():
    schema = {
        "type": "object",
        "required": ["nip"],
        "properties": {"nip": {"type": "string", "pattern": "^[0-9]{10}$"}},
    }
    assert schema_validity({"nip": "abc"}, schema) is False


def test_schema_validity_accepts_extras():
    schema = {"type": "object", "required": ["a"], "properties": {"a": {"type": "string"}}}
    # No additionalProperties:false, so extras are fine
    assert schema_validity({"a": "x", "extra": 1}, schema) is True
