# Modelo de dados — Central Universal V0.1

Schema completo em `central_universal/persistence/migrations/0001_init.sql`.
Todas as tabelas sao `STRICT`. `foreign_keys=ON` e ativado por conexao em
`persistence/db.py`, nao e uma configuracao global do arquivo `.db`.

## Visao geral das entidades

| Tabela | Papel | Mutabilidade |
|---|---|---|
| `learner` | pessoa usando o sistema | mutavel |
| `learning_domain` | dominio de conhecimento (ex.: ingles) | mutavel |
| `competency` | um no do grafo de competencias | mutavel |
| `prerequisite_relation` | aresta `competency -> prerequisite` | mutavel |
| `rule_version` | uma versao das regras pedagogicas | **imutavel apos criada** |
| `learning_session` | uma sessao de estudo | mutavel (status/ended_at) |
| `activity` | uma atividade dentro de uma sessao | mutavel |
| `raw_interaction` | uma resposta bruta do aprendiz | **imutavel (trigger SQL)** |
| `evidence_event` | evidencia classificada a partir de uma interacao | **imutavel (trigger SQL)** |
| `evidence_assessment` | interpretacao versionada de um evidence_event | **imutavel (trigger SQL)** |
| `competency_state` | projecao derivada, append-only | **append-only** |
| `memory_state` | card FSRS "atual" de uma competencia | mutavel (1 linha por competencia) |
| `memory_review_log` | historico de reviews FSRS | append-only |
| `decision_event` | uma decisao do Decisor, com justificativa | **imutavel** |
| `provider_event` | uma chamada a um fornecedor de IA | **imutavel** |
| `backup_event` | um backup executado | **imutavel** |

## Relacoes principais

```
learning_domain 1---N competency N---N competency   (via prerequisite_relation)
learner 1---N learning_session 1---N activity 1---N raw_interaction
raw_interaction 1---N evidence_event 1---N evidence_assessment
competency 1---N evidence_event
competency 1---1 memory_state 1---N memory_review_log
competency 1---N competency_state (append-only, uma "dimension" por vez)
rule_version 1---N evidence_assessment
rule_version 1---N competency_state
rule_version 1---N decision_event
```

## Por que `EvidenceAssessment.classification` NAO e um `CompetencyDimensionState`

Erro de modelagem comum que evitamos deliberadamente: o Avaliador (Secao
16) julga UMA evidencia (`positive` / `negative` / `contradictory` /
`inconclusive`), nunca declara o estado agregado da competencia
(`not_assessed` / `acquiring` / `demonstrated` / ...). O estado agregado e
SEMPRE calculado por `evidence.aggregation.classify_dimension` a partir de
MUITAS avaliacoes — nunca escrito diretamente por um avaliador individual
(Principio 2 e 8).

## `competency_state`: por que append-only

Cada recomputo de `(competency_id, dimension)` insere uma NOVA linha; o
"estado atual" e sempre a de `computed_at` mais recente. Isso da, de
graca:

- historico completo de como o estado evoluiu (Principio 3);
- recalculabilidade total: `evidence.service.recompute_all_from_log`
  apaga a tabela inteira e a reconstroi a partir de
  `raw_interaction + evidence_event + evidence_assessment + rule_version`
  (Principio 6, testado em T10).

## Campos que merecem explicacao

- `evidence_event.evidence_cluster_id` — Secao 11: respostas da MESMA
  atividade compartilham cluster (na pratica, `evidence_cluster_id =
  activity_id`), para que 10 respostas quase identicas nao contem como 10
  provas independentes.
- `competency_state.possible_regression` — marcador booleano
  INDEPENDENTE do enum de estado (Secao 7): nunca vira um valor do enum
  `CompetencyDimensionState`.
- `competency_state.has_unresolved_contradiction` — permite ao Decisor
  (Secao 17, regra 7) diferenciar "sem evidencia ainda" de "evidencia
  ambigua", sem precisar re-consultar o event log inteiro a cada decisao.
- `raw_interaction.idempotency_key` — `UNIQUE`; e a chave que torna
  `EvidenceService.record_interaction` idempotente (Secao 24, T8).
- `activity.competency_targets` — `TEXT` com um array JSON de ids. E a
  UNICA referencia a `competency` que o SQLite nao valida via
  `FOREIGN KEY` (JSON embutido); por isso
  `integrity.checks.check_dangling_references` confere isso
  explicitamente.
- `memory_state.fsrs_card_json` / `memory_review_log.fsrs_review_log_json`
  — serializacao COMPLETA (`Card.to_json()` / `ReviewLog.to_json()`) do
  Py-FSRS, nao so os campos usados hoje. Permite reconstruir/recalcular o
  scheduler inteiro no futuro (Secao 13).

## Datas

Todas as colunas de data/hora sao `TEXT` em ISO-8601 UTC
(`datetime.now(timezone.utc).isoformat()`), nunca hora local nem timestamp
Unix. A conversao para horario local acontece so na apresentacao (a
interface web pode formatar com JS do navegador; a V0 atual mostra o
ISO-8601 UTC cru nas telas de auditoria/competencia, o que e suficiente
para provar o motor mas e uma melhoria de UX pendente).
