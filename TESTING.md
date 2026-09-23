# Estrategia de testes — Central Universal V0.2

## Como rodar

```bash
python -m pytest -q
```

Resultado atual: **140 testes**, 100% offline (nenhum teste faz chamada
de rede — `MockProvider` e usado em todos os cenarios que envolvem "IA",
e `test_P11_offline_execution_fails_if_network_is_attempted` ativamente
bloqueia qualquer tentativa de `socket.connect`/`create_connection`
durante o ciclo completo, falhando o teste se algo tentar).

**Resultado por sistema operacional (nao e universal - ver historico
abaixo; NAO declare um numero como resultado valido para um SO que nao
foi de fato executado nele):**

- **Linux** (ambiente desta sessao): **140/140 passando**, tempo total
  ~3.4s. Rodado 10 vezes consecutivas apos a correcao mais recente (os
  tres testes novos - dois de P1/regressao pre-aprendizagem, um de P2/
  recuperacao da revisao de memoria via web - inclusos em todas as 10)
  sem nenhuma falha.
- **Windows**: um usuario reportou e CONFIRMOU, em seis rodadas:
  1. Apos o commit `1133ded` (pacote v0.2.2): 120/128 passando, 5 falhas
     em `test_restore.py` (`PermissionError: [WinError 5]`) - corrigido
     no commit `961b5b8` (ver "Correcao pos-entrega: restore quebrava no
     Windows" em DECISIONS.md).
  2. Apos o commit `961b5b8`: **128/128 na suite completa e 10/10 em
     `test_restore.py` isolado, CONFIRMADO em Windows real** pelo mesmo
     usuario - o `WinError 5` original esta resolvido.
  3. Apos o commit `c4ce152` (que fechou as duas janelas de corrida do
     portao de concorrencia): **130/130 na suite completa e 12/12 em
     `test_restore.py` isolado, CONFIRMADO em Windows real** pelo mesmo
     usuario, via auditoria de codigo E execucao real.
  4. Apos o commit `c3b9bcb` (P2: preparacao do restore entrando no
     tratamento de falha): **132/132 CONFIRMADO em Windows real** pelo
     mesmo usuario, via auditoria de codigo (sem alterar o repositorio).
  5. Apos o commit `3a5f988` (falha na propria limpeza da copia de
     seguranca): **135/135 CONFIRMADO em Windows real** pelo mesmo
     usuario - o restore foi dado como validado em ambos os SOs.
  6. Apos o commit `c208fee` (revisao de memoria retentada + suspeita de
     regressao permanente): **137/137 CONFIRMADO em Windows real** pelo
     mesmo usuario, via auditoria de codigo (sem alterar o repositorio).
  Essa mesma auditoria do commit `c208fee` encontrou mais dois achados no
  fluxo de aprendizagem: um FALSO sinal de regressao para erros
  anteriores a qualquer dominio demonstrado (P1), e a recuperacao da
  revisao de memoria nunca chegando ao usuario pela rota web (P2) - ver
  "Correcao pos-entrega: sinal de regressao passou a acender para erros
  ANTERIORES a qualquer dominio demonstrado (P1)" e "Correcao
  pos-entrega: recuperacao da revisao de memoria nunca chegava ao usuario
  (P2)" em DECISIONS.md. Ambos corrigidos nesta revisao (140 testes, tres
  novos - dois de P1, um de P2). Esta correcao mais recente foi validada
  em Linux (10 execucoes consecutivas), mas **ainda nao foi executada num
  Windows real** - isso depende do usuario confirmar. Ate essa
  confirmacao chegar, trate "140/140" como "corrigido no codigo e
  validado em Linux, pendente de confirmacao em Windows" para ESTA
  correcao especifica - nao como um resultado ja observado em Windows.
- **macOS**: nunca foi executado nesta ou em rodadas anteriores; sem
  dados. O mecanismo de `os.replace` deveria se comportar como Linux
  (semantica POSIX de `rename()`), mas isso e inferencia, nao um
  resultado observado.

## Camadas de teste

