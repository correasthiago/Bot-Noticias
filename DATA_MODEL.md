# Modelo de dados — Central Universal V0.2

Schema completo em `central_universal/persistence/migrations/0001_init.sql`
(base) + `0002_v0_2_corrections.sql` (pacote de correcao pos Red Team).
Todas as tabelas sao `STRICT`. `foreign_keys=ON` e ativado por conexao em
`persistence/db.py`, nao e uma configuracao global do arquivo `.db`.

## Visao geral das entidades

| Tabela | Papel | Mutabilidade |
|---|---|---|
| `learner` | pessoa usando o sistema | mutavel |
| `learning_domain` | dominio de conhecimento (ex.: ingles) | mutavel |
| `competency` | um no do grafo de competencias | mutavel |
| `prerequisite_relation` | aresta `competency -> prerequisite` (ciclos bloqueados na escrita) | mutavel |
| `rule_version` | uma versao das regras pedagogicas, com `config_json`/`algorithm_version` | **imutavel (trigger SQL)** |
| `active_rule_version` | ponteiro singleton para a RuleVersion ativa agora | **mutavel (ponteiro)** |
| `learning_session` | uma sessao de estudo | mutavel (status/ended_at) |
| `activity` | uma atividade dentro de uma sessao, com `is_planned_recall` | mutavel |
| `evidence_cluster` | contexto de tentativa computado no SERVIDOR | mutavel (so `last_seen_at`) |
| `raw_interaction` | uma resposta bruta do aprendiz, com `evaluation_status` | **imutavel, exceto pending->completed (trigger SQL)** |
| `evidence_event` | FATOS observados de uma interacao (sem classificacao) | **imutavel (trigger SQL)** |
| `evidence_assessment` | classificacao/confianca/causa/RuleVersion de um evidence_event | **imutavel (trigger SQL)** |
| `projection_generation` | uma geracao da projecao CompetencyState | mutavel (`status`, ciclo de vida) |
| `active_projection_generation` | ponteiro singleton para a geracao ativa agora | **mutavel (ponteiro)** |
| `competency_state` | projecao derivada, append-only, marcada com `generation_id` | **imutavel/append-only (trigger SQL)** |
| `memory_state` | card FSRS "atual" de uma competencia | mutavel (1 linha por competencia, por design) |
| `memory_review_log` | historico de reviews FSRS | **imutavel (trigger SQL)** |
| `memory_observation` | evento explicito que autorizou uma revisao FSRS | **imutavel (trigger SQL)** |
| `decision_event` | uma decisao do Decisor, com justificativa | **imutavel (trigger SQL)** |
| `provider_event` | uma chamada a um fornecedor de IA | **imutavel (trigger SQL)** |
| `backup_event` | um backup executado | **imutavel (trigger SQL)** |

## Relacoes principais

```
learning_domain 1---N competency N---N competency   (via prerequisite_relation, sem ciclos)
learner 1---N learning_session 1---N activity 1---N raw_interaction
learning_session 1---N evidence_cluster (por sessao+contexto)
raw_interaction N---1 evidence_cluster
raw_interaction 1---N evidence_event 1---N evidence_assessment
competency 1---N evidence_event
competency 1---1 memory_state 1---N memory_review_log
competency 1---N memory_observation
projection_generation 1---N competency_state (append-only, uma "dimension" por vez, POR geracao)
rule_version 1---N evidence_assessment
rule_version 1---N competency_state
rule_version 1---N decision_event
rule_version 1---N projection_generation
```

## Por que `EvidenceAssessment.classification` NAO e um `CompetencyDimensionState`

Erro de modelagem comum que evitamos deliberadamente: o Avaliador (Secao
16) julga UMA evidencia (`positive` / `negative` / `contradictory` /
`inconclusive`), nunca declara o estado agregado da competencia
(`not_assessed` / `acquiring` / `demonstrated` / ...). O estado agregado e
SEMPRE calculado por `evidence.aggregation.classify_dimension` a partir de
MUITAS avaliacoes — nunca escrito diretamente por um avaliador individual
(Principio 2 e 8).

## Por que `EvidenceEvent` e `EvidenceAssessment` sao estritamente separados (v0.2)

Ate a V0.1, `EvidenceEvent` tinha uma coluna `evidence_type` que
duplicava, mecanicamente, o que `EvidenceAssessment.classification` ja
carregava - uma inconsistencia que o Red Team pos-entrega apontou. Desde
v0.2:

