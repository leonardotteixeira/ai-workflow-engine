# AI Workflow Engine — Fase 0: Discovery, Domain Design & Implementation Plan

> Documento de arquitetura. Nenhum código de produção foi escrito nesta fase.
> Objetivo: workflow engine enxuto, autoral e tecnicamente rigoroso — não um clone de n8n/Temporal/Camunda.

---

## 0. Auditoria do ambiente

| Item | Resultado |
|---|---|
| Diretório do projeto | raiz do repositório `ai-workflow-engine` — vazio, sem arquivos |
| Git | não inicializado nesta pasta; git 2.53.0 disponível globalmente |
| Python | 3.11.9 e 3.14.3 instalados; nenhuma dependência (fastapi/sqlalchemy/pydantic/alembic) instalada globalmente — ambiente limpo |
| Node | v24.14.0, npm 11.9.0; nenhum pacote relevante instalado globalmente |
| Projeto anterior | `ai-research-agent` (Desktop, projeto Python com pyproject.toml, evals/, docs/, testes) — servirá **apenas como inspiração conceitual** (typed contracts, provider abstraction, structured logging, deterministic testing). Nenhum código será copiado; arquitetura será própria. |

Decisão: começar 100% do zero, sem inicializar git ainda, sem instalar dependências — esta fase é só design.

---

## 1. Linguagem e stack (decisão preliminar, justificada)

**Backend: Python 3.11+, FastAPI, SQLAlchemy 2.0, Pydantic v2, SQLite (V1) → Postgres (roadmap).**

Justificativa:
- Python é a linguagem natural para o domínio (LLM providers, tooling, ecossistema de IA), e é a mesma stack já dominada no `ai-research-agent`, reduzindo custo de contexto e permitindo reaproveitar princípios (não código).
- Pydantic v2 dá contratos tipados fortes para eventos, node I/O e a condition DSL — importante porque o engine depende de payloads JSON estruturados e precisamos de validação estrita nas fronteiras.
- SQLAlchemy Core/ORM dá controle explícito sobre transações, o que é crítico para persistência de eventos com sequência monotônica e para checkpointing.

**Persistência V1: SQLite.**

Justificativa técnica (não é "porque é mais simples", é porque atende os requisitos reais de V1):
- V1 é single-process, single-writer por execução. SQLite com modo WAL suporta múltiplos leitores + um escritor concorrente, o que é suficiente porque o engine serializa escritas por `execution_id` (ver §8 Concorrência).
- SQLite dá transações ACID reais — necessário para o par (event append + state transition) ser atômico, que é o requisito mais importante de todo o sistema de durabilidade.
- Zero infraestrutura externa: portfolio roda com `sqlite3` embutido, sem exigir Docker/Postgres para avaliação por terceiros — isso tem valor real de portfólio (fricção zero para quem for rodar o projeto).
- Postgres é adiado para roadmap explicitamente quando houver necessidade real de multi-processo/worker distribuído (que está fora do escopo V1). Trocar de SQLite→Postgres deve ser transparente porque a camada de persistência será uma interface (`EventStore`, `ExecutionRepository`), não SQL espalhado pelo domínio.

**API: FastAPI.** CLI: `typer` ou `click` (decisão final na Fase 1). Frontend: fora do escopo da Fase 0; decisão de framework adiada.

**Fila de retry/timeout V1: nenhuma fila externa.** Um scheduler in-process (loop/poller) que varre `NodeExecution` com `next_retry_at <= now` ou `WAITING` expirados. Justificado porque V1 é single-node e explicitamente exclui Kafka/RabbitMQ/workers distribuídos.

---

## 2. Domínio — Entidades e responsabilidades

Princípio orientador: cada entidade tem uma única responsabilidade; nenhuma entidade conhece a camada de persistência ou de transporte (API/CLI). O domínio é puro (Pydantic models + funções), a orquestração fica no `Engine`.

### 2.1 Definição do workflow (estático, versionado, imutável após publicação)

```
WorkflowDefinition
  - id: WorkflowDefinitionId
  - version: int                  # imutabilidade por versão; nova versão = novo registro
  - name: str
  - nodes: list[WorkflowNode]
  - edges: list[WorkflowEdge]
  - start_node_id: NodeId
  - created_at: datetime
  - checksum: str                 # hash do grafo, usado para detectar drift em replay

WorkflowNode
  - id: NodeId                    # único dentro da definição
  - type: NodeType                # enum: START, LLM, TOOL, CONDITION, TRANSFORM, HUMAN_APPROVAL, END
  - config: dict                  # validado por schema específico do NodeType (via registry)
  - retry_policy: RetryPolicy | None
  - timeout_policy: TimeoutPolicy | None

WorkflowEdge
  - id: EdgeId
  - source_node_id: NodeId
  - target_node_id: NodeId
  - condition: EdgeCondition | None   # None = edge incondicional; usado para branching de ConditionNode
```