| Arquivo | O que cobre | Testes |
|---|---|---|
| `test_persistence.py` | foreign keys, migrations idempotentes, roundtrip de entidades, imutabilidade de `raw_interaction` (incluindo a UNICA transicao permitida, `pending->completed`), `sequence_number` monotonico com RELOGIO CONGELADO (`current`/`history` deterministicos mesmo com todas as linhas no mesmo `computed_at`) | 7 |
| `test_evidence_aggregation.py` | `classify_dimension` pura: todos os limiares de estado (config-driven), retencao longitudinal, independencia A0/A1, contradicao, cap de contribuicao por cluster, exclusao por confianca baixa, ausencia TOTAL de rebaixamento automatico, thresholds nao hardcoded, suspeita de regressao NUNCA silenciada por evidencia positiva comum mais recente (so validacao deliberada resolveria), erro ANTES de dominio demonstrado nunca e regressao, erro DEPOIS de dominio demonstrado continua sendo | 20 |
| `test_evidence_service.py` | idempotencia, rejeicao de `idempotency_key` reutilizada com conteudo diferente (inclusive apos reinicio do processo), reprocessamento a partir da `RawInteraction` persistida, `mere_presence`, payload invalido, reconstrucao completa preservando geracoes, reprocessamento idempotente de interacao `pending`, exclusao de `inconclusive`/`alternative_cause` | 10 |
| `test_clustering.py` | cluster computado no servidor a partir de `(activity_type, competency_targets)` - nunca do prompt: mesmo contexto/sessao colide (mesmo com prompts textualmente diferentes mas previsiveis), contextos/competencias/sessoes diferentes nao colidem, API nao aceita cluster id do chamador | 6 |
| `test_decision_engine.py` | as 9 regras da Secao 17 (`choose_next_action`) e o roteamento SKIP/VALIDATE/STUDY (Secao 18), incluindo prioridade entre regras | 14 |
| `test_decision_service.py` | integracao do Decisor com o banco: `DecisionEvent` gravado com justificativa | 1 |
| `test_memory_adapter.py` | criacao/idempotencia de card, elegibilidade de observacao (dimensao RETENTION + relacao target + confianca + intervalo minimo, config-driven), nota FSRS derivada EXCLUSIVAMENTE de sinais observaveis (help_level/production_result, nunca confianca nem ProductionResult bruto - alta confianca + pista explicita = Hard, nunca Easy), intervalo desde a aprendizagem verificado tambem na PRIMEIRA revisao, review avanca due date, escrita atomica card+log+observation (falha em CADA uma das tres gravacoes testada via parametrize, byte a byte), reconstrucao do Card a partir do JSON, `is_recall_due` | 23 (21 defs, 1 parametrizado em 3) |
| `test_migrations.py` | migration 0002 atomica (rollback completo diante de falha sintetica no meio do script), preserva TODAS as avaliacoes v0.1 (normalizando em vez de descartar as incompativeis com o novo CHECK), backup automatico antes de atualizar um banco existente, banco novo vazio nao dispara backup, falha ao GRAVAR o registro em schema_migrations reverte o esquema inteiro | 5 |
| `test_providers.py` | MockProvider produz tutor/evaluator validos (avaliando a dimensao que a atividade foi desenhada para exercitar); falha de provider e payload malformado nunca alteram estado | 4 |
| `test_orchestration.py` | ciclo completo via `SessionOrchestrator` com MockProvider; primeiro card FSRS nasce pelo fluxo NORMAL da aplicacao (banco novo, sem `is_planned_recall=True` setado no teste, so a sequencia real de chamadas ate o Decisor escolher SCHEDULE_RECALL, com relogio controlado via `now=`); atividade comum NAO chama FSRS; double-submit no nivel de orquestracao; falha do FSRS numa primeira submissao NAO perde a revisao para sempre - uma submissao repetida tenta de novo, sem nunca duplicar numa terceira | 4 |
| `test_integrity.py` | ciclo de pre-requisitos rejeitado na escrita (+ defesa por integrity_check contra bypass via SQL bruto), referencia pendurada, consistencia de geracao (multiplas geracoes 'active'), consistencia memory_state x memory_review_log | 6 |
| `test_backup.py` | snapshot consistente e restauravel, falha de backup nao apaga estado, retencao mantem so os N mais recentes | 3 |
| `test_restore.py` | restore real substitui e recupera o banco, snapshot invalido e rejeitado sem tocar no banco ativo, falha pos-troca reverte automaticamente, politica de backup automatico respeita intervalo minimo, WAL genuinamente ativo e achatado antes da troca, conexao concorrente detectada e recusada sem tocar em arquivos, reversao limpa com WAL ativo, NENHUMA conexao sqlite3 aberta no instante do `os.replace` (a causa raiz do bug relatado no Windows), o portao da aplicacao liga durante a operacao e sempre desliga depois, dupla falha (restauracao + reversao) nunca levanta excecao e preserva a copia de seguranca, uma SEGUNDA restauracao concorrente e rejeitada na hora (threads reais), uma requisicao com conexao ja aberta impede a restauracao de tocar arquivos ate fechar, falha em `close_connections()` na preparacao vira `RestoreResult` legivel sem tentar reverter, falha na criacao da copia de seguranca na preparacao idem, falha na propria LIMPEZA da copia de seguranca (apos falha na preparacao, apos reversao bem-sucedida, apos restauracao bem-sucedida) nunca escapa nem oculta o resultado real | 17 |
| `test_seed_english_graph.py` | seed idempotente, sem ciclos, todas as referencias validas | 3 |
| `test_web.py` | fluxo HTTP completo (start -> next -> answer -> next -> end), paginas de mapa/auditoria, backup manual via UI, falha de restore mostrada ao usuario na pagina de auditoria, requisicao HTTP recebe 503 se chegar enquanto uma restauracao esta em andamento, falha na revisao de memoria e mostrada ao usuario na pagina da sessao com um caminho de retentativa visivel que recupera a revisao (usando o horario da tentativa original) sem duplicar | 6 |
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
8. FSRS agenda recuperacao — `test_memory_adapter.py`, `test_orchestration.py::test_first_fsrs_card_is_born_through_normal_application_flow`.
9. Reiniciar programa preserva tudo — `test_redteam.py::test_P10_full_process_restart_preserves_state`.
10. Backup/restore testado — `test_backup.py`, `test_restore.py`, `test_redteam.py::test_P9_real_restore_substitutes_and_recovers_the_database`.
11. `integrity_check` passa — `test_integrity.py::test_clean_database_has_no_findings`, checado ao vivo em `/audit`.
12. Testes de Red Team passam — `test_redteam.py` (P1-P11, ver tabela acima).
13. Estado reconstruivel do event log, preservando historico — `test_redteam.py::test_P5_reconstruction_preserves_history_and_is_atomic_on_failure`.
14. Aplicacao funciona sem internet com MockProvider — `test_redteam.py::test_P11_offline_execution_fails_if_network_is_attempted`, e toda a suite roda sem rede.
15. Nenhuma regra constitucional violada — ver `PEDAGOGICAL_CONSTITUTION.md`, cada principio aponta para o teste que o exercita.

