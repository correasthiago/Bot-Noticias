# Estrategia de testes — Central Universal V0.2

## Como rodar

```bash
python -m pytest -q
```

Resultado atual: **100 testes, 100 passando, 0 falhando**, tempo total
< 2s, 100% offline (nenhum teste faz chamada de rede — `MockProvider` e
usado em todos os cenarios que envolvem "IA", e
`test_P11_offline_execution_fails_if_network_is_attempted` ativamente
bloqueia qualquer tentativa de `socket.connect`/`create_connection`
durante o ciclo completo, falhando o teste se algo tentar).

## Camadas de teste

| Arquivo | O que cobre | Testes |
|---|---|---|
| `test_persistence.py` | foreign keys, migrations idempotentes, roundtrip de entidades, imutabilidade de `raw_interaction` (incluindo a UNICA transicao permitida, `pending->completed`) | 6 |
| `test_evidence_aggregation.py` | `classify_dimension` pura: todos os limiares de estado (config-driven), retencao longitudinal, independencia A0/A1, contradicao, cap de contribuicao por cluster, exclusao por confianca baixa, ausencia TOTAL de rebaixamento automatico, thresholds nao hardcoded | 17 |
| `test_evidence_service.py` | idempotencia, `mere_presence`, payload invalido, reconstrucao completa preservando geracoes, reprocessamento idempotente de interacao `pending`, exclusao de `inconclusive`/`alternative_cause` | 7 |
| `test_clustering.py` | cluster computado no servidor: mesmo contexto/sessao colide, contextos diferentes nao colidem, sessoes diferentes nunca colidem, API nao aceita cluster id do chamador | 4 |
| `test_decision_engine.py` | as 9 regras da Secao 17 (`choose_next_action`) e o roteamento SKIP/VALIDATE/STUDY (Secao 18), incluindo prioridade entre regras | 14 |
| `test_decision_service.py` | integracao do Decisor com o banco: `DecisionEvent` gravado com justificativa | 1 |
| `test_memory_adapter.py` | criacao/idempotencia de card, elegibilidade de observacao (Secao 2 do pacote de correcao), review avanca due date, escrita atomica card+log+observation (falha em CADA uma das tres gravacoes testada via parametrize, byte a byte), reconstrucao do Card a partir do JSON, `is_recall_due` | 14 (12 defs, 1 parametrizado em 3) |
| `test_providers.py` | MockProvider produz tutor/evaluator validos; falha de provider e payload malformado nunca alteram estado | 4 |
| `test_orchestration.py` | ciclo completo via `SessionOrchestrator` com MockProvider; atividade planejada gera MemoryObservation, atividade comum NAO chama FSRS; double-submit no nivel de orquestracao | 3 |
| `test_integrity.py` | ciclo de pre-requisitos rejeitado na escrita (+ defesa por integrity_check contra bypass via SQL bruto), referencia pendurada, consistencia de geracao (multiplas geracoes 'active'), consistencia memory_state x memory_review_log | 6 |
| `test_backup.py` | snapshot consistente e restauravel, falha de backup nao apaga estado, retencao mantem so os N mais recentes | 3 |
| `test_restore.py` | restore real substitui e recupera o banco, snapshot invalido e rejeitado sem tocar no banco ativo, falha pos-troca reverte automaticamente, politica de backup automatico respeita intervalo minimo | 4 |
| `test_seed_english_graph.py` | seed idempotente, sem ciclos, todas as referencias validas | 3 |
| `test_web.py` | fluxo HTTP completo (start -> next -> answer -> next -> end), paginas de mapa/auditoria, backup manual via UI | 3 |
| `test_redteam.py` | **P1-P11**, um teste por item do pacote de correcao v0.2 (ver abaixo) | 11 |

## Red Team pos-correcao v0.2 — P1 a P11

Todos em `tests/test_redteam.py`, nomeados `test_P<n>_...`. Os antigos
T1-T15 descreviam o algoritmo v1 (hardcoded, com rebaixamento automatico
por corroboracao) - alguns desses comportamentos foram deliberadamente
REVERTIDOS pela correcao, entao os numeros antigos deixaram de fazer
sentido como checklist. Os principios do v1 que continuam validos (ver
tabela acima) permanecem cobertos nos outros arquivos.