Responsabilidade: descrever a *forma* do workflow. Não sabe nada de execução, estado, ou histórico. É validado estruturalmente na criação (ver §5 validação do grafo) e depois é imutável (append-only por versão) — isso é o que torna replay possível.

### 2.2 Execução (dinâmico, uma instância por "rodada" do workflow)

```
Execution
  - id: ExecutionId
  - workflow_definition_id: WorkflowDefinitionId
  - workflow_version: int
  - state: ExecutionState           # enum, ver §3
  - context: ExecutionContext
  - current_node_ids: list[NodeId]  # frontier de execução (>1 só se houver fan-out futuro; V1 = 1)
  - created_at, updated_at: datetime
  - version: int                    # optimistic concurrency (compare-and-swap em updates)

ExecutionContext
  - variables: dict[str, Any]       # dados produzidos por nodes, acumulados; namespaced por node_id
  - trigger_input: dict             # payload inicial, imutável
  # ExecutionContext é dados puros (serializável), NUNCA contém lógica de execução ou I/O.

NodeExecution
  - id: NodeExecutionId
  - execution_id: ExecutionId
  - node_id: NodeId
  - attempt: int                    # 1-based, incrementa a cada retry
  - status: NodeExecutionStatus     # PENDING, RUNNING, WAITING, COMPLETED, FAILED, SKIPPED
  - input: dict
  - output: dict | None
  - error: NodeExecutionError | None
  - idempotency_key: str            # ver §9
  - started_at, finished_at: datetime | None
  - next_retry_at: datetime | None
```

Responsabilidade de cada peça:
- `Execution` sabe *onde* o workflow está (estado macro, frontier) — não sabe *como* um node roda.
- `ExecutionContext` é só dados — não tem métodos de execução, evitando que vire um "god object" que mistura estado com comportamento.
- `NodeExecution` é o registro de uma tentativa concreta de rodar um node — permite múltiplas tentativas (retries) sem perder histórico, e é a unidade de idempotência.

### 2.3 Eventos e Aprovação

```
Event
  - event_id: EventId (UUID)
  - execution_id: ExecutionId
  - sequence: int                  # monotônico POR execution_id — ver §7
  - event_type: EventType
  - node_id: NodeId | None
  - payload: dict
  - created_at: datetime

ApprovalRequest
  - approval_id: ApprovalId
  - execution_id: ExecutionId
  - node_id: NodeId
  - node_execution_id: NodeExecutionId
  - status: ApprovalStatus          # PENDING, APPROVED, REJECTED
  - requested_at: datetime
  - resolved_at: datetime | None
  - resolved_by: str | None         # placeholder — sem RBAC em V1, só identificador livre
  - decision_payload: dict | None   # comentário/motivo opcional
```

### 2.4 Políticas (imutáveis, reutilizáveis, sem I/O)

```
RetryPolicy
  - max_attempts: int
  - backoff: BackoffStrategy         # FIXED | EXPONENTIAL
  - base_delay_seconds: float
  - max_delay_seconds: float
  - retry_on: list[ErrorCategory]    # ex: [TRANSIENT] — não retry em ValidationError

TimeoutPolicy
  - duration_seconds: int
  - on_timeout: TimeoutAction        # FAIL | RETRY

ExecutionPolicy
  - max_execution_duration_seconds: int | None
  - on_node_failure: FailurePropagation   # FAIL_EXECUTION (V1 único suportado)
```

**Decisão de responsabilidade — onde vive o retry:** o **Engine** é dono do retry, não o `LLMProvider`/`Tool`. Motivo: retry precisa ser observável via eventos (`node_retrying`), respeitar `RetryPolicy` declarada no node (não no provider), e coexistir com timeout e idempotency — centralizar isso no engine evita duplicidade de política entre providers diferentes. O provider apenas classifica o erro (`ErrorCategory.TRANSIENT` vs `PERMANENT`) para o engine decidir se retry se aplica.

### 2.5 Diagrama de relações (alto nível)

```
WorkflowDefinition 1───* WorkflowNode
WorkflowDefinition 1───* WorkflowEdge
WorkflowDefinition 1───* Execution
Execution 1───* NodeExecution
Execution 1───* Event
NodeExecution 0..1───1 ApprovalRequest   (apenas para HumanApprovalNode)
WorkflowNode 0..1───1 RetryPolicy
WorkflowNode 0..1───1 TimeoutPolicy
```

Nenhuma entidade de execução referencia objetos de definição por ponteiro vivo — só por ID + `workflow_version`, garantindo que a definição possa evoluir sem quebrar execuções em andamento (imutabilidade referencial).

---

## 3. State Machine

### 3.1 Estados de `Execution`

