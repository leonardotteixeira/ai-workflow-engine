from __future__ import annotations

from datetime import timedelta

import pytest
from pydantic import ValidationError

from tests.factories import NOW, make_execution
from workflow_engine.domain import ExecutionContext, ExecutionState


def test_execution_context_with_patch_merges_without_mutating_original() -> None:
    ctx = ExecutionContext(trigger_input={"x": 1}, variables={"a": 1})
    patched = ctx.with_patch({"b": 2})
    assert patched.variables == {"a": 1, "b": 2}
    assert ctx.variables == {"a": 1}  # original untouched
    assert patched.trigger_input == {"x": 1}


def test_execution_context_patch_overrides_same_key() -> None:
    ctx = ExecutionContext(variables={"a": 1})
    patched = ctx.with_patch({"a": 99})
    assert patched.variables == {"a": 99}


def test_execution_context_is_frozen() -> None:
    ctx = ExecutionContext()
    with pytest.raises(ValidationError):
        ctx.variables = {"x": 1}  # type: ignore[misc]


def test_execution_is_frozen() -> None:
    execution = make_execution()
    with pytest.raises(ValidationError):
        execution.state = ExecutionState.RUNNING  # type: ignore[misc]


def test_execution_transition_bumps_version_monotonically() -> None:
    execution = make_execution(state=ExecutionState.PENDING)
    v1 = execution.transition_to(ExecutionState.RUNNING, now=NOW)
    v2 = v1.transition_to(ExecutionState.WAITING, now=NOW + timedelta(seconds=1))
    assert (execution.version, v1.version, v2.version) == (1, 2, 3)


def test_with_context_bumps_version_and_replaces_context() -> None:
    execution = make_execution(state=ExecutionState.RUNNING)
    new_ctx = execution.context.with_patch({"llm_output": {"risk_score": 91}})
    updated = execution.with_context(new_ctx, now=NOW + timedelta(seconds=2))
    assert updated.context.variables == {"llm_output": {"risk_score": 91}}
    assert updated.version == execution.version + 1
    assert execution.context.variables == {}


def test_with_frontier_replaces_current_node_ids() -> None:
    execution = make_execution(state=ExecutionState.RUNNING)
    updated = execution.with_frontier(["node-2"], now=NOW)
    assert updated.current_node_ids == ["node-2"]
    assert execution.current_node_ids == ["start"]
