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

## Principios adicionados pelo pacote de correcao v0.2.1

Uma segunda auditoria pos-entrega, sobre o commit que fechou a v0.2,
encontrou que os principios acima ainda eram satisfeitos so parcialmente
em seis pontos. Estes principios nao substituem os 23 anteriores - sao
reforcos que essa auditoria tornou explicitos.

24. **"Recuperacao planejada" e uma consequencia da decisao pedagogica,
    nunca um dado informado por um chamador.**
    `Activity.is_planned_recall` e SEMPRE derivado de
    `action.decision_type == DecisionType.SCHEDULE_RECALL` dentro de
    `SessionOrchestrator.start_activity` - nunca um parametro que outro
    codigo (nem mesmo um teste) possa simplesmente setar. Isso garante que
    o PRIMEIRO card FSRS do sistema so pode nascer pela mesma sequencia de
    chamadas que a interface real usa
    (`test_first_fsrs_card_is_born_through_normal_application_flow`).

25. **A nota de uma recuperacao vem da avaliacao DAQUELA tentativa
    especifica, nunca de um mapeamento generico de resultado.**
    `ProductionResult` classifica a interacao bruta; a nota FSRS (1-4)
    exige uma `EvidenceAssessment` de RETENTION, com relacao `target`,
    confianca suficiente e intervalo desde a ultima revisao respeitando o
    minimo definido pela `RuleVersion` em uso
    (`recall_min_confidence`/`recall_min_interval_seconds`/
    `recall_rating_easy_min_confidence`/`recall_rating_good_min_confidence`
    em `AggregationConfig`, documentados na propria `RuleVersion` -
    `evidence.rule_versions.build_v0_2_1_rule_version`). Mudar esses
    numeros e criar uma nova `RuleVersion`, nunca editar uma constante de
    modulo (reforco direto do Principio 18).

26. **Reprocessar uma interacao pendente nunca confia em dados frescos do
    chamador, e uma chave de idempotencia identifica UMA submissao, nunca
    conteudo novo.**
    `EvaluatorInput` e sempre montado a partir da `RawInteraction`
    PERSISTIDA, mesmo quando a chamada que reprocessa passa parametros
    (por exemplo, `tutor_output_text`) diferentes dos da primeira
    tentativa. Uma `idempotency_key` reutilizada com atividade, sessao ou
    conteudo semanticamente relevante diferente e sempre rejeitada
    (`IdempotencyConflictError`) - nunca aceita silenciosamente escolhendo
    uma das duas versoes. Essa garantia sobrevive a um reinicio completo
    do processo, porque vem do banco, nunca de estado em memoria
    (`test_idempotency_conflict_rejected_after_process_restart`).

27. **Migracao de esquema e restauracao de backup sao operacoes de
    fronteira: atomicas, sem perda silenciosa, e visiveis ao operador
    quando falham.**
    Uma migration inteira aplica-se por completo ou nao aplica nada
    (`_apply_migration_atomically`); dados legados permitidos por um
    esquema anterior sao NORMALIZADOS para o novo, nunca descartados
    silenciosamente por um filtro `WHERE`. Uma restauracao de backup so
    prossegue depois de garantir exclusividade real no banco ativo
    (recusando-se explicitamente diante de uma conexao concorrente) e de
    tratar o WAL e seus arquivos auxiliares - e seu resultado (sucesso ou
    falha, com o motivo) e sempre mostrado ao usuario, nunca descartado
    apos o redirect.

## Principios adicionados pelo pacote de correcao v0.2.2

Uma terceira auditoria pos-entrega, sobre o commit que fechou a v0.2.1,
encontrou que os principios acima ainda eram satisfeitos so parcialmente
em quatro pontos. Estes principios nao substituem os 27 anteriores - sao
reforcos que essa auditoria tornou explicitos.

28. **"Mais recente" e sempre uma ordem monotonica explicita, nunca um
    timestamp de texto desempatado por um identificador aleatorio.**
    `CompetencyState.sequence_number` e um inteiro atribuido pelo
    REPOSITORIO na propria escrita (`MAX(sequence_number) + 1`), nunca
    informado por um chamador nem derivado de `computed_at`/`id`.
    `computed_at` continua existindo como o "quando" (para exibicao e
    auditoria), mas nunca mais decide qual linha e a atual - um timestamp
    de texto pode empatar sob escritas rapidas, e desempatar por um
    `uuid4()` aleatorio torna a escolha nao-deterministica
    (`test_competency_state_current_and_history_use_monotonic_sequence_frozen_clock`,
    reproduzido explicitamente com o relogio congelado).