```
PENDING → RUNNING
RUNNING → WAITING
RUNNING → COMPLETED
RUNNING → FAILED
RUNNING → CANCELLED
WAITING → RUNNING
WAITING → CANCELLED
WAITING → FAILED        # timeout de aprovação, se configurado
```

Estados terminais: `COMPLETED`, `FAILED`, `CANCELLED`. **Nenhuma transição sai de um estado terminal.** Isso é um invariante de banco (constraint lógica aplicada na camada de repositório, não só no domínio) — toda transição de estado é um UPDATE condicional (`WHERE state = <estado_esperado> AND version = <version_esperada>`), nunca um UPDATE incondicional.

### 3.2 Estados de `NodeExecution`

```
PENDING → RUNNING
RUNNING → COMPLETED
RUNNING → FAILED
RUNNING → WAITING          # somente HumanApprovalNode
WAITING → RUNNING          # após approve/reject
FAILED → PENDING           # nova tentativa (novo NodeExecution com attempt+1, não reuso do mesmo registro)
PENDING → SKIPPED          # branch não tomado por ConditionNode
```

Nota de design: um retry **não reabre** o `NodeExecution` que falhou — cria um novo registro com `attempt = anterior + 1`. Isso preserva histórico completo e imutável de cada tentativa (auditabilidade), em vez de mutar um registro existente.

### 3.3 Perguntas críticas — respostas de design

**O que acontece se uma execução `COMPLETED` receber um approve?**
Rejeitado com erro de domínio (`InvalidTransitionError`), sem side-effect. A operação de approve carrega `execution_id` + `approval_id`; o handler carrega a `ApprovalRequest` atual e valida `status == PENDING` **e** `Execution.state == WAITING` antes de aplicar. Se a execução já terminou, a approval associada já deveria ter sido marcada como órfã (ver abaixo) — mas a checagem dupla é a garantia real.

**O que acontece se duas aprovações chegarem (dupla aprovação / corrida)?**
`ApprovalRequest` tem um UPDATE condicional: `UPDATE approval_requests SET status='APPROVED' WHERE approval_id=? AND status='PENDING'`. Apenas a primeira chamada afeta uma linha; a segunda recebe 0 rows afetadas → engine retorna `AlreadyResolvedError` (idempotente do ponto de vista do chamador: mesmo resultado final, sem duplo avanço do workflow). Isso é compare-and-swap otimista, sem lock explícito.

**O que acontece se um workflow `WAITING` for cancelado?**
Transição válida (`WAITING → CANCELLED`). A `ApprovalRequest` pendente associada é marcada como órfã/expirada (status seu próprio, não reaproveita `REJECTED` para não confundir "humano rejeitou" com "sistema cancelou"). Nenhum node subsequente é agendado.

**O que acontece se o processo morrer durante uma transição?**
Toda transição de estado é persistida na mesma transação SQL que o evento que a causou (event append + state update atômicos — ver §8 Durabilidade). Se o processo morre entre "node terminou de executar" e "persistir resultado", o efeito do node (ex: chamada de LLM) pode ter ocorrido mas não foi registrado — na recuperação, esse `NodeExecution` permanece `RUNNING` indefinidamente sem heartbeat. Recovery trata isso via **timeout de heartbeat**: todo `NodeExecution` tem `started_at`; se `RUNNING` há mais tempo que `timeout_policy` (ou um teto default de segurança), é considerado órfão e tratado como falha transitória sujeita a retry — nunca reexecutado silenciosamente sem passar pela política de retry/idempotency.

**O que acontece se um retry chegar depois da execução já ter terminado?**
O scheduler de retry age sobre `NodeExecution.next_retry_at`, mas antes de executar, **relê o estado atual da `Execution`**. Se `Execution.state` não é mais `RUNNING` (ou o `NodeExecution` não é mais o mais recente attempt pendente), o retry é descartado como no-op e logado como evento `retry_skipped_stale`. Isso é o que torna retries seguros mesmo com scheduler at-least-once.

### 3.4 Invariantes

- Um `Execution` tem no máximo uma `ApprovalRequest` com `status=PENDING` por `node_id` ativo.
- `sequence` de `Event` é estritamente crescente e sem buracos por `execution_id`.
- Toda transição de `Execution.state` gera exatamente um `Event` correspondente, na mesma transação.
- `NodeExecution.attempt` nunca é reescrito; é write-once.

---

## 4. Workflow Graph

**Decisão V1: DAG estrito, sem loops.** Ciclos são rejeitados na validação do grafo. Loops controlados (ex: "repita até condição" ou "retry manual de um subgrafo") ficam documentados como limitação de V1 e vão para roadmap como `LoopNode`/`SubworkflowNode` — a introdução de loops exige decisões adicionais (limite de iterações, detecção de loop infinito, custo de LLM descontrolado) que merecem uma fase própria de design, não devem ser encaixadas apressadamente aqui.

### 4.1 Validação do grafo (na criação de `WorkflowDefinition`, antes de persistir)

