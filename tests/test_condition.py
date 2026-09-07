from __future__ import annotations

import pytest

from workflow_engine.domain.condition import ConditionAll, ConditionAny, ConditionLeaf, evaluate


@pytest.mark.parametrize(
    "field,operator,value,variables,expected",
    [
        ("risk_score", "eq", 80, {"risk_score": 80}, True),
        ("risk_score", "eq", 80, {"risk_score": 81}, False),
        ("risk_score", "neq", 80, {"risk_score": 81}, True),
        ("risk_score", "gt", 80, {"risk_score": 81}, True),
        ("risk_score", "gt", 80, {"risk_score": 80}, False),
        ("risk_score", "gte", 80, {"risk_score": 80}, True),
        ("risk_score", "lt", 80, {"risk_score": 79}, True),
        ("risk_score", "lte", 80, {"risk_score": 80}, True),
        ("log", "contains", "err", {"log": "an error occurred"}, True),
        ("log", "contains", "zzz", {"log": "an error occurred"}, False),
        ("risk_score", "exists", None, {"risk_score": None}, True),
        ("risk_score", "exists", None, {}, False),
    ],
)
def test_leaf_operators(
    field: str, operator: str, value: object, variables: dict, expected: bool
) -> None:
    expr = ConditionLeaf(field=field, operator=operator, value=value)
    assert evaluate(expr, variables) is expected


def test_missing_field_is_false_except_for_exists() -> None:
    expr = ConditionLeaf(field="missing", operator="eq", value=1)
    assert evaluate(expr, {}) is False


def test_dotted_path_resolution() -> None:
    expr = ConditionLeaf(field="classification.risk_score", operator="gte", value=80)
    assert evaluate(expr, {"classification": {"risk_score": 85}}) is True
    assert evaluate(expr, {"classification": {"risk_score": 10}}) is False
    assert evaluate(expr, {"classification": "not-a-dict"}) is False


def test_all_composition_requires_every_subexpression() -> None:
    expr = ConditionAll(
        all=[
            ConditionLeaf(field="risk_score", operator="gte", value=80),
            ConditionLeaf(field="region", operator="eq", value="BR"),
        ]
    )
    assert evaluate(expr, {"risk_score": 90, "region": "BR"}) is True
    assert evaluate(expr, {"risk_score": 90, "region": "US"}) is False


def test_any_composition_requires_one_subexpression() -> None:
    expr = ConditionAny(
        any=[
            ConditionLeaf(field="risk_score", operator="gte", value=80),
            ConditionLeaf(field="flagged", operator="eq", value=True),
        ]
    )
    assert evaluate(expr, {"risk_score": 10, "flagged": True}) is True
    assert evaluate(expr, {"risk_score": 10, "flagged": False}) is False


def test_numeric_operator_rejects_non_numeric_value() -> None:
    with pytest.raises(ValueError):
        ConditionLeaf(field="risk_score", operator="gte", value="high")


def test_evaluation_is_deterministic() -> None:
    expr = ConditionLeaf(field="risk_score", operator="gte", value=80)
    variables = {"risk_score": 85}
    results = {evaluate(expr, variables) for _ in range(50)}
    assert results == {True}


def test_contains_on_non_container_returns_false_not_error() -> None:
    expr = ConditionLeaf(field="risk_score", operator="contains", value="x")
    assert evaluate(expr, {"risk_score": 42}) is False
