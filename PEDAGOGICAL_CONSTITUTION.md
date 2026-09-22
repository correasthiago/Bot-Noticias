# Constituicao Pedagogica — Central Universal V0.2

Estes 20 principios nao podem ser alterados por conveniencia de
implementacao. Qualquer mudanca de regra pedagogica exige uma nova
`RuleVersion` (Principio 18), nunca uma edicao silenciosa do
comportamento existente. Cada principio abaixo aponta para onde ele e
imposto no codigo e qual teste do Red Team o exercita.

> **Nota de versao (v0.1.0 -> v0.2.0):** apos a entrega da V0.1, um Red
> Team pos-entrega encontrou pontos onde a implementacao original cumpria
> a LETRA de alguns principios mas nao o espirito completo (ex.:
> regressao podia ser rebaixada automaticamente por corroboracao;
> clusters de evidencia aceitavam um id arbitrario do chamador). O pacote
> de correcao v0.2 fecha essas lacunas. Este documento ja reflete o
> comportamento CORRIGIDO; `DECISIONS.md` tem o detalhe de cada mudanca.

1. **Aula concluida nao significa competencia adquirida.**
   O Tutor (`tutor/`) nunca escreve em `CompetencyState`; so o motor de
   agregacao (`evidence/aggregation.py`), a partir de evidencia real, o
   faz.

2. **Probabilidade nao declara dominio sozinha.**
   `CompetencyDimensionState` nao e um score numerico de "mastery": e uma
   maquina de estados baseada em contagem de evidencia independente POR
   CLUSTER (no maximo uma contribuicao por cluster/competencia/dimensao/
   direcao - ver DECISIONS.md e `evidence/clustering.py`).

3. **Estado atual nao substitui historico.**
   `competency_state` e append-only e IMUTAVEL no banco (trigger SQL);
   `raw_interaction`, `evidence_event` e `evidence_assessment` nunca sao
   apagados ou sobrescritos. Reconstrucoes completas criam uma nova
   `ProjectionGeneration` em vez de apagar a projecao (Secao 14 v0.2) -
   geracoes antigas permanecem no banco para sempre.

4. **Evidencias brutas sao imutaveis.**
   Triggers SQL (`trg_raw_interaction_immutable_*`,
   `trg_evidence_event_immutable_*`, `trg_evidence_assessment_immutable_*`,
   `trg_competency_state_immutable_*`, e desde v0.2 tambem
   `trg_rule_version_immutable_*`, `trg_decision_event_immutable_*`,
   `trg_provider_event_immutable_*`, `trg_memory_review_log_immutable_*`,
   `trg_backup_event_immutable_*`) bloqueiam UPDATE/DELETE no nivel do
   banco, nao so por convencao de codigo. A UNICA excecao documentada e a
   transicao `raw_interaction.evaluation_status` de `pending` para
   `completed` (necessaria para reavaliacao idempotente - Principio 21
   abaixo) - qualquer outra alteracao, inclusive tentar voltar de
   `completed` para `pending`, e abortada. Verificado por
   `check_immutability_triggers_present` e por
   `test_raw_interaction_is_immutable`.

5. **Avaliacoes sao versionadas.**
   Toda `EvidenceAssessment` carrega `rule_version_id`; mudar de regra
   cria uma nova avaliacao, nunca edita a antiga
   (`test_P6_two_rule_versions_with_different_config_diverge`).

6. **Estados derivados devem ser recalculaveis.**
   `recompute_all_from_log` reconstroi `competency_state` a partir de
   `raw_interaction + evidence_event + evidence_assessment + rule_version`
   numa GERACAO NOVA, validada antes de ser ativada, PRESERVANDO a
   geracao anterior intacta (`test_P5_reconstruction_preserves_history_and_is_atomic_on_failure`,
   `check_generation_consistency`).

7. **Professor nao certifica aquilo que acabou de ensinar.**
   `tutor.contract.TutorOutput` nao tem nenhum campo de estado; o Tutor
   so produz texto de atividade.

8. **Professor, Avaliador e Decisor sao funcoes separadas.**
   Tres modulos (`tutor/`, `evaluator/`, `decision/`) com contratos
   proprios; o Decisor (`decision/engine.py`) nao chama nenhum provider.
   Desde v0.2, `EvidenceEvent` (fatos observados) e `EvidenceAssessment`
   (classificacao/confianca/causa/RuleVersion) tambem sao entidades
   estritamente separadas - nenhuma delas mistura fato com julgamento.

9. **O Decisor V0 deve ser deterministico/auditavel.**
   `decision/engine.py` e codigo puro, sem I/O, sem chamada de LLM;
   cada saida carrega `rule_applied` (slug estavel) e `justification`
   (texto).

10. **Repeticoes no mesmo dia nao provam retencao longitudinal.**
    `classify_dimension` exige dias DISTINTOS de evidencia forte
    (deduplicada por cluster) para `retention` chegar a
    `demonstrated`/`consolidated`
    (`test_retention_same_day_repeats_never_consolidate`,
    `check_retention_same_day_promotion`).

11. **Evidencia com ajuda nao equivale a recuperacao independente.**
    Evidencia positiva em A2/A3 nunca conta como evidencia forte em
    `classify_dimension` (`test_a3_help_never_counts_as_independent_retrieval`,
    `check_retrieval_independence`).

