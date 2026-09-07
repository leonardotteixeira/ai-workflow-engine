"""Tool abstraction — DESIGN.md §8.

`ToolNodeExecutor` (node_executor.py) depends only on the `Tool` protocol and
`ToolRegistry` here, never on a concrete tool implementation. A `Tool` never
sees an `Execution` or `ExecutionContext` — it only ever gets the plain input
dict the workflow author put in `WorkflowNode.config["input"]` (DESIGN.md §8:
"Não permitir que Tool conheça Execution internamente").

Both tools shipped here are intentionally tiny and fully deterministic:
no filesystem, no network, no subprocess, no `eval`. `CalculatorTool` takes a
*structured* `{op, a, b}` input rather than a string expression specifically
to avoid ever needing an expression parser (let alone `eval`) for arithmetic.
"""

from __future__ import annotations

from typing import Any, Literal, Protocol

from pydantic import BaseModel, ConfigDict

from workflow_engine.domain.enums import ErrorCategory
from workflow_engine.engine.errors import UnknownToolError

_ARITHMETIC_OPS = {"add", "subtract", "multiply", "divide"}


class ToolExecutionError(BaseModel):
    model_config = ConfigDict(frozen=True)

    category: ErrorCategory
    message: str


class ToolResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    status: Literal["success", "error"]
    output: dict[str, Any] | None = None
    error: ToolExecutionError | None = None


class Tool(Protocol):
    name: str
    description: str

    def execute(self, tool_input: dict[str, Any]) -> ToolResult: ...


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}

    def register(self, tool: Tool) -> None:
        self._tools[tool.name] = tool

    def resolve(self, name: str) -> Tool:
        tool = self._tools.get(name)
        if tool is None:
            raise UnknownToolError(f"no tool registered with name {name!r}")
        return tool


class CalculatorTool:
    """Input: `{"op": "add"|"subtract"|"multiply"|"divide", "a": number, "b": number}`."""

    name = "calculator"
    description = "Performs add/subtract/multiply/divide on two numbers."

    def execute(self, tool_input: dict[str, Any]) -> ToolResult:
        op = tool_input.get("op")
        a, b = tool_input.get("a"), tool_input.get("b")

        if (
            op not in _ARITHMETIC_OPS
            or not isinstance(a, (int, float))
            or not isinstance(b, (int, float))
        ):
            return ToolResult(
                status="error",
                error=ToolExecutionError(
                    category=ErrorCategory.VALIDATION,
                    message=(
                        "CalculatorTool requires {'op': one of "
                        f"{sorted(_ARITHMETIC_OPS)}, 'a': number, 'b': number}}, got {tool_input!r}"
                    ),
                ),
            )
        if op == "divide" and b == 0:
            return ToolResult(
                status="error",
                error=ToolExecutionError(
                    category=ErrorCategory.VALIDATION, message="division by zero"
                ),
            )

        result = {"add": a + b, "subtract": a - b, "multiply": a * b, "divide": a / b}[op]
        return ToolResult(status="success", output={"result": result})


class TextLengthTool:
    """Input: `{"text": str}`. Output: `{"length": int}`."""

    name = "text_length"
    description = "Returns the character length of the given text."

    def execute(self, tool_input: dict[str, Any]) -> ToolResult:
        text = tool_input.get("text")
        if not isinstance(text, str):
            return ToolResult(
                status="error",
                error=ToolExecutionError(
                    category=ErrorCategory.VALIDATION,
                    message=f"TextLengthTool requires {{'text': str}}, got {tool_input!r}",
                ),
            )
        return ToolResult(status="success", output={"length": len(text)})


def default_tool_registry() -> ToolRegistry:
    registry = ToolRegistry()
    registry.register(CalculatorTool())
    registry.register(TextLengthTool())
    return registry
