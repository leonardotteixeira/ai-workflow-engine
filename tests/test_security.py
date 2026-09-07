"""Security-oriented tests for the domain layer.

Two concerns, per DESIGN.md §5 and the Fase 1 brief:
1. The condition DSL must never execute arbitrary code — it is a closed,
   typed schema, not a mini-interpreter.
2. The domain layer must have zero infrastructure imports (no network, no
   filesystem, no subprocess, no DB driver, no HTTP client, no LLM SDK).
"""

from __future__ import annotations

import ast
import importlib
import pathlib

import pytest

from workflow_engine.domain.condition import ConditionLeaf, evaluate

FORBIDDEN_MODULE_PREFIXES = (
    "fastapi",
    "starlette",
    "sqlalchemy",
    "httpx",
    "requests",
    "aiohttp",
    "openai",
    "anthropic",
    "subprocess",
    "socket",
    "sqlite3",
)


def test_condition_field_cannot_smuggle_code_execution() -> None:
    """A malicious `field` value is just a dotted-path string lookup — it can
    never reach eval/exec, so payloads like this resolve to "not found", not
    to code execution."""
    malicious = ConditionLeaf(field="__import__('os').system('echo pwned')", operator="exists")
    assert evaluate(malicious, {}) is False


def test_condition_value_is_never_interpreted_as_code() -> None:
    payload = "__import__('os').system('echo pwned')"
    expr = ConditionLeaf(field="user_input", operator="eq", value=payload)
    # the payload is compared as an opaque string, never executed
    assert evaluate(expr, {"user_input": payload}) is True
    assert evaluate(expr, {"user_input": "safe"}) is False


def test_condition_module_contains_no_eval_or_exec() -> None:
    source = pathlib.Path(
        importlib.import_module("workflow_engine.domain.condition").__file__  # type: ignore[arg-type]
    ).read_text(encoding="utf-8")
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            assert node.func.id not in {"eval", "exec", "compile", "__import__"}


def _iter_domain_source_files() -> list[pathlib.Path]:
    domain_dir = pathlib.Path(importlib.import_module("workflow_engine.domain").__file__).parent  # type: ignore[arg-type]
    return list(domain_dir.glob("*.py"))


@pytest.mark.parametrize("path", _iter_domain_source_files(), ids=lambda p: p.name)
def test_domain_module_has_no_forbidden_infrastructure_imports(path: pathlib.Path) -> None:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    imported_modules: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported_modules.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported_modules.append(node.module)

    for module in imported_modules:
        top_level = module.split(".")[0]
        assert top_level not in FORBIDDEN_MODULE_PREFIXES, (
            f"{path.name} imports forbidden infrastructure module: {module}"
        )


def test_domain_module_has_no_eval_exec_anywhere() -> None:
    for path in _iter_domain_source_files():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                assert node.func.id not in {"eval", "exec"}, f"{path.name} calls {node.func.id}()"