- `EvidenceEvent` registra so FATOS OBSERVADOS e condicoes da tentativa:
  `dimension`, `relation` (target/qualified_incidental/mere_presence),
  `help_level`, `production_result`, `evidence_cluster_id`. Nunca uma
  classificacao/julgamento.
- `EvidenceAssessment` registra a INTERPRETACAO: `classification`,
  `confidence`, `justification`, `alternative_cause`, `inconclusive`,
  `rule_version_id`. Um CHECK constraint garante
  `(classification='inconclusive') <=> (inconclusive=1)`.

## `competency_state`: por que append-only E organizado em geracoes (v0.2)

Cada recomputo INCREMENTAL de `(competency_id, dimension)` insere uma
NOVA linha na geracao ATIVA; o "estado atual" e sempre a linha de
`computed_at` mais recente DENTRO da geracao ativa (nunca misturando
geracoes). Isso da, de graca:

- historico completo de como o estado evoluiu (Principio 3);
- recalculabilidade total SEM PERDA: `evidence.service.recompute_all_from_log`
  cria uma `ProjectionGeneration` NOVA, recalcula TUDO dentro dela numa
  transacao, valida, e SO ENTAO move o ponteiro `active_projection_generation`
  - a geracao anterior fica no banco para sempre, marcada `superseded`
  (Principio 6 e 10 do pacote de correcao v0.2).

A tabela em si e IMUTAVEL (trigger `BEFORE UPDATE`/`BEFORE DELETE`) desde
v0.2 - nenhuma linha de nenhuma geracao, ativa ou nao, pode ser alterada
ou apagada depois de inserida.

## Por que o cluster de evidencia e uma tabela propria, computada no servidor (v0.2, refinada em v0.2.1)

Ate a V0.1, `evidence_cluster_id` era um `TEXT` livre que o CHAMADOR
decidia (na pratica, o orquestrador usava `activity_id`, mas nada no
schema impedia outra coisa). O Red Team apontou que isso permite
"provar independencia" so inventando um id novo. Desde v0.2,
`evidence_cluster` e uma tabela real, com `UNIQUE(session_id,
context_signature)`; `raw_interaction.evidence_cluster_id` e
`evidence_event.evidence_cluster_id` sao FOREIGN KEY para ela - nenhum
codigo de aplicacao aceita mais um cluster id vindo de fora (ver
`evidence/clustering.py`).

`context_signature` era originalmente um hash de `(session_id,
activity_type, prompt normalizado)`. Uma segunda auditoria (pacote de
correcao v0.2.1) encontrou que isso ainda permitia "provar independencia"
trocando so palavras superficiais de um exercicio previsivel (mesmo
template, sujeito/verbo diferentes). O prompt saiu da assinatura:
`context_signature` agora e um hash de `(activity_type,
competency_targets ordenados)` - a MESMA acao pedagogica repetida para a
MESMA competencia, na mesma sessao, sempre colapsa num cluster so, seja
qual for o texto exato do prompt.

## Campos que merecem explicacao

- `raw_interaction.evaluation_status` (`pending`/`completed`) — permite
  reprocessar (reavaliar) uma interacao cuja avaliacao anterior falhou,
  sem duplicar evidencia se a avaliacao ja tiver completado. A transicao
  `pending -> completed` e feita por um `UPDATE ... WHERE
  evaluation_status = 'pending'` atomico
  (`RawInteractionRepository.try_claim_evaluation`) - e a UNICA excecao
  permitida pela trigger de imutabilidade de `raw_interaction`.
- `activity.is_planned_recall` — marca se esta atividade foi criada
  especificamente como uma tentativa de recuperacao espacada (e nao um
  estudo comum). E um dos requisitos para que a interacao gere um
  `MemoryObservation` (ver Secao 1-2 do pacote de correcao v0.2). Desde
  v0.2.1, e SEMPRE derivado por `SessionOrchestrator.start_activity` a
  partir de `action.decision_type == DecisionType.SCHEDULE_RECALL` -
  nunca um valor que um chamador (nem um teste) possa setar diretamente.
- `memory_observation` — o UNICO evento que autoriza
  `MemoryAdapter.observe_and_review` a chamar o FSRS. Guarda
  `planned_recall`, `interval_days` (desde a ultima revisao) e `rating`
  como um registro auditavel de POR QUE aquela revisao aconteceu.
