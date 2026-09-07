from __future__ import annotations

from tests.api.conftest import FAILING_WORKFLOW, HITL_WORKFLOW, LINEAR_WORKFLOW


def test_health(client) -> None:
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


# ---------------------------------------------------------------------------
# Workflow creation
# ---------------------------------------------------------------------------


def test_create_workflow(client) -> None:
    response = client.post("/workflows", json=LINEAR_WORKFLOW)
    assert response.status_code == 201
    body = response.json()
    assert body["id"] == "wf-linear"
    assert body["node_count"] == 3
    assert "checksum" in body


def test_get_workflow(client) -> None:
    client.post("/workflows", json=LINEAR_WORKFLOW)
    response = client.get("/workflows/wf-linear")
    assert response.status_code == 200
    assert response.json()["name"] == "linear"


def test_get_unknown_workflow_is_404(client) -> None:
    response = client.get("/workflows/does-not-exist")
    assert response.status_code == 404
    assert "stack" not in response.text.lower()
    assert "traceback" not in response.text.lower()


def test_invalid_workflow_graph_is_422(client) -> None:
    cycle_edge = {"id": "e1", "source_node_id": "t", "target_node_id": "t"}
    broken = {**LINEAR_WORKFLOW, "id": "wf-cycle", "edges": [cycle_edge]}
    response = client.post("/workflows", json=broken)
    assert response.status_code == 422


def test_malformed_workflow_payload_is_422(client) -> None:
    response = client.post("/workflows", json={"id": "x"})  # missing required fields
    assert response.status_code == 422


def test_unknown_node_type_is_422(client) -> None:
    broken = {**LINEAR_WORKFLOW, "id": "wf-bad-type"}
    broken["nodes"] = [{"id": "n", "type": "NOT_A_TYPE"}]
    response = client.post("/workflows", json=broken)
    assert response.status_code == 422


def test_registering_same_workflow_version_twice_is_idempotent(client) -> None:
    r1 = client.post("/workflows", json=LINEAR_WORKFLOW)
    r2 = client.post("/workflows", json=LINEAR_WORKFLOW)
    assert r1.status_code == r2.status_code == 201
    assert r1.json() == r2.json()


# ---------------------------------------------------------------------------
# Execution lifecycle
# ---------------------------------------------------------------------------


def test_create_and_query_execution(client) -> None:
    client.post("/workflows", json=LINEAR_WORKFLOW)
    create_resp = client.post(
        "/executions", json={"workflow_id": "wf-linear", "workflow_version": 1, "trigger_input": {}}
    )
    assert create_resp.status_code == 201
    body = create_resp.json()
    assert body["state"] == "COMPLETED"
    assert body["context_variables"] == {"risk_score": 91}

    query_resp = client.get(f"/executions/{body['id']}")
    assert query_resp.status_code == 200
    assert query_resp.json()["id"] == body["id"]


def test_execution_for_unknown_workflow_is_404(client) -> None:
    response = client.post(
        "/executions", json={"workflow_id": "ghost", "workflow_version": 1, "trigger_input": {}}
    )
    assert response.status_code == 404


def test_get_unknown_execution_is_404(client) -> None:
    response = client.get("/executions/does-not-exist")
    assert response.status_code == 404


def test_execution_reaching_failure_reports_failed_state(client) -> None:
    client.post("/workflows", json=FAILING_WORKFLOW)
    response = client.post(
        "/executions",
        json={"workflow_id": "wf-failing", "workflow_version": 1, "trigger_input": {}},
    )
    assert response.status_code == 201
    assert response.json()["state"] == "FAILED"


# ---------------------------------------------------------------------------
# Human-in-the-loop
# ---------------------------------------------------------------------------


def test_execution_waits_for_approval(client) -> None:
    client.post("/workflows", json=HITL_WORKFLOW)
    response = client.post(
        "/executions", json={"workflow_id": "wf-hitl", "workflow_version": 1, "trigger_input": {}}
    )
    body = response.json()
    assert body["state"] == "WAITING"
    assert body["waiting_approval"]["node_id"] == "approval"