29. **Um sinal so decide o que ele de fato mede - confianca do avaliador
    nunca e usada como substituto de facilidade de recuperacao.**
    A nota FSRS de uma recuperacao (Easy/Good/Hard) vem exclusivamente de
    sinais OBSERVAVEIS do processo de recuperacao da propria tentativa
    (`help_level`, `production_result`) - nunca de `confidence`, que mede
    o quanto o avaliador confia no proprio julgamento de classificacao,
    uma grandeza diferente. `confidence` continua sendo usada, mas
    exclusivamente como filtro de elegibilidade
    (`derive_recall_rating`/`evaluate_recall_eligibility` em
    `memory/fsrs_adapter.py`).

30. **A PRIMEIRA observacao de um fenomeno temporal tambem precisa de um
    intervalo minimo verificado - "nao ha revisao anterior para comparar"
    nunca e motivo para pular a checagem.**
    A primeira revisao de memoria de uma competencia (sem
    `memory_state.last_review_at` ainda) verifica o intervalo desde a
    PRIMEIRA evidencia registrada para a competencia
    (`first_review_min_interval_since_learning_seconds`), nao fica sem
    nenhuma checagem so por nao ter uma revisao anterior.

31. **Uma janela de exclusividade protege a operacao INTEIRA, do inicio
    da checagem ate o fim de cada caminho possivel - nao so o instante em
    que a exclusividade foi confirmada - e essa protecao precisa
    funcionar em qualquer sistema operacional, nao so no que foi testado
    primeiro.**
    Uma correcao inicial (v0.2.2) protegia a restauracao mantendo uma
    conexao SQLite presa com `BEGIN EXCLUSIVE` durante toda a copia/troca
    de arquivo - funcionava no Linux/macOS, mas quebrava no Windows
    (`os.replace` recusa substituir um arquivo que qualquer processo,
    inclusive o proprio, ainda tem aberto - `PermissionError: [WinError
    5]`, reportado por um usuario rodando a suite em Windows). A protecao
    correta e PORTATIL: um portao em memoria
    (`persistence.restore.is_restore_in_progress`) coordena a pausa no
    NIVEL DA APLICACAO durante toda a janela (troca ou reversao) - a
    unica porta de entrada de conexoes da camada web (`web/deps.py:get_conn`)
    recusa (503) qualquer requisicao nova enquanto ele estiver ligado -
    combinada com uma checagem de exclusividade no proprio banco ANTES de
    cada troca, mas SEMPRE fechada imediatamente, nunca mantida presa
    durante o `os.replace` em si
    (`test_restore_holds_no_sqlite_connection_open_across_os_replace`,
    `test_web.py::test_restore_rejects_new_requests_while_in_progress`).
    O mesmo principio de "nao deixar uma excecao escapar da fronteira"
    vale para o tratamento de falha: se a PROPRIA reversao tambem falhar,
    isso nunca propaga como excecao nao tratada - o banco original
    permanece preservado (a copia de seguranca so e apagada quando a
    reversao realmente terminou) e um resultado legivel descrevendo as
    duas falhas e sempre devolvido
    (`test_restore_never_raises_when_revert_itself_also_fails`).
    O mesmo principio vale para bookkeeping: o registro de uma migration
    em `schema_migrations` entra na MESMA transacao atomica do resto da
    migration, nunca como uma escrita separada depois que o esquema ja
    commitou.

32. **Coordenar exclusao entre threads exige uma primitiva que serializa
    "checar e agir" atomicamente - um booleano isolado, ligado e
    desligado por chamadores diferentes sem um lock compartilhado com
    quem le, NAO basta.**
    O mesmo usuario que validou o Principio 31 em Windows encontrou, la
    mesmo, dois problemas no portao: (a) `_pause_for_restore()` so
    ligava/desligava um `bool`, nunca impedindo uma SEGUNDA restauracao de
    tambem "entrar" enquanto a primeira ainda estava em andamento - quando
    a primeira terminava, desligava o portao mesmo com a segunda ainda
    tocando arquivos; (b) `get_conn()` consultava o portao e SO DEPOIS
    abria a conexao, dois passos sem nenhum lock em comum entre eles - uma
    restauracao podia comecar exatamente nesse meio-tempo. A correcao
    trocou o `bool` isolado por uma coordenacao leitor/escritor de verdade
    sobre um `threading.Condition` UNICO, compartilhado entre
    `reader_slot()` (usado por `get_conn`, registra "conexao aberta" e
    checa o portao como UMA operacao atomica) e `_pause_for_restore()`
    (rejeita uma segunda restauracao NA HORA, antes de tocar qualquer
    coisa, e so avanca depois que todos os leitores registrados
    terminarem) - nunca dois passos separados de nenhum dos dois lados
    (`test_restore_rejects_a_concurrent_second_restore_attempt`,
    `test_restore_waits_for_an_in_flight_request_before_touching_files`,
    ambos com threads reais e `threading.Event` para sincronizacao
    deterministica, nunca `sleep`).