| # | Requisito | Teste | Resultado |
|---|---|---|---|
| P1 | Avaliacao invalida nao altera nenhuma projecao nem FSRS | `test_P1_invalid_evaluation_never_alters_projection_or_memory` | PASS |
| P2 | Queda depois da interacao bruta permite reavaliacao idempotente | `test_P2_reprocessing_after_evaluator_failure_is_idempotent` | PASS |
| P3 | Duas negativas no mesmo cluster nao corroboram regressao | `test_P3_two_negatives_from_same_cluster_never_corroborate_regression` | PASS |
| P4 | IDs de cluster diferentes nao provam independencia por si | `test_P4_different_activity_ids_same_context_collapse_into_one_cluster` | PASS |
| P5 | Reconstrucao preserva historico e e atomica | `test_P5_reconstruction_preserves_history_and_is_atomic_on_failure` | PASS |
| P6 | Duas RuleVersions produzem resultados diferentes quando suas regras diferem | `test_P6_two_rule_versions_with_different_config_diverge` | PASS |
| P7 | Falha ao inserir MemoryReviewLog nao altera MemoryState | `test_P7_memory_review_log_failure_never_alters_memory_state` | PASS |
| P8 | Ciclo de pre-requisito e rejeitado na escrita | `test_P8_prerequisite_cycle_is_rejected_at_write_time` | PASS |
| P9 | Restore real substitui e recupera o banco | `test_P9_real_restore_substitutes_and_recovers_the_database` | PASS |
| P10 | Reinicio completo do processo preserva estado | `test_P10_full_process_restart_preserves_state` | PASS |
| P11 | Execucao offline falha no teste se qualquer acesso de rede for tentado | `test_P11_offline_execution_fails_if_network_is_attempted` | PASS |

## Criterios de aceite (Secao 30) e onde sao verificados

1. Banco criado do zero — `test_persistence.py`, `web/bootstrap.py` (usado em `test_web.py`).
2. Seed de ingles funciona — `test_seed_english_graph.py`.
3. Sessao pode comecar/terminar — `test_orchestration.py`, `test_web.py`.
4. Interacao bruta e preservada — `test_persistence.py::test_raw_interaction_is_immutable`.
5. Avaliacao gera evidencia — `test_evidence_service.py`, `test_providers.py`.
6. Estado e derivado — `test_evidence_aggregation.py`.
7. Decisor produz proxima acao e justificativa — `test_decision_engine.py`, `test_decision_service.py`.
8. FSRS agenda recuperacao — `test_memory_adapter.py`, `test_orchestration.py::test_planned_recall_activity_produces_memory_observation`.
9. Reiniciar programa preserva tudo — `test_redteam.py::test_P10_full_process_restart_preserves_state`.
10. Backup/restore testado — `test_backup.py`, `test_restore.py`, `test_redteam.py::test_P9_real_restore_substitutes_and_recovers_the_database`.
11. `integrity_check` passa — `test_integrity.py::test_clean_database_has_no_findings`, checado ao vivo em `/audit`.
12. Testes de Red Team passam — `test_redteam.py` (P1-P11, ver tabela acima).
13. Estado reconstruivel do event log, preservando historico — `test_redteam.py::test_P5_reconstruction_preserves_history_and_is_atomic_on_failure`.
14. Aplicacao funciona sem internet com MockProvider — `test_redteam.py::test_P11_offline_execution_fails_if_network_is_attempted`, e toda a suite roda sem rede.
15. Nenhuma regra constitucional violada — ver `PEDAGOGICAL_CONSTITUTION.md`, cada principio aponta para o teste que o exercita.

## O que NAO esta coberto (limitacoes de teste, nao so de produto)

- Nao ha teste de carga/concorrencia real (multiplos processos escrevendo
  no mesmo SQLite ao mesmo tempo) — irrelevante para um unico usuario
  local na V0, mas relevante se o escopo mudar. A idempotencia de
  avaliacao (`try_claim_evaluation`) e desenhada para ser segura sob
  concorrencia gracas ao lock `BEGIN IMMEDIATE` do SQLite, mas isso nao
  foi exercitado com threads/processos reais.
- Nao ha teste de UI automatizado com navegador real (Playwright/Selenium);
  a cobertura de `/session`, `/map`, `/audit` e via `TestClient` do
  FastAPI (HTTP direto), o que valida rotas e HTML gerado mas nao
  interacao JS no navegador (nao ha JS relevante na V0).
- Os testes de "confirmar cenario adversarial" usam numeros de evidencia
  pequenos (poucas dezenas) para rodar em milissegundos; nao ha teste de
  propriedade (property-based, ex. Hypothesis) variando centenas de
  combinacoes de evidencia. Ficou fora do escopo minimo da V0 (Secao 35).
- O bloqueio de rede em `test_P11` cobre `socket.socket.connect` e
  `socket.create_connection`; uma biblioteca que abrisse conexoes por um
  caminho mais baixo nivel (ex.: syscalls diretas) nao seria pega - mas
  nenhuma dependencia do projeto faz isso, e o `MockProvider` em si nao
  importa nenhuma biblioteca de rede.