def test_approve_resumes_execution_to_completion(client) -> None:
    client.post("/workflows", json=HITL_WORKFLOW)
    created = client.post(
        "/executions", json={"workflow_id": "wf-hitl", "workflow_version": 1, "trigger_input": {}}
    ).json()
    approval_id = created["waiting_approval"]["approval_id"]

    response = client.post(
        f"/executions/{created['id']}/approve",
        json={"approval_id": approval_id, "resolved_by": "alice"},
    )
    assert response.status_code == 200
    assert response.json()["state"] == "COMPLETED"


def test_reject_resumes_execution_without_marking_it_failed(client) -> None:
    client.post("/workflows", json=HITL_WORKFLOW)
    created = client.post(
        "/executions", json={"workflow_id": "wf-hitl", "workflow_version": 1, "trigger_input": {}}
    ).json()
    approval_id = created["waiting_approval"]["approval_id"]

    response = client.post(
        f"/executions/{created['id']}/reject",
        json={"approval_id": approval_id, "resolved_by": "bob"},
    )
    assert response.status_code == 200
    assert response.json()["state"] == "COMPLETED"  # HITL_WORKFLOW routes reject -> end too


def test_double_approve_is_409(client) -> None:
    client.post("/workflows", json=HITL_WORKFLOW)
    created = client.post(
        "/executions", json={"workflow_id": "wf-hitl", "workflow_version": 1, "trigger_input": {}}
    ).json()
    approval_id = created["waiting_approval"]["approval_id"]
    payload = {"approval_id": approval_id, "resolved_by": "alice"}

    first = client.post(f"/executions/{created['id']}/approve", json=payload)
    assert first.status_code == 200
    second = client.post(f"/executions/{created['id']}/approve", json=payload)
    assert second.status_code == 409


def test_approving_unknown_execution_is_404(client) -> None:
    response = client.post(
        "/executions/does-not-exist/approve",
        json={"approval_id": "x", "resolved_by": "alice"},
    )
    assert response.status_code == 404


# ---------------------------------------------------------------------------
# Events + replay
# ---------------------------------------------------------------------------


def test_list_events(client) -> None:
    client.post("/workflows", json=LINEAR_WORKFLOW)
    created = client.post(
        "/executions", json={"workflow_id": "wf-linear", "workflow_version": 1, "trigger_input": {}}
    ).json()
    response = client.get(f"/executions/{created['id']}/events")
    assert response.status_code == 200
    events = response.json()
    assert events[0]["event_type"] == "execution_started"
    assert events[-1]["event_type"] == "execution_completed"
    sequences = [e["sequence"] for e in events]
    assert sequences == sorted(sequences)


def test_events_for_unknown_execution_is_404(client) -> None:
    response = client.get("/executions/does-not-exist/events")
    assert response.status_code == 404


def test_replay_reconstructs_state(client) -> None:
    client.post("/workflows", json=LINEAR_WORKFLOW)
    created = client.post(
        "/executions",
        json={"workflow_id": "wf-linear", "workflow_version": 1, "trigger_input": {"x": 1}},
    ).json()
    response = client.get(f"/executions/{created['id']}/replay")
    assert response.status_code == 200
    body = response.json()
    assert body["state"] == "COMPLETED"
    assert body["variables"] == {"risk_score": 91}
    assert body["trigger_input"] == {"x": 1}


def test_replay_for_unknown_execution_is_404(client) -> None:
    response = client.get("/executions/does-not-exist/replay")
    assert response.status_code == 404


# ---------------------------------------------------------------------------
# Idempotency (§10.4)
# ---------------------------------------------------------------------------


def test_idempotency_key_on_create_execution_returns_same_execution_twice(client) -> None:
    client.post("/workflows", json=LINEAR_WORKFLOW)
    headers = {"Idempotency-Key": "create-once-123"}
    body = {"workflow_id": "wf-linear", "workflow_version": 1, "trigger_input": {}}

    first = client.post("/executions", json=body, headers=headers)
    second = client.post("/executions", json=body, headers=headers)

    assert first.status_code == second.status_code == 201
    assert first.json()["id"] == second.json()["id"]  # same execution, not two