33. **"Nunca deixar uma excecao escapar da fronteira" (Principio 31) vale
    para TODA a operacao, inclusive os passos que rodam antes de qualquer
    arquivo ser tocado - e reverter so faz sentido depois que algo de fato
    mudou.**
    O mesmo usuario, auditando o codigo do Principio 32 sem executar nada,
    encontrou que o callback `close_connections()` do chamador e a
    criacao da copia de seguranca (`shutil.copy2`) rodavam ANTES do bloco
    que converte falhas em `RestoreResult` - uma excecao ali escapava sem
    tratamento ate a rota web, quebrando a mesma promessa que o Principio
    31 ja fazia, so que num ponto anterior da operacao. A correcao isolou
    essa fase de preparacao no seu proprio tratamento de falha: qualquer
    excecao ali descarta uma copia de seguranca parcial e devolve um
    `RestoreResult(success=False, ...)` legivel - e, precisamente porque
    `_atomic_replace` nunca chegou a ser chamado nesse caminho, NUNCA
    tenta `_revert_to_safety_copy` (nao ha o que reverter quando nada
    mudou)
    (`test_restore_returns_readable_result_when_close_connections_fails`,
    `test_restore_returns_readable_result_when_safety_copy_creation_fails`).

34. **Uma falha de LIMPEZA nunca decide o resultado no lugar do estado
    real do sistema, e nunca escapa da mesma fronteira que o Principio 33
    ja protegia.**
    O mesmo usuario, auditando o codigo do Principio 33 sem executar nada,
    encontrou que os tres pontos onde a copia de seguranca do restore e
    removida (`safety_copy.unlink(...)`) chamavam a remocao direto, sem
    tratamento proprio - uma falha ali podia escapar como excecao nao
    tratada, e, no ponto mais critico, podia ocorrer DEPOIS que a
    restauracao ja tinha terminado com sucesso (troca, migrations e
    integrity_check todos bem-sucedidos): uma excecao ali impediria o
    `RestoreResult(success=True, ...)` de sequer ser construido,
    escondendo um sucesso real por tras de um detalhe de limpeza. A
    correcao isolou a remocao num helper que nunca levanta
    (`_safe_unlink`) e fez o `RestoreResult` em cada um dos tres pontos
    (falha de preparacao, reversao bem-sucedida, restauracao
    bem-sucedida) refletir o ESTADO REAL DO BANCO naquele ponto - nunca o
    sucesso ou falha da limpeza em si: falha na preparacao continua
    `success=False` mesmo que a limpeza tambem falhe (nada foi trocado);
    reversao bem-sucedida continua `success=False` (a restauracao falhou,
    so foi revertida) mesmo que a copia sobrando nao possa ser removida;
    e restauracao bem-sucedida continua `success=True` mesmo que a copia
    de seguranca sobressalente nao possa ser removida - so ganha uma nota
    na mensagem pedindo remocao manual
    (`test_restore_readable_result_when_preparation_failure_and_its_cleanup_both_fail`,
    `test_restore_readable_result_when_cleanup_after_successful_revert_fails`,
    `test_restore_still_reports_success_when_cleanup_after_successful_restore_fails`).

35. **Uma avaliacao gravada com sucesso e uma revisao de memoria
    concluida sao dois fatos DIFERENTES - "ja avaliada" nunca pode
    significar "nada mais a fazer" se a memoria ainda estiver pendente.**
    Com o restore validado em Linux e Windows, o mesmo usuario passou a
    auditar o resto do fluxo de aprendizagem e encontrou que
    `SessionOrchestrator.submit_interaction` tratava
    `raw_interaction.evaluation_status == COMPLETED` como sinonimo de
    "nada a reprocessar" - correto para a AVALIACAO em si (Principio de
    idempotencia, T8), mas incorreto para a revisao de MEMORIA: se uma
    submissao anterior tivesse avaliado com sucesso mas o FSRS falhasse
    DEPOIS, reenviar a mesma resposta saia direto nesse atalho sem
    sequer tentar a memoria de novo, perdendo a revisao DEFINITIVAMENTE.
    A correcao extraiu a logica de observacao/revisao para
    `_review_memory_if_pending`, chamada tanto na primeira quanto em
    qualquer submissao repetida, mas gated por um fato verificavel e
    unico - existe um `MemoryObservation` gravado para esta interacao? -
    nunca pelo status da avaliacao: se nao existir, tenta (de novo, se
    for o caso); se ja existir, nunca tenta de novo (o FSRS so pode
    revisar uma interacao uma unica vez)
    (`test_memory_review_retries_after_fsrs_failure_without_duplicating`).