## Segunda auditoria pos-entrega — pacote de correcao v0.2.1

Uma segunda auditoria sobre o commit que fechou a v0.2 encontrou seis
pontos onde os 100 testes daquele pacote passavam mas o criterio de
aceite ainda nao era atendido (ver secao "Pacote de correcao v0.2.1" em
`DECISIONS.md` para a decisao completa de cada um). Cada um tem pelo
menos um teste que exercita o fluxo REAL, nao um atalho:

| # | Requisito | Teste principal |
|---|---|---|
| 1 | Primeiro card FSRS nasce pelo fluxo normal da aplicacao, sem `is_planned_recall=True` setado no teste | `test_orchestration.py::test_first_fsrs_card_is_born_through_normal_application_flow` |
| 2 | Nota FSRS derivada da EvidenceAssessment de RETENTION da propria tentativa (negativa, baixa confianca, incidental, tentativas segundos-apartadas) | `test_memory_adapter.py::test_negative_evaluation_is_eligible_with_again_rating` e vizinhos |
| 3 | Reprocessamento usa a RawInteraction persistida; idempotency_key reutilizada com conteudo diferente e rejeitada, inclusive apos reinicio do processo | `test_evidence_service.py::test_idempotency_conflict_rejected_after_process_restart` |
| 4 | Migration 0002 atomica, preserva avaliacoes v0.1 incompativeis com o novo CHECK (normalizando, nunca descartando) | `test_migrations.py::test_migration_0002_preserves_all_v01_evaluations_including_incompatible_ones` |
| 5 | Restore impede escritas concorrentes, trata WAL/sidecars, mostra falha ao usuario | `test_restore.py::test_restore_aborts_cleanly_with_concurrent_connection_open`, `test_web.py::test_restore_failure_is_shown_to_the_user` |
| 6 | Prompts diferentes mas previsiveis na mesma sessao nao promovem competencia sozinhos | `test_clustering.py::test_predictable_template_variation_does_not_prove_independence` |

