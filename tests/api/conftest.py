"""Fase 10 §10.7 — every test gets a fully isolated app: its own `:memory:`
SQLite database (via `StaticPool`, see `persistence/db.py`) and its own
Mock LLM provider — nothing shared between tests, nothing shared with a real
LLM.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from workflow_engine.api.app import create_app
from workflow_engine.api.config import Settings


@pytest.fixture()
def client() -> TestClient:
    settings = Settings(DATABASE_URL=":memory:", LLM_PROVIDER="mock")
    app = create_app(settings)
    with TestClient(app) as test_client:
        yield test_client


LINEAR_WORKFLOW = {
    "id": "wf-linear",
    "version": 1,
    "name": "linear",
    "nodes": [
        {"id": "start", "type": "START"},
        {"id": "t", "type": "TRANSFORM", "config": {"set": {"risk_score": 91}}},
        {"id": "end", "type": "END"},
    ],
    "edges": [
        {"id": "e1", "source_node_id": "start", "target_node_id": "t"},
        {"id": "e2", "source_node_id": "t", "target_node_id": "end"},
    ],
    "start_node_id": "start",
}

HITL_WORKFLOW = {
    "id": "wf-hitl",
    "version": 1,
    "name": "hitl",
    "nodes": [
        {"id": "start", "type": "START"},
        {"id": "approval", "type": "HUMAN_APPROVAL"},
        {"id": "end", "type": "END"},
    ],
    "edges": [
        {"id": "e1", "source_node_id": "start", "target_node_id": "approval"},
        {"id": "e2", "source_node_id": "approval", "target_node_id": "end"},
    ],
    "start_node_id": "start",
}

FAILING_WORKFLOW = {
    "id": "wf-failing",
    "version": 1,
    "name": "failing",
    "nodes": [
        {"id": "start", "type": "START"},
        {"id": "bad", "type": "TRANSFORM", "config": {"set": "not-a-dict"}},
        {"id": "end", "type": "END"},
    ],
    "edges": [
        {"id": "e1", "source_node_id": "start", "target_node_id": "bad"},
        {"id": "e2", "source_node_id": "bad", "target_node_id": "end"},
    ],
    "start_node_id": "start",
}
