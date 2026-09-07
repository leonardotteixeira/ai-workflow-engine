# Architecture

This document explains how the pieces fit together and why the boundaries are drawn where they are. For the domain model, state machine, and reliability semantics in full detail, see [`DESIGN.md`](../DESIGN.md) — this file is the map; `DESIGN.md` is the territory.

## Layering

```
                         ┌─────────────┐
                         │  Frontend   │  React + TypeScript SPA (frontend/)
                         └──────┬──────┘
                                │ HTTP/JSON
                         ┌──────▼──────┐
                         │     API     │  FastAPI (src/workflow_engine/api/)
                         └──────┬──────┘
                                │
                         ┌──────▼──────┐
                         │ Persistence │  SQLite/SQLAlchemy (.../persistence/)
                         └──────┬──────┘
                                │
                         ┌──────▼──────┐
                         │   Engine    │  in-memory orchestration (.../engine/)
                         └──────┬──────┘
                                │
                         ┌──────▼──────┐
                         │   Domain    │  pure data + state machines (.../domain/)
                         └─────────────┘
```

Dependencies point only downward: `domain` imports nothing from this project; `engine` imports only `domain`; `persistence` imports `domain` and `engine`; `api` imports all three. This is enforced by convention and checked by a grep-based architecture test in the suite (`tests/test_security.py` and friends) — not by a dependency-injection framework, which would be overkill for four packages.

### Why this split

- **Domain** (Fase 1–2) is pure: Pydantic models, a formal state machine, a condition DSL with no `eval`. Zero I/O. This is the part that has to be *correct*, and correctness is cheapest to verify when nothing else can interfere with it.
- **Engine** (Fase 3, 5, 6, 8, 9) is an in-memory orchestrator. It knows how to walk a `WorkflowDefinition`'s graph, dispatch to node executors via a registry (never `if node.type == ...`), retry, and emit events — but it has never heard of SQL or HTTP.
- **Persistence** (Fase 7) is the only layer that knows SQLite exists. It wraps the Engine in a transaction boundary and adds durability, recovery, and optimistic concurrency (CAS) on top of an Engine that itself has no concept of "the process might die."
- **API** (Fase 10) is a thin translation layer: HTTP → Pydantic schemas → application service functions → the layers below. No business logic lives in a route handler.
- **Frontend** (Fase 11) is a separate, independently deployable SPA that only ever talks to the API over HTTP — it has no access to the database or the Engine directly, by construction.

## Execution lifecycle

```
PENDING
   │  ExecutionEngine.run()
   ▼
RUNNING ──────────────┐
   │                  │
   │ HumanApprovalNode │ node fails, no
   │ reached           │ retryable policy
   ▼                  ▼
WAITING            FAILED
   │  resume()
   │  (approve/reject)
   ▼
RUNNING
   │  reaches an END node
   ▼
COMPLETED

(PENDING/RUNNING/WAITING) ──cancel()──▶ CANCELLED
```

Every arrow above is a transition the domain's state machine (`domain/state_machine.py`) explicitly allows — anything not drawn here is rejected with `InvalidStateTransitionError`. A `RetryPolicy`-eligible node failure doesn't appear as a top-level arrow: it's handled *inside* `RUNNING` (the Engine retries synchronously, up to `max_attempts`, before ever touching `Execution.state`).

## Request flow: starting an execution

```
POST /executions
   │
   ▼
api/routers/executions.py         (parses request, handles Idempotency-Key)
   │
   ▼
api/services.py::start_execution   (application service — no business logic
   │                                 beyond "translate schema, call engine")
   ▼
PersistentExecutionEngine.start()  (one SQL transaction)
   │
   ├─▶ ExecutionRepository.insert()
   ├─▶ ExecutionEngine.run()        (in-memory: walks the graph,
   │      │                          dispatches to NodeExecutorRegistry,
   │      │                          applies context_patch, emits events)
   │      ▼
   │   EngineRunResult
   │
   ├─▶ ExecutionRepository.update_cas()
   ├─▶ NodeExecutionRepository.insert() × N
   ├─▶ EventRepository.insert() × N
   └─▶ ApprovalRepository.insert() (if the run ended WAITING)
```

If anything in that transaction raises — including a `ConcurrencyConflictError` from the CAS update — the whole thing rolls back. The caller never observes a partially-applied execution.

## Node dispatch (no `if/elif` chain)

```
WorkflowNode.type ──▶ NodeExecutorRegistry.resolve(type) ──▶ NodeExecutor.execute(node, context)
                                                                      │
                                                              NodeResult(status, output,
                                                                context_patch, events)
```

Seven executors are registered by default (`START`, `END`, `CONDITION`, `TRANSFORM`, `HUMAN_APPROVAL`, `LLM`, `TOOL`). Adding an eighth node type means writing one class implementing the `NodeExecutor` protocol and registering it — the Engine's dispatch loop (`engine/engine.py::_loop`) never changes.

## Branching

`CONDITION` nodes don't evaluate their own edges — the Engine calls `engine/next_node.py::resolve_next_node_id`, which in turn calls the domain's `condition.evaluate()` (Fase 1, no `eval`/`exec`, closed operator set). First matching edge (in declaration order) wins; an unconditioned "default" edge is the fallback; no match and no default raises `NoMatchingBranchError` rather than guessing.

## Human-in-the-loop

`HumanApprovalNode` always returns `status="waiting"`. The Engine then:

1. Transitions `Execution` to `WAITING`.
2. Constructs an `ApprovalRequest` (domain object from Fase 1 — no second state machine).
3. Returns it to the caller as part of `EngineRunResult.approval_request`.

Resuming (`ExecutionEngine.resume()`) validates through the *exact same* `domain.approve()`/`domain.reject()` functions a direct domain-level caller would use, then continues the graph traversal from the node after the approval. Branching on the decision (approved vs. rejected) is not special-cased — the decision is written into context under the node's own key (`{node_id: {"decision": ..., "resolved_by": ...}}`), and a normal `CONDITION` node placed after it branches on that value using the same condition DSL as anywhere else.

## Persistence & recovery

See [`DESIGN.md` §7, §10, §12](../DESIGN.md) for the full design. In short: every `PersistentExecutionEngine` call is one transaction; `ExecutionRepository`/`ApprovalRepository` enforce optimistic concurrency via `UPDATE ... WHERE version = ?`; and `persistence/recovery.py` detects a `NodeExecution` stuck `RUNNING` past a heartbeat timeout and marks it `FAILED` (a new row, never mutating the original) without ever re-executing it blindly.

## Replay vs. rerun

`engine/replay.py::replay()` takes an `Event` list and reconstructs `variables`/`trigger_input`/node history/inferred state by folding over the log — it never imports the Engine, a provider, or a tool (checked by an AST-based test). This is reconstruction, not reexecution: a workflow with a real LLM node cannot be "replayed" into calling the LLM again, by design.