## Terceira auditoria pos-entrega — pacote de correcao v0.2.2

Uma terceira auditoria sobre o commit que fechou a v0.2.1 encontrou
quatro pontos, incluindo uma falha intermitente que o proprio auditor
reproduziu rodando a suite duas vezes seguidas (ver secao "Pacote de
correcao v0.2.2" em `DECISIONS.md` para a decisao completa de cada um):

| # | Requisito | Teste principal |
|---|---|---|
| 1 | "Estado atual" escolhido por sequence_number monotonico, nunca por computed_at+id aleatorio; reproduzido com relogio congelado | `test_persistence.py::test_competency_state_current_and_history_use_monotonic_sequence_frozen_clock` |
| 2 | Nota FSRS de sinais observaveis (help_level/production_result), nunca de confianca; primeira revisao verifica intervalo desde a aprendizagem | `test_memory_adapter.py::test_derive_recall_rating_uses_observable_signals_not_confidence`, `test_first_review_too_soon_after_learning_is_never_eligible` |
| 3 | Guarda de exclusividade do restore protege ate o fim da troca - conexao aberta depois da checagem e antes do os.replace e bloqueada | Mecanismo original desta correcao (mantinha um lock do SQLite preso durante a troca) foi REVISADO na correcao pos-entrega seguinte por quebrar no Windows - ver secao abaixo. |
| 4 | Registro em schema_migrations na mesma transacao atomica da migration; falha ao grava-lo reverte o esquema inteiro | `test_migrations.py::test_migration_bookkeeping_failure_leaves_whole_schema_at_previous_version` |

## Correcao pos-entrega: restore quebrava no Windows

Um usuario rodou a suite no Windows apos o commit que fechou a v0.2.2 e
reportou 120/128 passando com as 5 falhas concentradas em
`test_restore.py` - `os.replace` recusando substituir o banco ativo
porque a guarda de exclusividade do ponto 3 acima mantinha uma conexao
SQLite aberta durante a troca (`PermissionError: [WinError 5]`, um
comportamento que o Linux/macOS simplesmente nao tem). Ver secao
"Correcao pos-entrega: restore quebrava no Windows" em `DECISIONS.md`
para a decisao completa.

| # | Requisito | Teste principal |
|---|---|---|
| 1 | Nenhuma conexao sqlite3 deste processo aberta no instante exato do `os.replace` (a causa raiz do bug no Windows) | `test_restore.py::test_restore_holds_no_sqlite_connection_open_across_os_replace` |
| 2 | Pausa de requisicoes no nivel da aplicacao durante toda a janela de restauracao, com retomada garantida | `test_restore.py::test_restore_pauses_the_application_gate_and_always_resumes_it`, `test_web.py::test_restore_rejects_new_requests_while_in_progress` |
| 3 | Falha na propria reversao nunca levanta excecao; banco original preservado com resultado legivel | `test_restore.py::test_restore_never_raises_when_revert_itself_also_fails` |

Confirmado pelo usuario em Windows real apos esta correcao: 128/128
(suite completa) e 10/10 (`test_restore.py` isolado).

## Correcao pos-entrega: portao de concorrencia do restore tinha duas janelas

Ja em Windows, o mesmo usuario encontrou e REPRODUZIU dois problemas
novos no portao introduzido pela correcao acima: (1) duas restauracoes
disparadas ao mesmo tempo podiam ambas "entrar", e quando a primeira
terminava, desligava o portao mesmo com a segunda ainda em andamento;
(2) havia uma janela em `get_conn()` entre consultar o portao e abrir a
conexao, onde uma restauracao podia comecar no meio. Ver secao "Correcao
pos-entrega: portao de concorrencia do restore tinha duas janelas" em
`DECISIONS.md` para a decisao completa (coordenacao leitor/escritor com
`threading.Condition`).

| # | Requisito | Teste principal |
|---|---|---|
| 1 | Uma segunda restauracao disparada enquanto a primeira ainda esta em andamento e rejeitada na hora, nunca "entra" junto | `test_restore.py::test_restore_rejects_a_concurrent_second_restore_attempt` |
| 2 | Uma requisicao que ja passou pela checagem do portao (conexao aberta) impede a restauracao de tocar arquivos ate ela terminar | `test_restore.py::test_restore_waits_for_an_in_flight_request_before_touching_files` |

Ambos os testes usam threads reais e sincronizacao deterministica
(`threading.Event`, nunca `sleep`) - rodados 20 vezes consecutivas em
Linux sem falha. Confirmado pelo usuario em Windows real apos esta
correcao (commit `c4ce152`): 130/130 (suite completa) e 12/12
(`test_restore.py` isolado).

## Correcao pos-entrega: preparacao do restore nao entrava no tratamento de falha

Auditando o codigo do commit `c4ce152` (sem executar nada), o mesmo
usuario encontrou um ponto ainda nao coberto: `close_connections()` (o
callback do chamador) e a criacao da copia de seguranca (`shutil.copy2`)
rodavam antes do bloco que converte falhas em `RestoreResult` - uma
excecao ali escapava sem tratamento ate a rota web. Ver secao "Correcao
pos-entrega: preparacao do restore nao entrava no tratamento de falha" em
`DECISIONS.md` para a decisao completa.

| # | Requisito | Teste principal |
|---|---|---|
| 1 | Falha em `close_connections()` na preparacao vira `RestoreResult(success=False, ...)` legivel, sem excecao escapando, sem tentar reverter (a troca nunca comecou) | `test_restore.py::test_restore_returns_readable_result_when_close_connections_fails` |
| 2 | Falha na criacao da copia de seguranca (`shutil.copy2`) na preparacao idem | `test_restore.py::test_restore_returns_readable_result_when_safety_copy_creation_fails` |

Rodados 10 vezes consecutivas em Linux sem falha. Confirmado pelo usuario
em Windows real apos esta correcao (commit `c3b9bcb`): **132/132**.

## Correcao pos-entrega: falha na propria limpeza da copia de seguranca podia escapar ou ocultar o resultado

Na mesma auditoria (do commit `c3b9bcb`, sem executar nada), o usuario
encontrou que os tres pontos onde a copia de seguranca e removida
(`safety_copy.unlink(...)`) chamavam `unlink` direto, sem tratamento -
uma falha ali podia escapar como excecao nao tratada, e no caso da
limpeza APOS uma restauracao bem-sucedida, ate impedir que o
`RestoreResult(success=True, ...)` fosse sequer construido, ocultando um
sucesso real por tras de um erro de limpeza. Ver secao "Correcao
pos-entrega: falha na propria limpeza da copia de seguranca podia escapar
ou ocultar o resultado" em `DECISIONS.md` para a decisao completa (helper
`_safe_unlink`, nunca levanta).

