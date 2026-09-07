"""Condition DSL — DESIGN.md §5.

Included in Fase 1 because `WorkflowEdge.condition` needs a concrete type and
the graph-validation rule "a ConditionNode must have >=2 conditioned edges"
(DESIGN.md §4.1 rule 5) is only meaningful once that type exists. `evaluate()`
is a pure function (no I/O, no randomness, no wall-clock) so it belongs in the
domain layer per DESIGN.md §5.3 — it is deliberately minimal: only the
`ConditionLeaf` + `all`/`any` composition described in the design, no new
operators.
"""

from __future__ import annotations

from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field

from workflow_engine.domain.enums import ConditionOperator

_NUMERIC_OPERATORS = {
    ConditionOperator.GT,
    ConditionOperator.GTE,
    ConditionOperator.LT,
    ConditionOperator.LTE,
}


class ConditionLeaf(BaseModel):
    """A single `{field, operator, value}` comparison against ExecutionContext.variables."""

    model_config = ConfigDict(frozen=True)

    field: str
    operator: ConditionOperator
    value: Any = None

    def model_post_init(self, __context: Any) -> None:
        if self.operator == ConditionOperator.EXISTS:
            return
        if self.operator in _NUMERIC_OPERATORS and not isinstance(self.value, (int, float)):
            raise ValueError(f"operator {self.operator.value!r} requires a numeric value")


class ConditionAll(BaseModel):
    model_config = ConfigDict(frozen=True)

    all: list[ConditionExpr] = Field(min_length=1)


class ConditionAny(BaseModel):
    model_config = ConfigDict(frozen=True)

    any: list[ConditionExpr] = Field(min_length=1)


ConditionExpr = Annotated[ConditionLeaf | ConditionAll | ConditionAny, Field()]

ConditionAll.model_rebuild()
ConditionAny.model_rebuild()


def _resolve(field: str, variables: dict[str, Any]) -> tuple[bool, Any]:
    """Resolve a dotted path (e.g. "classification.risk_score") in `variables`.

    Returns (found, value). A key present with value `None` still counts as found.
    """
    current: Any = variables
    for part in field.split("."):
        if not isinstance(current, dict) or part not in current:
            return False, None
        current = current[part]
    return True, current


def evaluate(expr: ConditionExpr, variables: dict[str, Any]) -> bool:
    """Deterministically evaluate a condition expression. No I/O, no side effects."""
    if isinstance(expr, ConditionAll):
        return all(evaluate(sub, variables) for sub in expr.all)
    if isinstance(expr, ConditionAny):
        return any(evaluate(sub, variables) for sub in expr.any)

    found, actual = _resolve(expr.field, variables)

    if expr.operator == ConditionOperator.EXISTS:
        return found
    if not found:
        return False
    if expr.operator == ConditionOperator.EQ:
        return bool(actual == expr.value)
    if expr.operator == ConditionOperator.NEQ:
        return bool(actual != expr.value)
    if expr.operator == ConditionOperator.GT:
        return bool(actual is not None and actual > expr.value)
    if expr.operator == ConditionOperator.GTE:
        return bool(actual is not None and actual >= expr.value)
    if expr.operator == ConditionOperator.LT:
        return bool(actual is not None and actual < expr.value)
    if expr.operator == ConditionOperator.LTE:
        return bool(actual is not None and actual <= expr.value)
    if expr.operator == ConditionOperator.CONTAINS:
        try:
            return bool(expr.value in actual)
        except TypeError:
            return False
    raise AssertionError(f"unhandled operator: {expr.operator}")  # pragma: no cover