12. **Um erro isolado nao apaga dominio anterior.**
    Desde v0.2, a agregacao NUNCA rebaixa automaticamente o estado por
    corroboracao de evidencia negativa - isso foi removido de proposito
    (ver Principio 8 abaixo e DECISIONS.md). Um erro isolado, ou ate
    varios, so acende `possible_regression=True`; rebaixar o estado exige
    validacao deliberada (Secao 17, regra 8 - `TARGETED_REGRESSION_CHECK`),
    nunca a agregacao sozinha
    (`test_regression_never_auto_downgrades_even_with_many_negatives`).
    Evidencia negativa `qualified_incidental` nunca acende nem esse
    sinal - vira apenas hipotese de validacao, nunca uma seta de
    regressao (`test_incidental_negative_never_sets_possible_regression`).

13. **Evidencia contraditoria deve poder coexistir.**
    `EvidenceType.CONTRADICTORY` e um tipo de primeira classe; o estado
    vai para `insufficient_evidence` (incerteza), nunca e descartado
    (`test_T6_contradictory_response_creates_uncertainty_routed_to_validate`).

14. **Incerteza e um estado legitimo.**
    `insufficient_evidence` e `inconclusive` sao valores validos, nao
    erros. Desde v0.2 isso vai mais fundo: um achado `inconclusive`,
    com confianca abaixo do minimo configurado na RuleVersion, ou com
    `alternative_cause` pendente, e EXCLUIDO da agregacao inteira - nunca
    promove, nunca rebaixa, e nunca dispara revisao de memoria FSRS
    (`evaluate_recall_eligibility`, `test_inconclusive_finding_never_updates_state`,
    `test_pending_alternative_cause_never_updates_state`).

15. **A coleta de evidencia deve ser subordinada a aprendizagem.**
    O Decisor escolhe a atividade pedagogica (Secao 17) antes de
    qualquer preocupacao de "quanto dado coletar"; o MockProvider nunca
    otimiza para maximizar volume de evidencia.

16. **Nenhum fornecedor de IA deve ser estruturalmente indispensavel.**
    `providers/base.py` define uma interface abstrata; `MockProvider`
    prova que o motor inteiro roda sem nenhum fornecedor real e sem
    nenhum acesso de rede
    (`test_P11_offline_execution_fails_if_network_is_attempted`).

17. **Nenhuma mudanca de fornecedor, infraestrutura ou componente pago
    pode ocorrer automaticamente.**
    `web/deps.py` define o provider ativo como uma unica linha de
    configuracao explicita — nunca ha fallback automatico entre
    providers.

18. **Mudanca de regra pedagogica cria nova versao.**
    `RuleVersion` e IMUTAVEL de verdade no banco desde v0.2 (trigger SQL);
    qual versao esta ativa vive num ponteiro mutavel separado
    (`active_rule_version`), nunca numa coluna da propria linha - assim
    "ativar" uma versao nunca precisa reescrever/reetiquetar nenhuma
    versao antiga nem as projecoes que ela produziu
    (`test_P6_two_rule_versions_with_different_config_diverge`).

19. **Falha de componente externo nao pode corromper estado valido.**
    Falha do Tutor, do Avaliador, do FSRS ou do backup nunca apaga ou
    corrompe evidencia ja gravada (Secao 25;
    `test_P1_invalid_evaluation_never_alters_projection_or_memory`,
    `test_P5_reconstruction_preserves_history_and_is_atomic_on_failure`,
    `test_P7_memory_review_log_failure_never_alters_memory_state`, testes
    de `test_backup.py`/`test_restore.py`).

20. **O sistema deve explicar por que tomou uma decisao.**
    Todo `DecisionEvent` tem `justification` obrigatoria e nao vazia,
    reforcada por CHECK constraint no banco
    (`check_decision_events_without_justification`).

## Principios adicionados pelo pacote de correcao v0.2

Estes nao substituem os 20 originais - sao reforcos que o Red Team
pos-entrega tornou explicitos porque a V0.1 os cumpria so parcialmente.

21. **Reavaliacao deve ser idempotente, nunca duplicadora.**
    Uma `RawInteraction` cuja avaliacao nunca completou
    (`evaluation_status='pending'`) pode ser reprocessada quantas vezes
    for preciso; uma ja `completed` nunca gera evidencia nova. A
    transicao e atomica (`try_claim_evaluation`), o que impede duas
    tentativas concorrentes de completarem a mesma avaliacao duas vezes
    (`test_P2_reprocessing_after_evaluator_failure_is_idempotent`).

22. **Independencia de evidencia e uma propriedade do CONTEXTO, nao de um
    identificador arbitrario.**
    Nenhum chamador pode "provar" independencia so passando um id de
    cluster diferente. O cluster e computado no SERVIDOR a partir de
    sessao + tipo de atividade + prompt normalizado
    (`evidence/clustering.py`,
    `test_P4_different_activity_ids_same_context_collapse_into_one_cluster`).

23. **Revisao de memoria (FSRS) exige um evento explicito, nao um efeito
    colateral de qualquer resposta.**
    `MemoryAdapter.observe_and_review` so e chamado quando
    `evaluate_recall_eligibility` confirma recuperacao PLANEJADA,
    intervalo relevante desde a ultima revisao, e avaliacao valida e
    conclusiva (`central_universal/memory/fsrs_adapter.py`).