- `competency_state.possible_regression` — marcador booleano
  INDEPENDENTE do enum de estado (Secao 7): nunca vira um valor do enum
  `CompetencyDimensionState`. Desde v0.2, NUNCA rebaixa o `state`
  automaticamente - e so um sinal, pendente de validacao deliberada.
- `competency_state.has_unresolved_contradiction` — permite ao Decisor
  (Secao 17, regra 7) diferenciar "sem evidencia ainda" de "evidencia
  ambigua", sem precisar re-consultar o event log inteiro a cada decisao.
- `raw_interaction.idempotency_key` — `UNIQUE`; e a chave que torna
  `EvidenceService.record_interaction` idempotente.
- `activity.competency_targets` — `TEXT` com um array JSON de ids. E a
  UNICA referencia a `competency` que o SQLite nao valida via
  `FOREIGN KEY` (JSON embutido); por isso
  `integrity.checks.check_dangling_references` confere isso
  explicitamente.
- `memory_state.fsrs_card_json` / `memory_review_log.fsrs_review_log_json`
  — serializacao COMPLETA (`Card.to_json()` / `ReviewLog.to_json()`) do
  Py-FSRS, nao so os campos usados hoje. Permite reconstruir/recalcular o
  scheduler inteiro no futuro (Secao 13).
- `rule_version.config_json` — a UNICA fonte de thresholds de agregacao
  (`AggregationConfig`); nada mais no codigo hardcoda esses numeros.
  `rule_version.algorithm_version` documenta qual forma de codigo sabe
  interpretar aquele JSON. Desde v0.2.1, tambem carrega a politica de
  revisao de memoria: `recall_min_confidence`,
  `recall_min_interval_seconds`, `recall_rating_easy_min_confidence` e
  `recall_rating_good_min_confidence` - os limiares que
  `memory.fsrs_adapter.derive_recall_rating`/`evaluate_recall_eligibility`
  usam para transformar a `EvidenceAssessment` de RETENTION de uma
  tentativa em elegibilidade + nota FSRS. Antes da v0.2.1 esses numeros so
  existiam como defaults implicitos da dataclass `AggregationConfig`;
  agora sao explicitos em `evidence.rule_versions.build_v0_2_1_rule_version`.
- `raw_interaction`/`activity`/`learner_input`/`help_level`/`production_result`
  identificam uma submissao junto com `idempotency_key`: desde v0.2.1,
  `EvidenceService.record_interaction` rejeita
  (`IdempotencyConflictError`) uma `idempotency_key` reutilizada com
  qualquer um desses campos diferente do que foi persistido da primeira
  vez. `tutor_output` deliberadamente NAO faz parte dessa checagem (nao e
  parte da identidade da submissao), mas tambem nunca e sobrescrito - a
  linha existente e sempre devolvida como esta.
- `schema_migrations.notes` (coluna nova em v0.2.1, adicionada sob
  demanda a bancos mais antigos) — diagnostico auditavel de uma migration
  especifica, calculado ANTES dela alterar qualquer coisa. Por exemplo,
  quantas linhas de `evidence_assessment` de um banco v0.1 precisaram ser
  normalizadas (nunca descartadas) para respeitar o novo `CHECK` de
  consistencia `inconclusive`/`classification` da migration 0002.

## Por que "versao ativa" e "geracao ativa" vivem em tabelas-ponteiro separadas

`rule_version` e `competency_state`/`projection_generation` sao
imutaveis/append-only. Se "qual versao/geracao esta ativa" fosse uma
coluna dessas proprias linhas, "ativar" uma nova exigiria dar UPDATE
numa linha antiga - proibido pela trigger. Por isso `active_rule_version`
e `active_projection_generation` sao tabelas SEPARADAS, de uma linha so
(`id=1`), deliberadamente mutaveis: sao bookkeeping/ponteiro, nao fato
historico.

## Datas

Todas as colunas de data/hora sao `TEXT` em ISO-8601 UTC
(`datetime.now(timezone.utc).isoformat()`), nunca hora local nem timestamp
Unix. A conversao para horario local acontece so na apresentacao (a
interface web pode formatar com JS do navegador; a V0 atual mostra o
ISO-8601 UTC cru nas telas de auditoria/competencia, o que e suficiente
para provar o motor mas e uma melhoria de UX pendente).