| # | Requisito | Teste principal |
|---|---|---|
| 1 | Falha na preparacao + falha ao limpar a copia parcial: causa original preservada, nota de limpeza anexada, `success=False` (a troca nunca comecou) | `test_restore.py::test_restore_readable_result_when_preparation_failure_and_its_cleanup_both_fail` |
| 2 | Reversao bem-sucedida + falha ao limpar a copia que sobrou: causa original preservada, `success=False` (a restauracao em si falhou) | `test_restore.py::test_restore_readable_result_when_cleanup_after_successful_revert_fails` |
| 3 | Restauracao bem-sucedida + falha ao limpar a copia que sobrou: `success=True`/`integrity_ok=True` (o estado do banco decide, nao a limpeza) | `test_restore.py::test_restore_still_reports_success_when_cleanup_after_successful_restore_fails` |

Rodados 10 vezes consecutivas em Linux sem falha. Confirmado pelo usuario
em Windows real apos esta correcao (commit `3a5f988`): **135/135** - o
restore foi dado como validado em ambos os SOs.

## Correcao pos-entrega: duas lacunas no fluxo de aprendizagem (fora do restore)

Com o restore fechado, o usuario passou a auditar o restante do fluxo de
aprendizagem e encontrou dois achados independentes, ambos so por leitura
de codigo (sem executar nada). Ver "Correcao pos-entrega: revisao de
memoria podia se perder para sempre apos falha do FSRS" e "Correcao
pos-entrega: suspeita de regressao podia ser silenciada por evidencia
comum, sem validacao deliberada" em `DECISIONS.md` para as decisoes
completas.