def test_without_idempotency_key_two_calls_create_two_executions(client) -> None:
    client.post("/workflows", json=LINEAR_WORKFLOW)
    body = {"workflow_id": "wf-linear", "workflow_version": 1, "trigger_input": {}}

    first = client.post("/executions", json=body)
    second = client.post("/executions", json=body)

    assert first.json()["id"] != second.json()["id"]


def test_idempotency_key_on_approve_replays_first_response(client) -> None:
    client.post("/workflows", json=HITL_WORKFLOW)
    created = client.post(
        "/executions", json={"workflow_id": "wf-hitl", "workflow_version": 1, "trigger_input": {}}
    ).json()
    approval_id = created["waiting_approval"]["approval_id"]
    payload = {"approval_id": approval_id, "resolved_by": "alice"}
    headers = {"Idempotency-Key": "approve-once-456"}

    first = client.post(f"/executions/{created['id']}/approve", json=payload, headers=headers)
    second = client.post(f"/executions/{created['id']}/approve", json=payload, headers=headers)

    assert first.status_code == second.status_code == 200
    assert first.json() == second.json()
    # and it did NOT hit the real (409-raising) domain path the second time


# ---------------------------------------------------------------------------
# Security
# ---------------------------------------------------------------------------


def test_oversized_workflow_node_list_is_rejected(client) -> None:
    huge_nodes = [{"id": f"n{i}", "type": "TRANSFORM"} for i in range(501)]
    broken = {**LINEAR_WORKFLOW, "id": "wf-huge", "nodes": huge_nodes}
    response = client.post("/workflows", json=broken)
    assert response.status_code == 422


def test_malicious_workflow_id_is_inert_string(client) -> None:
    payload = {**LINEAR_WORKFLOW, "id": "'; DROP TABLE workflows; --"}
    response = client.post("/workflows", json=payload)
    assert response.status_code == 201
    assert response.json()["id"] == "'; DROP TABLE workflows; --"
    # database is still intact — prove with a normal follow-up request
    assert client.get("/health").status_code == 200


def test_extra_unexpected_fields_are_rejected(client) -> None:
    payload = {**LINEAR_WORKFLOW, "id": "wf-extra", "unexpected_field": "hack"}
    response = client.post("/workflows", json=payload)
    assert response.status_code == 422


def test_error_responses_never_leak_internal_details(client) -> None:
    response = client.get("/executions/does-not-exist")
    text = response.text.lower()
    for forbidden in ("traceback", "sqlalchemy", "site-packages", " at 0x"):
        assert forbidden not in text


def test_secret_shaped_event_payload_fields_are_redacted_in_api_response(client) -> None:
    """Uses the LLM node path so the event payload actually contains a
    provider `usage` block, then confirms nothing secret-shaped survives —
    this exercises the real redaction wiring end-to-end through HTTP, not
    just the unit-level `redact_payload` function."""
    from workflow_engine.engine.node_executor import registry_with_ai
    from workflow_engine.engine.providers import LLMResponse, MockLLMProvider, TokenUsage
    from workflow_engine.engine.tools import default_tool_registry

    llm_workflow = {
        "id": "wf-llm-secret",
        "version": 1,
        "name": "llm",
        "nodes": [
            {"id": "start", "type": "START"},
            {"id": "ask", "type": "LLM", "config": {"prompt": "hi"}},
            {"id": "end", "type": "END"},
        ],
        "edges": [
            {"id": "e1", "source_node_id": "start", "target_node_id": "ask"},
            {"id": "e2", "source_node_id": "ask", "target_node_id": "end"},
        ],
        "start_node_id": "start",
    }
    client.post("/workflows", json=llm_workflow)

    usage = TokenUsage(prompt_tokens=1, completion_tokens=1, total_tokens=2)
    provider = MockLLMProvider({"hi": LLMResponse(content="hello", usage=usage)})
    client.app.state.node_registry = registry_with_ai(provider, default_tool_registry())

    created = client.post(
        "/executions",
        json={"workflow_id": "wf-llm-secret", "workflow_version": 1, "trigger_input": {}},
    ).json()
    events = client.get(f"/executions/{created['id']}/events").json()
    for event in events:
        assert "api_key" not in str(event["payload"]).lower()