36. **Uma suspeita de regressao e permanente ate uma validacao
    DELIBERADA resolve-la - nenhuma evidencia comum, por mais recente
    que seja, pode silenciar sozinha o que o Principio 12 exige
    validacao explicita para resolver.**
    A mesma auditoria encontrou que `classify_dimension` computava
    `possible_regression` comparando qual evidencia TARGET era mais
    recente (positiva forte vs. negativa/contraditoria, ja deduplicadas
    por cluster) - se a evidencia positiva fosse a mais nova, a suspeita
    simplesmente nao acendia na proxima recomputacao, mesmo que a
    evidencia positiva viesse de uma atividade COMUM, nunca de uma
    `TARGETED_REGRESSION_CHECK` deliberada (Secao 17, regra 8). Na
    pratica, isso implementava exatamente o tipo de resolucao automatica
    que o Principio 12 proibe - so que como um SILENCIAMENTO em vez de um
    REBAIXAMENTO, mais dificil de perceber porque o tier nunca caia, so a
    bandeira de suspeita desaparecia silenciosamente. A correcao removeu
    essa comparacao: existir QUALQUER evidencia TARGET negativa/
    contraditoria ainda no event log (deduplicada por cluster) e
    suficiente para manter o sinal ligado, independente de quantas
    evidencias positivas comuns cheguem depois - resolver isso de
    verdade continua sendo trabalho futuro nao implementado na V0 (ja
    documentado em `KNOWN_LIMITATIONS.md`), nunca algo que a propria
    agregacao decide sozinha
    (`test_common_positive_evidence_after_regression_never_silences_the_signal`).

37. **Nao ha o que regredir de um dominio que ainda nao existia - uma
    regressao so pode ser medida em relacao ao momento em que o dominio
    foi demonstrado, nunca em relacao ao estado final recalculado com
    TODA a evidencia.**
    O usuario auditou o commit que implementou o Principio 36 e reproduziu
    a sequencia "erro inicial -> tres acertos independentes": o resultado
    era `consolidated` com `possible_regression=True`, mas o erro
    aconteceu ANTES de qualquer evidencia positiva sequer existir - a
    correcao anterior tinha ido longe demais ao remover TODA nocao de
    timing, sinalizando regressao para qualquer evidencia negativa,
    independente de quando ela ocorreu em relacao ao dominio ja
    demonstrado. A correcao ajustou a condicao: uma evidencia TARGET
    negativa/contraditoria so conta como regressao se, NO MOMENTO em que
    ela ocorreu, ja existia evidencia positiva forte suficiente (>=
    `demonstrated_min_clusters` clusters, ou >= `retention_demonstrated_min_days`
    dias para retencao) - calculado usando so a evidencia com `created_at`
    estritamente anterior aquela evidencia negativa especifica, nunca o
    estado final. Um erro cronologicamente anterior a qualquer dominio
    demonstrado e ruido normal de aquisicao, nunca regressao; um erro
    posterior ao dominio ja demonstrado continua sinalizando regressao,
    permanente, exatamente como o Principio 36 estabelece
    (`test_error_before_any_demonstrated_mastery_is_not_a_regression`,
    `test_error_after_demonstrated_mastery_is_still_a_regression`).

38. **Um caminho de recuperacao que so existe no servico e invisivel -
    a interface tem que dar ao usuario uma forma real de acionar a
    retentativa, e essa retentativa tem que preservar o tempo real dos
    eventos, nunca o tempo do clique que a disparou.**
    O Principio 35 corrigiu o SERVICO para retentar a revisao de memoria
    numa submissao repetida - mas a mesma auditoria encontrou que a rota
    web (`POST /session/{id}/answer`) descartava o resultado inteiro
    (inclusive um `MemoryReviewError`), e que a pagina da sessao esconde o
    formulario de resposta assim que a interacao existe, deixando NENHUM
    caminho visivel para o usuario sequer saber que uma revisao ficou
    pendente, muito menos recupera-la. A correcao deu a interface web um
    caminho completo: a pagina calcula `memory_review_pending` (atividade
    planejada como recuperacao, ja respondida, sem `MemoryObservation`
    gravado ainda) e mostra um formulario de retentativa que reenvia
    EXATAMENTE a mesma resposta ja registrada - nunca uma nova. Crucial:
    a retentativa usa `RawInteraction.occurred_at` (o horario da
    tentativa ORIGINAL) como `review_datetime` do FSRS, nunca o horario
    do clique de retentativa - que pode acontecer muito depois e, se
    usado, inflaria artificialmente o intervalo que o FSRS enxerga entre
    o momento real da tentativa e o momento em que a revisao foi
    finalmente registrada
    (`test_memory_review_recovery_is_visible_and_retryable_over_http`).