| # | Requisito | Teste principal |
|---|---|---|
| 1 | Uma submissao repetida (mesma idempotency_key) de uma interacao ja avaliada tenta a revisao de memoria de novo se ela ainda nao aconteceu - nunca duplica se ja tiver acontecido | `test_orchestration.py::test_memory_review_retries_after_fsrs_failure_without_duplicating` |
| 2 | `possible_regression` nunca e silenciado por evidencia positiva COMUM mais recente - so validacao deliberada (fora de escopo da V0) resolveria | `test_evidence_aggregation.py::test_common_positive_evidence_after_regression_never_silences_the_signal` |

Rodados 10 vezes consecutivas em Linux sem falha (137/137, suite
completa). Confirmado pelo usuario em Windows real apos esta correcao
(commit `c208fee`): **137/137**.

## Correcao pos-entrega: falso sinal de regressao e recuperacao de memoria invisivel na web (P1 e P2)

Auditando o commit `c208fee` (sem executar nada), o usuario encontrou
mais dois achados: a correcao anterior do sinal de regressao passou a
sinalizar erros ANTERIORES a qualquer dominio demonstrado (P1), e a
retentativa de revisao de memoria que o servico ja sabia fazer nunca
chegava a aparecer para o usuario na interface web (P2). Ver "Correcao
pos-entrega: sinal de regressao passou a acender para erros ANTERIORES a
qualquer dominio demonstrado (P1)" e "Correcao pos-entrega: recuperacao
da revisao de memoria nunca chegava ao usuario (P2)" em `DECISIONS.md`
para as decisoes completas.

| # | Requisito | Teste principal |
|---|---|---|
| 1 (P1) | Um erro ANTES de qualquer dominio demonstrado (>= `demonstrated_min_clusters`/dias na evidencia estritamente anterior a ele) nunca sinaliza regressao | `test_evidence_aggregation.py::test_error_before_any_demonstrated_mastery_is_not_a_regression` |
| 2 (P1, contraste) | Um erro DEPOIS de dominio ja demonstrado continua sinalizando regressao, permanente | `test_evidence_aggregation.py::test_error_after_demonstrated_mastery_is_still_a_regression` |
| 3 (P2) | Falha na revisao de memoria e visivel na pagina da sessao, com um formulario de retentativa que reenvia a MESMA resposta e recupera a revisao usando o horario da tentativa ORIGINAL, sem duplicar | `test_web.py::test_memory_review_recovery_is_visible_and_retryable_over_http` |

Rodados 10 vezes consecutivas em Linux sem falha (140/140, suite
completa). Validacao em Windows ainda pendente (ver "Como rodar" acima).

## O que NAO esta coberto (limitacoes de teste, nao so de produto)

- Nao ha teste de carga/concorrencia real (multiplos processos escrevendo
  no mesmo SQLite ao mesmo tempo) — irrelevante para um unico usuario
  local na V0, mas relevante se o escopo mudar. A idempotencia de
  avaliacao (`try_claim_evaluation`) e desenhada para ser segura sob
  concorrencia gracas ao lock `BEGIN IMMEDIATE` do SQLite, mas isso nao
  foi exercitado com threads/processos reais. O mesmo vale para
  `test_restore.py::test_restore_aborts_cleanly_with_concurrent_connection_open`:
  a "conexao concorrente" e uma segunda `sqlite3.Connection` no MESMO
  processo/thread do teste, nao um processo separado de verdade - prova
  o mecanismo (o proprio SQLite recusa sair do modo WAL com outra conexao
  aberta), mas nao testa contencao real entre processos do SO.
- Desde a correcao pos-entrega para Windows, a checagem de exclusividade
  do restore (`_check_exclusive`) e best-effort e NUNCA mantida presa
  durante a copia/`os.replace` (ao contrario de uma revisao anterior, que
  mantinha - e quebrava no Windows). Isso significa que uma conexao
  verdadeiramente EXTERNA (nao roteada pela propria aplicacao, logo
  imune ao portao `is_restore_in_progress`) que abra bem no meio da
  janela entre a checagem e a troca nao e mais bloqueada por um lock do
  SQLite - so seria pega por sorte, se tentasse escrever no instante
  exato em que ainda houvesse alguma conexao presa. A protecao real hoje
  e o portao em memoria, que so cobre requisicoes que passam por
  `web/deps.py:get_conn` - um script Python externo tocando o arquivo
  diretamente durante uma restauracao continua sem protecao formal. Essa
  e uma troca deliberada (ver DECISIONS.md), nao um descuido, mas vale
  registrar que a garantia ficou mais fraca contra terceiros externos.
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
