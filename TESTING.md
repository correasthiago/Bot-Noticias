# Estrategia de testes — Central Universal V0.1

## Como rodar

```bash
python -m pytest -q
```

Resultado atual: **75 testes, 75 passando, 0 falhando**, tempo total
< 1s, 100% offline (nenhum teste faz chamada de rede — `MockProvider` e
usado em todos os cenarios que envolvem "IA").

## Camadas de teste

| Arquivo | O que cobre | Testes |
|---|---|---|
| `test_persistence.py` | foreign keys, migrations idempotentes, roundtrip de entidades, imutabilidade de `raw_interaction` via trigger | 5 |
| `test_evidence_aggregation.py` | `classify_dimension` pura: todos os limiares de estado, retencao longitudinal, independencia A0/A1, contradicao, regressao | 12 |
| `test_evidence_service.py` | idempotencia, `mere_presence`, payload invalido, reconstrucao completa | 4 |
| `test_decision_engine.py` | as 9 regras da Secao 17 (`choose_next_action`) e o roteamento SKIP/VALIDATE/STUDY (Secao 18), incluindo prioridade entre regras | 14 |
| `test_decision_service.py` | integracao do Decisor com o banco: `DecisionEvent` gravado com justificativa | 1 |
| `test_memory_adapter.py` | criacao/idempotencia de card, review avanca due date, falha do FSRS nao corrompe estado, reconstrucao do Card a partir do JSON, `is_recall_due` | 6 |
| `test_providers.py` | MockProvider produz tutor/evaluator validos; falha de provider e payload malformado nunca alteram estado | 4 |
| `test_orchestration.py` | ciclo completo via `SessionOrchestrator` com MockProvider; double-submit no nivel de orquestracao | 2 |
| `test_integrity.py` | deteccao de ciclo de pre-requisitos e de referencia pendurada | 3 |
| `test_backup.py` | snapshot consistente e restauravel, falha de backup nao apaga estado, retencao mantem so os N mais recentes | 3 |
| `test_seed_english_graph.py` | seed idempotente, sem ciclos, todas as referencias validas | 3 |
| `test_web.py` | fluxo HTTP completo (start -> next -> answer -> next -> end), paginas de mapa/auditoria, backup manual via UI | 3 |
| `test_redteam.py` | **T1-T15**, um teste por requisito (ver abaixo) | 15 |

## Red Team — T1 a T15 (Fase H)

Todos em `tests/test_redteam.py`, nomeados `test_T<n>_...` para
rastreabilidade direta com a especificacao.

| # | Requisito | Teste | Resultado |
|---|---|---|---|
| T1 | 10 acertos iguais na mesma sessao nao consolidam retencao | `test_T1_ten_same_day_correct_answers_do_not_consolidate_retention` | PASS |
| T2 | Resposta A3 nao demonstra retrieval independente | `test_T2_a3_response_never_demonstrates_independent_retrieval` | PASS |
| T3 | Erro incidental isolado nao derruba competencia consolidada | `test_T3_isolated_incidental_error_does_not_topple_consolidated_competency` | PASS |
| T4 | Evidencia incidental qualificada pode criar hipotese/evidencia secundaria | `test_T4_qualified_incidental_evidence_creates_secondary_hypothesis` | PASS |
| T5 | `mere_presence` nao altera estado | `test_T5_mere_presence_never_alters_state` | PASS |
| T6 | Resposta contraditoria cria incerteza/validacao | `test_T6_contradictory_response_creates_uncertainty_routed_to_validate` | PASS |
| T7 | Avaliacao invalida do LLM nao altera estado | `test_T7_invalid_evaluator_response_never_alters_state` | PASS |
| T8 | Double-submit nao duplica evidencia | `test_T8_double_submit_never_duplicates_evidence` | PASS |
| T9 | Falha no meio da transacao nao cria estado parcial | `test_T9_mid_transaction_failure_creates_no_partial_state` | PASS |
| T10 | Reconstrucao dos estados a partir dos eventos reproduz estado atual | `test_T10_full_reconstruction_reproduces_current_state` | PASS |
| T11 | Mudanca de RuleVersion nao apaga avaliacao historica | `test_T11_rule_version_change_preserves_historical_assessments` | PASS |
| T12 | Multiplas sessoes no mesmo dia nao simulam retencao de varios dias | `test_T12_multiple_sessions_same_day_do_not_simulate_multi_day_retention` | PASS |
| T13 | Prerequisite cycle e bloqueado/detectado | `test_T13_prerequisite_cycle_is_detected` | PASS |
| T14 | Falha do FSRS nao destroi evidencia | `test_T14_fsrs_failure_does_not_destroy_evidence` | PASS |
| T15 | Sistema completo funciona offline com MockProvider | `test_T15_full_system_works_offline_with_mock_provider` | PASS |

## Criterios de aceite (Secao 30) e onde sao verificados

1. Banco criado do zero — `test_persistence.py`, `web/bootstrap.py` (usado em `test_web.py`).
2. Seed de ingles funciona — `test_seed_english_graph.py`.
3. Sessao pode comecar/terminar — `test_orchestration.py`, `test_web.py`.
4. Interacao bruta e preservada — `test_persistence.py::test_raw_interaction_is_immutable`.
5. Avaliacao gera evidencia — `test_evidence_service.py`, `test_providers.py`.
6. Estado e derivado — `test_evidence_aggregation.py`.
7. Decisor produz proxima acao e justificativa — `test_decision_engine.py`, `test_decision_service.py`.
8. FSRS agenda recuperacao — `test_memory_adapter.py::test_review_advances_due_date_and_logs`.
9. Reiniciar programa preserva tudo — SQLite em arquivo local (`data/central.db`), nao ha estado em memoria.
10. Backup/restore testado — `test_backup.py::test_backup_produces_consistent_restorable_snapshot`.
11. `integrity_check` passa — `test_integrity.py::test_clean_database_has_no_findings` e checado ao vivo em `/audit`.
12. T1-T15 passam — `test_redteam.py` (ver tabela acima).
13. Estado reconstruivel do event log — T10.
14. Aplicacao funciona sem internet com MockProvider — T15, e toda a suite roda sem rede.
15. Nenhuma regra constitucional violada — ver `PEDAGOGICAL_CONSTITUTION.md`, cada principio aponta para o teste que o exercita.

## O que NAO esta coberto (limitacoes de teste, nao so de produto)

- Nao ha teste de carga/concorrencia real (multiplos processos escrevendo
  no mesmo SQLite ao mesmo tempo) — irrelevante para um unico usuario
  local na V0, mas relevante se o escopo mudar.
- Nao ha teste de UI automatizado com navegador real (Playwright/Selenium);
  a cobertura de `/session`, `/map`, `/audit` e via `TestClient` do
  FastAPI (HTTP direto), o que valida rotas e HTML gerado mas nao
  interacao JS no navegador (nao ha JS relevante na V0).
- Os testes de "confirmar cenario adversarial" (T1-T15) usam numeros de
  evidencia pequenos (poucas dezenas) para rodar em milissegundos; nao ha
  teste de propriedade (property-based, ex. Hypothesis) variando
  centenas de combinacoes de evidencia. Ficou fora do escopo minimo da V0
  (Secao 35).
