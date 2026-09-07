from __future__ import annotations

import pytest

from workflow_engine.domain.enums import ErrorCategory
from workflow_engine.engine.errors import UnknownToolError
from workflow_engine.engine.tools import (
    CalculatorTool,
    TextLengthTool,
    ToolRegistry,
    default_tool_registry,
)


@pytest.mark.parametrize(
    "op,a,b,expected",
    [
        ("add", 2, 3, 5),
        ("subtract", 5, 3, 2),
        ("multiply", 4, 3, 12),
        ("divide", 10, 4, 2.5),
    ],
)
def test_calculator_tool_arithmetic(op: str, a: float, b: float, expected: float) -> None:
    result = CalculatorTool().execute({"op": op, "a": a, "b": b})
    assert result.status == "success"
    assert result.output == {"result": expected}


def test_calculator_tool_division_by_zero_is_a_validation_error() -> None:
    result = CalculatorTool().execute({"op": "divide", "a": 1, "b": 0})
    assert result.status == "error"
    assert result.error is not None
    assert result.error.category == ErrorCategory.VALIDATION


@pytest.mark.parametrize(
    "tool_input",
    [
        {"op": "modulo", "a": 1, "b": 2},
        {"op": "add", "a": "1", "b": 2},
        {"op": "add", "a": 1},
        {},
    ],
)
def test_calculator_tool_rejects_malformed_input(tool_input: dict) -> None:
    result = CalculatorTool().execute(tool_input)
    assert result.status == "error"
    assert result.error is not None


def test_text_length_tool() -> None:
    result = TextLengthTool().execute({"text": "hello"})
    assert result.status == "success"
    assert result.output == {"length": 5}


def test_text_length_tool_rejects_non_string_input() -> None:
    result = TextLengthTool().execute({"text": 123})
    assert result.status == "error"


def test_tool_registry_resolves_registered_tool() -> None:
    registry = default_tool_registry()
    tool = registry.resolve("calculator")
    assert tool.name == "calculator"


def test_tool_registry_raises_for_unknown_tool() -> None:
    registry = ToolRegistry()
    with pytest.raises(UnknownToolError):
        registry.resolve("does-not-exist")


def test_calculator_tool_rejects_malicious_input_as_inert_data() -> None:
    """A prompt-injection-shaped string as the 'op' value is just an invalid
    op — never interpreted or executed."""
    malicious_op = "__import__('os').system('echo pwned')"
    result = CalculatorTool().execute({"op": malicious_op, "a": 1, "b": 2})
    assert result.status == "error"


def test_no_eval_exec_subprocess_or_pickle_in_tools_module() -> None:
    import ast
    import importlib
    import pathlib

    source = pathlib.Path(
        importlib.import_module("workflow_engine.engine.tools").__file__  # type: ignore[arg-type]
    ).read_text(encoding="utf-8")
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            assert node.func.id not in {"eval", "exec", "compile"}
        if isinstance(node, ast.Import):
            assert all(a.name not in {"subprocess", "pickle", "os"} for a in node.names)