1. Exatamente um `START` node; alcançável a partir dele, todos os outros nodes.
2. Pelo menos um `END` node alcançável.
3. Nenhum ciclo (validação topológica — Kahn's algorithm ou DFS com detecção de back-edge).
4. Todo `NodeId` referenciado em `WorkflowEdge` existe em `nodes`.
5. `ConditionNode` deve ter ≥2 edges de saída, cada uma com `condition` não-nula, exceto opcionalmente uma edge "default"/else (no máximo uma edge sem condição saindo de um ConditionNode, avaliada por último).
6. Nodes que não são `ConditionNode` têm no máximo 1 edge de saída incondicional (sem branching implícito fora de `ConditionNode`).
7. Nenhum node órfão (sem edge de entrada, exceto START; sem edge de saída, exceto END).
8. `START` não pode ter edges de entrada — nada precede o ponto de entrada. *(adicionado na Fase 2; lacuna da Fase 1)*
9. `END` não pode ter edges de saída — é um nó terminal de fato, não só "reachável". *(adicionado na Fase 2; lacuna identificada na transição Fase 1 → Fase 2: a regra 7 só exigia saída para não-END, nunca proibiu explicitamente saída em END)*
10. Duas edges com o mesmo par `(source_node_id, target_node_id)` são rejeitadas como ambíguas, mesmo com `id` diferente — nunca deduplicadas silenciosamente. *(adicionado na Fase 2)*

Essa validação roda inteiramente em memória sobre a definição antes de qualquer persistência — é pura função `validate_graph(definition) -> list[GraphError]`.

---

## 5. Condition Engine (DSL segura)

**Proibido:** `eval`, `exec`, qualquer execução arbitrária de código do usuário.

### 5.1 Estrutura

```json
{
  "field": "risk_score",
  "operator": "gte",
  "value": 80
}
```

Composição via `and`/`or` explícitos (não aninhamento implícito):

```json
{
  "all": [
    {"field": "risk_score", "operator": "gte", "value": 80},
    {"field": "region", "operator": "eq", "value": "BR"}
  ]
}
```
ou `{"any": [...]}`.

### 5.2 Operadores V1

`eq`, `neq`, `gt`, `gte`, `lt`, `lte`, `contains`, `exists`.

### 5.4 Branch selection em runtime (Fase 5)

Quando um `CONDITION` node tem múltiplas edges cujas condições avaliam `true` simultaneamente contra o mesmo contexto, o Engine escolhe a **primeira, em ordem de declaração** na lista `edges` da `WorkflowDefinition` — nunca a "mais específica" nem uma escolhida por prioridade implícita. Isso é determinístico (mesma ordem de declaração → mesmo resultado sempre) e explícito: o autor do workflow controla a prioridade apenas pela ordem em que declara as edges. Se nenhuma condição bate e não há edge default, o Engine levanta `NoMatchingBranchError` — nunca escolhe uma edge arbitrária nem falha silenciosamente.

Avaliação: suficiente para V1. Operadores como regex ou expressões aritméticas são explicitamente fora de escopo (aumentam superfície de insegurança e complexidade sem agregar valor demonstrativo proporcional). `contains` opera em string/list; `exists` verifica presença de chave em `ExecutionContext.variables` independente do valor (inclusive `None` conta como "exists").

### 5.3 Propriedades garantidas

- **Determinístico**: mesma entrada + mesmo contexto → mesmo resultado, sempre (sem chamadas de rede, sem `now()` implícito).
- **Seguro**: parser apenas interpreta um schema Pydantic fechado (`ConditionExpr` com `Literal` para operadores) — não há caminho para código arbitrário.
- **Tipado**: `field` resolve via dotted-path dentro de `ExecutionContext.variables` (ex: `"classification.risk_score"`); tipo de `value` é validado contra o tipo do operador (ex: `gte` exige numérico).
- **Testável**: função pura `evaluate(expr: ConditionExpr, context: ExecutionContext) -> bool`, sem side-effects, 100% cobrível por testes unitários tabulares.
- Toda avaliação gera um `Event` tipo `condition_evaluated` com o resultado — importante para debugging de branching e para replay.

---

## 6. Node Contract

### 6.1 Contrato

```
Node.execute(input: NodeInput, context: ExecutionContext) -> NodeResult
```

`NodeResult` é um envelope, não um valor cru:

```
NodeResult
  - status: Literal["completed", "failed", "waiting"]
  - output: dict | None
  - context_patch: dict            # merge explícito no ExecutionContext (não mutação direta)
  - error: NodeExecutionError | None
  - events: list[EventDraft]       # eventos de domínio específicos do node (ex: condition_evaluated)
```

Decisões:
- **Sem mutação direta de `ExecutionContext`.** O node retorna um `context_patch`; o Engine aplica o merge. Isso evita que um node tenha acesso amplo ao contexto de outros nodes e mantém o merge auditável/logável em um único ponto.
- **Idempotency** é responsabilidade dupla: o node declara uma `idempotency_key` derivável deterministicamente do seu `input` (ex: hash de input); o Engine garante que, para o mesmo `(execution_id, node_id, idempotency_key)`, o node não é executado duas vezes com efeito colateral real — ao reexecutar (retry) com a mesma key, se já existe um `NodeExecution` `COMPLETED` com essa key, o resultado é reaproveitado sem nova chamada externa. Isso é crítico para nodes que chamam LLMs/tools com custo/efeito real.
- **Timeout** é aplicado pelo Engine ao redor da chamada de `execute` (via policy do node), não implementado individualmente por cada node.
- **Erros** são tipados (`NodeExecutionError` com `category: ErrorCategory` — `TRANSIENT | PERMANENT | VALIDATION`), permitindo ao Engine decidir retry sem inspecionar strings de exceção.

### 6.2 Registry (evitar if/elif por tipo)

```
NodeExecutorRegistry
  - register(node_type: NodeType, executor: NodeExecutor)
  - resolve(node_type: NodeType) -> NodeExecutor
```

O Engine nunca faz `if node.type == "llm"`. Ele resolve o `NodeExecutor` via registry na inicialização (cada tipo de node se registra) e chama `executor.execute(input, context)` polimorficamente. Isso é o padrão strategy/plugin interno pedido — novos tipos de node se registram sem tocar no Engine.

---

## 7. LLM Provider Abstraction

```
LLMProvider
  - generate(request: LLMRequest) -> LLMResponse

LLMRequest
  - messages: list[Message]
  - response_schema: dict | None      # para structured output (JSON schema)
  - max_tokens, temperature, etc.

LLMResponse
  - content: str | dict               # dict se response_schema foi satisfeito
  - usage: TokenUsage                 # prompt_tokens, completion_tokens
  - raw_error: LLMError | None
```

- Implementações futuras: `MockLLMProvider` (determinístico, para testes e demos sem custo) e `OpenAIProvider`/equivalente Anthropic — **não implementadas nesta fase**.
- **Retry pertence ao Engine, não ao Provider** (reafirmando §2.4): o provider apenas retorna/lança um erro classificado (`LLMError.category`); nunca implementa backoff internamente, para não duplicar a política já definida em `RetryPolicy` do node. O provider é responsável só por timeout de rede baixo nível (proteção técnica), não por timeout de negócio (que é do `TimeoutPolicy`).
- `LLMNode` (o node type) depende de `LLMProvider` (a abstração), nunca de uma implementação concreta — injeção via `NodeExecutor` construído com o provider.

---

## 8. Tool Abstraction

```
Tool
  - name: str
  - description: str
  - input_schema: dict         # JSON schema
  - output_schema: dict
  - timeout_policy: TimeoutPolicy
  - execute(input: dict) -> ToolResult

ToolResult
  - status: Literal["success", "error"]
  - output: dict | None
  - error: ToolError | None

ToolRegistry
  - register(tool: Tool)
  - resolve(name: str) -> Tool
```

Idempotência de tools é responsabilidade do próprio `Tool` (ex: uma tool que chama uma API externa deve, se possível, expor uma operação idempotente ou aceitar uma idempotency key repassada pelo Engine) — o Engine não pode garantir idempotência de efeitos fora de seu controle, só evita *re-chamar* uma tool já bem-sucedida (mesmo mecanismo de idempotency key do §6.1). `ToolNode` depende de `ToolRegistry`, nunca de tools concretas.

---

## 9. Human-in-the-loop

### 9.1 Fluxo

```
HumanApprovalNode.execute() é chamado
  → cria ApprovalRequest (status=PENDING), persiste na mesma transação que:
  → NodeExecution.status = WAITING
  → Execution.state = WAITING
  → Event(approval_requested)
```

Resume:
```
approve(approval_id, decision, resolved_by) ou reject(...)
  → UPDATE condicional ApprovalRequest WHERE status='PENDING'  (ver §3.3, CAS)
  → se sucesso: NodeExecution.status = RUNNING → COMPLETED (approve) ou FAILED (reject, se reject = falha do node)
  → Execution.state = RUNNING
  → Event(approval_approved | approval_rejected)
  → Engine agenda avanço para os próximos nodes conforme edges
```

### 9.2 Decisões

- **Reject não é necessariamente falha de execução** — é uma saída de dados do `HumanApprovalNode` (`output = {"decision": "rejected"}`), e o grafo pode ter uma edge condicional a partir dele tratando reject como um branch válido (ex: reject → END com status "not approved"), em vez de forçar `Execution.state = FAILED`. Isso é mais correto semanticamente: rejeição é um resultado de negócio, não um erro técnico. `FAILED` fica reservado para falhas técnicas/exhaustão de retries.
- **Quem pode aprovar**: V1 não implementa RBAC. `resolved_by` é um campo livre (string) — ex: e-mail ou username informado pelo chamador da API — sem autorização real. Documentado como limitação explícita; roadmap: RBAC.
- **Idempotência da aprovação**: garantida pelo CAS em `ApprovalRequest.status` (§3.3).
- **Concorrência**: coberta pelo mesmo CAS — dupla aprovação simultânea resulta em uma única aplicação efetiva.
- **Aprovação após cancelamento**: `Execution.state != WAITING` → aprovação rejeitada com erro de domínio (execução não está mais aguardando).
- **Aprovação após completion**: mesma checagem — `ApprovalRequest.status` já não é `PENDING` nesse ponto de qualquer forma (foi resolvida ou expirada antes do término), então o CAS já barra isso estruturalmente.
- **Timeout de aprovação**: opcional via `TimeoutPolicy` no `HumanApprovalNode` — se configurado e expirado, scheduler transiciona `WAITING → FAILED` (ou a uma edge de timeout, se modelada) automaticamente, gerando `Event(execution_failed, reason=approval_timeout)`.

---

## 10. Persistence

Confirmando §1: **SQLite (modo WAL) para V1**, acessado via SQLAlchemy com uma camada de repositório (`ExecutionRepository`, `EventStore`, `WorkflowDefinitionRepository`) que abstrai SQL do domínio — troca futura para Postgres não deve tocar o domínio nem o Engine.

Tabelas (visão lógica, não DDL final):
- `workflow_definitions`, `workflow_nodes`, `workflow_edges` (append-only por versão)
- `executions` (mutável, com `version` para optimistic locking)
- `node_executions` (append-only por attempt)
- `events` (append-only, `UNIQUE(execution_id, sequence)`)
- `approval_requests` (mutável só no campo `status`/`resolved_*`)

**Concorrência**: escritas em uma mesma `execution_id` são serializadas logicamente pelo Engine (um "worker" lógico por execução em V1, já que não há execução paralela). Leituras (API consultando status) usam WAL para não bloquear o escritor. Onde múltiplos processos possam colidir (ex: dois requests de approve simultâneos, ou API + scheduler), o optimistic locking (`version` em `Execution`, CAS em `ApprovalRequest.status`) é a defesa real — não confiar apenas em "só um processo escreve".

---

## 11. Events

### 11.1 Catálogo (V1)

```
execution_started, execution_completed, execution_failed, execution_cancelled, execution_resumed
node_started, node_completed, node_failed, node_retrying, node_skipped
condition_evaluated
approval_requested, approval_approved, approval_rejected, approval_expired
```

### 11.2 Schema

```
Event
  - event_id: UUID
  - execution_id: ExecutionId
  - sequence: int            # monotônico por execution_id, sem buracos
  - event_type: EventType
  - node_id: NodeId | None
  - payload: dict
  - created_at: datetime (UTC)
```

**Sequência monotônica**: sim, necessária. É a coluna que torna o event log totalmente ordenável e é o que viabiliza replay determinístico (§12) — sem ela, reconstruir o estado a partir dos eventos fica sujeito a ambiguidade de ordem quando timestamps colidem (comum em SQLite com resolução de tempo mais baixa). Implementação: sequência obtida via `SELECT COALESCE(MAX(sequence), 0) + 1` dentro da mesma transação que insere o evento, com a transação de nível `SERIALIZABLE` (ou lock explícito de linha da `Execution`) para evitar corrida entre dois eventos concorrentes na mesma execução — mas como escritas por execução já são serializadas pelo Engine (§10), isso é defesa em profundidade, não o mecanismo primário.

---

## 12. Durability & Recovery

### 12.1 Cenário do prompt

```
NODE A ✓
NODE B ✓
NODE C iniciou
PROCESS CRASH
```

### 12.2 Mecanismo de recuperação

1. **Checkpoint = o próprio event log + estado persistido de `Execution`/`NodeExecution`.** Não há um "checkpoint" separado — cada transição de estado já É o checkpoint, porque é persistida atomicamente com seu evento antes do Engine seguir adiante. Isso é a decisão central de durabilidade: o Engine nunca avança em memória sem antes ter persistido o resultado do passo anterior.
2. Ao reiniciar, um processo de **recovery scan** roda: busca todas as `Execution` com `state IN (RUNNING, WAITING)`.
3. Para cada uma, olha o `NodeExecution` mais recente:
   - Se `status = COMPLETED`: o próximo passo (avaliar edges, agendar próximo node) simplesmente não tinha sido feito ainda — Engine recalcula a partir do estado persistido (idempotente, porque decidir "qual o próximo node" é uma função pura do grafo + contexto já persistido).
   - Se `status = RUNNING` e `started_at` mais antigo que o teto de heartbeat/timeout: tratado como órfão (§3.3) — vira `FAILED` com `error.category = TRANSIENT`, elegível a retry conforme `RetryPolicy` do node (novo `NodeExecution` attempt+1).
   - Se `status = WAITING` (aprovação pendente): nada a fazer, continua aguardando — não é um erro, é o comportamento esperado de durabilidade (a aprovação sobrevive ao crash porque está no banco).
4. Nenhuma reexecução ocorre sem passar pela checagem de idempotency key (§6.1) — se o `NodeExecution` órfão já tinha, por acaso, persistido um `output` antes de morrer (crash *depois* de obter resultado mas testando a gravação), o retry detecta a key já satisfeita e não re-chama o efeito externo.

### 12.3 Garantia oferecida

**At-least-once execution de side-effects, com proteção de idempotency key reduzindo a exposição a duplicação real.** Não há pretensão de exactly-once (explicitamente fora de escopo, conforme brief). Isso é documentado como uma limitação honesta, não escondida.

---

## 13. Replay

**Replay = reconstrução do estado de uma `Execution` a partir do zero, reproduzindo o `Event` log em ordem de `sequence`, sobre o `ExecutionContext` inicial (`trigger_input`).**

Viabilidade:
- Nodes **determinísticos** (`TransformNode`, `ConditionNode`) são 100% replayáveis: reexecutar a lógica pura sobre os mesmos inputs reproduz o mesmo output.
- Nodes com **efeito externo** (`LLMNode`, `ToolNode`) não são replayáveis "de verdade" (uma nova chamada ao LLM pode gerar resposta diferente) — para esses, replay usa o `output` **já persistido no evento/NodeExecution**, não uma nova chamada. Ou seja, replay em V1 é definido como: **reconstrução de estado a partir do histórico de eventos (sempre determinístico, porque lê outputs já gravados)**, e não como "reexecução ao vivo do workflow". Essa distinção é importante e deve ficar clara na documentação do projeto — replay ≠ reexecução.
- Replay serve para: (a) debugging/auditoria (reconstruir "o que aconteceu passo a passo"), (b) verificação de integridade (comparar checksum do grafo em cada versão), (c) potencialmente popular um novo `Execution` a partir de um ponto anterior no roadmap (não em V1).
- Limitação documentada: replay ao vivo (re-chamar LLMs/tools e potencialmente divergir) fica fora de V1.

---

## 14. Interfaces (contratos de alto nível, sem implementação)

- **CLI**: comandos para `create workflow`, `start execution`, `get execution <id>`, `approve <approval_id>`, `reject <approval_id>`, `list events <execution_id>`. Fino — chama a mesma camada de serviço que a API.
- **API (FastAPI)**: REST sobre os mesmos casos de uso (`POST /workflows`, `POST /executions`, `GET /executions/{id}`, `POST /approvals/{id}/approve`, `GET /executions/{id}/events`). Nenhuma lógica de domínio na camada de rota — só validação de request/response e chamada ao serviço de aplicação.
- **Frontend visual**: fora de escopo desta fase; será consumidor puro da API (visualização do grafo + estado da execução + timeline de eventos + botão approve/reject). Decisão de framework adiada para depois da Fase 0.

Camada de aplicação (entre interfaces e domínio): `WorkflowService`, `ExecutionService` — orquestram repositórios + Engine, e são o único ponto que CLI/API tocam.

---

## 15. O que fica fora da V1 (roadmap explícito)

Kubernetes, Kafka/RabbitMQ, workers distribuídos, multi-região, multi-tenancy, billing, marketplace de plugins, RBAC complexo, scaling horizontal, exactly-once, scheduler distribuído/cron distribuído, execução paralela (fan-out/fan-in), loops no grafo, replay ao vivo com re-invocação de LLM/tools, Postgres (troca de storage).

Cada um desses é uma decisão consciente de escopo, não uma omissão — o valor de portfólio está em fazer bem o que está em escopo (state machine correta, durabilidade real, HITL funcional, observabilidade via eventos), não em cobertura de features.

---

## 16. Plano de implementação (Fase 1 em diante — ainda sem código nesta entrega)

1. **Fase 1 — Domínio puro**: modelos Pydantic (entidades §2), state machine (§3) com testes exaustivos das transições e invariantes, condition engine (§5) com testes tabulares. Zero I/O, zero framework.
2. **Fase 2 — Persistência**: schema SQLite, repositórios, `EventStore` com sequência monotônica, testes de concorrência (CAS, optimistic locking).
3. **Fase 3 — Engine core**: loop de execução sobre o grafo (DAG), registry de node executors, aplicação de `context_patch`, integração com retry/timeout/idempotency.
4. **Fase 4 — Node types concretos**: `StartNode`, `EndNode`, `TransformNode`, `ConditionNode` primeiro (determinísticos, fáceis de testar); depois `LLMNode` (com `MockLLMProvider`), `ToolNode` (com tools mock), `HumanApprovalNode`.
5. **Fase 5 — Recovery**: recovery scan (§12), testes simulando crash (matar processo no meio de um `NodeExecution`).
6. **Fase 6 — API + CLI**: expor casos de uso.
7. **Fase 7 — Observabilidade**: consulta de eventos, endpoint de timeline.
8. **Fase 8 — Frontend**: visualização do grafo + estado + timeline (decisão de stack nesse momento).
9. **Fase 9 — Provider real** (ex: Anthropic) substituindo o mock, com um exemplo de workflow completo (o do brief: AI Analysis → Classification → Condition → Human Approval → Generate Report).

Cada fase deve fechar com testes antes de avançar — a demonstração de rigor de engenharia é o produto tanto quanto o software em si.

---

## 17. Reliability semantics (Fases 5–9, consolidado)

**V1 não garante exactly-once execution.** A garantia real é **at-least-once para efeitos colaterais de node, exactly-once para transições de estado persistidas** (via CAS). Abaixo, a semântica exata por trás de cada pergunta que costuma aparecer numa revisão de confiabilidade:

1. **O que acontece se o processo morrer durante um node?** O `NodeExecution` fica `RUNNING` no banco, sem conclusão. `find_orphaned_node_executions` (Fase 7) o detecta por heartbeat (`started_at` mais antigo que o timeout configurado) e `mark_orphaned_as_failed` grava um **novo** registro `FAILED(TRANSIENT)` — nunca muta o `RUNNING` original. A `Execution` continua `RUNNING`; decidir se/como reexecutar é explicitamente do retry runtime (Fase 8), não da recuperação.
2. **O que acontece se a mesma execution for resumida duas vezes?** `ExecutionEngine.run()`/`resume()` só aceitam `Execution` em `PENDING`/`RUNNING` (run) ou `WAITING` com aprovação `PENDING` pertencente ao node certo (resume). Uma segunda tentativa contra uma execução já avançada é rejeitada pela própria state machine do domínio antes de qualquer escrita — `PersistentExecutionEngine` reforça isso com CAS na camada de persistência como segunda linha de defesa.
3. **O que acontece se dois workers tentarem atualizar a mesma execution?** `ExecutionRepository.update_cas`/`ApprovalRepository.update_cas` fazem `UPDATE ... WHERE version = ?`; quem perder a corrida recebe `ConcurrencyConflictError` e sua transação inteira é revertida (nunca aplicação parcial). Dentro de um único processo, `ExecutionEngine._resolved_approval_ids` adiciona uma segunda barreira mais barata para o caso de aprovação.
4. **O que acontece se um approval for aprovado duas vezes?** A segunda chamada falha: primeiro pela guarda de processo (`_resolved_approval_ids`), depois pela precondição do domínio (`ApprovalRequest.status == PENDING`), e por fim pelo CAS persistido — três camadas independentes, qualquer uma sozinha já seria suficiente.
5. **O que acontece se um retry ocorrer após timeout?** V1 não tem scheduler real: retry acontece sincronamente dentro do mesmo `_loop`, até `max_attempts`. `next_retry_at` é calculado e registrado (auditável), mas o Engine nunca "espera" por ele — não existe um retry tardio de verdade para "ocorrer após o timeout" nesta versão; isso é uma limitação documentada, não um comportamento escondido.
6. **O que acontece se o event sequence estiver corrompido?** `engine.replay.validate_event_sequence` detecta gaps, duplicatas e mistura de `execution_id` e levanta `ReplayError` — nunca produz um estado "parece válido" a partir de histórico inconsistente (testado explicitamente em `test_replay.py`).
7. **Replay chama alguma API externa?** Não. `engine/replay.py` não importa `ExecutionEngine`, `LLMProvider`, `Tool`, SQLAlchemy, nem qualquer biblioteca de rede — verificado por AST em teste, não só por convenção.
8. **O sistema promete exactly-once?** Não, explicitamente. Ver abertura desta seção.
9. **Qual é a semântica real de retry?** At-least-once por node: um retry pode repetir o efeito colateral de um `Tool`/`LLMProvider` real (os mocks de V1 não têm estado externo para deduplicar). `retry.derive_idempotency_key` gera uma chave estável por tentativa lógica — a interface está pronta para um provider real implementar deduplicação, mas isso não é implementado nem fingido em V1.
10. **Qual é a unidade transacional?** Uma chamada a `PersistentExecutionEngine.start/run/resume` = uma transação SQL: `Execution` (CAS) + todos os `NodeExecution` + todos os `Event` + `ApprovalRequest` (insert ou CAS) daquela chamada, tudo ou nada. O que acontece *dentro* do `ExecutionEngine` em memória (múltiplos nodes, múltiplas tentativas de retry) faz parte dessa mesma transação — não há commit parcial no meio de um `run()`.
