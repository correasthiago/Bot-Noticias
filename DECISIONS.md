# Decisoes tecnicas — Central Universal V0.2

Decisoes puramente implementacionais e reversiveis, tomadas de forma
autonoma pelo executor conforme autorizado pelo pacote de implementacao.
Nenhuma delas altera pedagogia, arquitetura aprovada, governanca,
persistencia ou custos.

As decisoes 1-11 sao da V0.1 original. A secao "Pacote de correcao v0.2"
no final deste documento registra as decisoes tomadas para corrigir os
pontos encontrados no Red Team pos-entrega - leia-a junto com
`PEDAGOGICAL_CONSTITUTION.md` (principios 21-23) antes de mexer em
`evidence/`, `memory/` ou `persistence/`.

## 1. `EvidenceAssessment.classification` usa `EvidenceType`, nao `CompetencyDimensionState`

A especificacao (Secao 16) diz que o avaliador retorna uma
"classificacao", mas nao especifica seu dominio de valores. Modelar isso
como `CompetencyDimensionState` (o mesmo enum do estado agregado da
competencia) seria uma inconsistencia conceitual grave: um avaliador
julga UMA evidencia, nunca decide o estado agregado — isso e trabalho
exclusivo de `evidence.aggregation` (Principio 2 e 8). Escolhi
`EvidenceType` (`positive`/`negative`/`contradictory`/`inconclusive`),
que e exatamente o vocabulario da Secao 8. Documentado tambem em
`DATA_MODEL.md`.

## 2. Algoritmo de agregacao: contagem de evidencia independente, nao score numerico

A especificacao pede explicitamente para NAO cristalizar pesos/thresholds
de "mastery" sem dados reais para calibra-los (Secao 33, Red Team do
usuario). Implementei `classify_dimension` como uma maquina de estados
baseada em CONTAGEM de clusters de evidencia independente (2 para
`demonstrated`, 3 para `consolidated`; para `retention`, dias distintos
em vez de clusters). Esses numeros (2 e 3) sao um ponto de partida
deliberadamente simples e auditavel, nao uma calibracao com dados — a V0
deve PRODUZIR os dados que permitirao calibrar isso depois, nao assumir
uma calibracao que ainda nao existe. Isso esta marcado no proprio
docstring de `aggregation.py`.

> **SUPERADO pelo pacote de correcao v0.2** (item 8): estes numeros
> continuam os mesmos, mas deixaram de ser constantes de modulo e agora
> vem de `RuleVersion.config_json` via `AggregationConfig`. Ver secao
> "Pacote de correcao v0.2" #8 abaixo.

## 3. Regressao so "corrobora" (derruba estado) com >=2 evidencias negativas recentes

Principio 12 diz que um erro isolado nao apaga dominio anterior. Precisava
de um numero concreto para "isolado" vs. "corroborado". Escolhi: 1
evidencia negativa/contraditoria entre as 3 mais recentes marca
`possible_regression=True` mas NAO rebaixa o estado; 2 ou mais rebaixam em
UM nivel (nunca direto para `insufficient_evidence`, para nao apagar todo
o historico de uma vez). E uma regra simples e auditavel, nao uma
formula estatistica.

> **REVERTIDO pelo pacote de correcao v0.2** (item 8): o Red Team
> pos-entrega apontou, corretamente, que "corroboracao automatica" ainda
> e um rebaixamento automatico - so com um limiar mais alto. A partir da
> v0.2, a agregacao NUNCA rebaixa o tier: `possible_regression` e um
> sinal puro, sempre pendente de validacao deliberada. Ver secao "Pacote
> de correcao v0.2" #8.

## 4. Uma unica FSRS `Card` por competencia (nao por dimensao)

A especificacao nao diz explicitamente se o scheduler de memoria e por
competencia ou por dimensao. Escolhi um card FSRS por COMPETENCIA: a
retencao espacada e sobre "lembrar a competencia", nao sobre uma dimensao
especifica isolada, e seis schedulers independentes por competencia
multiplicariam a complexidade de agendamento sem beneficio pedagogico
claro na V0. Se dados futuros mostrarem que dimensoes precisam de
curvas de esquecimento diferentes, isso e uma extensao aditiva no
`memory/fsrs_adapter.py`, sem tocar no resto do motor.

## 5. Mapeamento `ProductionResult -> nota FSRS (1-4)`

Nenhuma biblioteca de FSRS aceita nosso vocabulario de 6 resultados de
producao (Secao 10) diretamente — ela espera Again/Hard/Good/Easy.
Mapeei:

| ProductionResult | Rating FSRS |
|---|---|
| spontaneous_correct | Easy |
| spontaneous_self_correction | Good |
| correct_after_hint | Hard |
| correct_after_external_correction | Again |
| incorrect | Again |
| inconclusive | (nenhum review e feito) |

Autocorrecao espontanea fica entre Easy e Hard deliberadamente (Good), em
vez de tratada como acerto perfeito, seguindo a Secao 10. `inconclusive`
NAO gera review de memoria: incerteza e um estado legitimo (Principio 14)
e nao deve forcar uma nota. Isto e um mapeamento inicial documentado, sujeito a
revisao quando houver dados reais de aprendizes.

> **REFINADO pelo pacote de correcao v0.2** (item 1): este mapeamento
> continua existindo (`rating_from_production_result`), mas deixou de ser
> o GATILHO da revisao FSRS. Desde a v0.2, ele so e consultado DEPOIS que
> `evaluate_recall_eligibility` confirma que a tentativa e uma
> recuperacao planejada, com intervalo relevante e avaliacao valida e
> conclusiva. Ver secao "Pacote de correcao v0.2" #1-2.

## 6. `DecisionService.preview_routing` sem gravar `DecisionEvent`

A tela inicial e o mapa de competencias precisam saber quantas
competencias estao em SKIP/VALIDATE/STUDY, mas rodar `decide()` (que
grava um `DecisionEvent`) a cada carregamento de pagina inundaria o log de
auditoria com decisoes que nunca viraram acao real. Separei
`preview_routing` (so leitura) de `decide` (leitura + escrita), e o
orquestrador so chama `decide` para a competencia efetivamente escolhida.

## 7. Pin de `fastapi==0.115.6` / `starlette==0.41.3`

Durante o desenvolvimento, a combinacao mais recente disponivel no
momento (`fastapi` 0.141 / `starlette` 1.6) quebrava
`Jinja2Templates.TemplateResponse` sempre que o contexto continha um
valor nao hashavel (por exemplo, um `dict` — o que e o caso normal de
qualquer template com dados estruturados), com
`TypeError: unhashable type: 'dict'` vindo do cache interno do Jinja2.
Fixei as duas dependencias numa combinacao estavel conhecida em vez de
mudar a forma de passar contexto (o que so mascararia o problema).
Revisar este pin quando uma versao mais nova corrigir o bug.

## 8. IDs = `uuid4().hex`

Secao 6 exige que IDs nao dependam de nomes humanos mutaveis. `uuid4`
hexadecimal e opaco, sem qualquer dependencia de nome, ordem de insercao
ou timestamp. Nao usei ULID/UUID7 (que embutem tempo, permitindo
ordenacao lexicografica) para nao adicionar uma dependencia externa numa
V0 cujo volume de dados (uma pessoa) nunca vai sentir falta de ordenacao
por ID.

## 9. `raw_interaction`/`evidence_event`/`evidence_assessment` imutaveis via TRIGGER, nao so por convencao

Poderia ter confiado em "o codigo da aplicacao nunca faz UPDATE nessas
tabelas". Preferi um TRIGGER `BEFORE UPDATE`/`BEFORE DELETE` que aborta a
transacao com `RAISE(ABORT, ...)`: a garantia fica no banco, sobrevive a
bugs futuros no codigo Python e e testavel diretamente
(`test_raw_interaction_is_immutable`).

## 10. Backup usa a Online Backup API do SQLite (`sqlite3.Connection.backup()`), nunca `shutil.copy`

Especificado explicitamente na Secao 26. `Connection.backup()` no modulo
padrao do Python e a mesma Online Backup API nativa do SQLite: produz um
snapshot consistente mesmo com o banco WAL ativo e sendo escrito. Copiar
o arquivo bruto poderia capturar um estado inconsistente entre o arquivo
principal e o WAL.

## 11. Um card FSRS por competencia + retencao ainda exige >=2/3 dias distintos na agregacao

Isto pode parecer redundante (o FSRS ja modela esquecimento), mas nao e:
o FSRS decide QUANDO vale a pena provocar uma nova tentativa de
recuperacao; ele nao decide se a dimensao pedagogica "retention" pode ser
classificada como demonstrada/consolidada. Essas sao responsabilidades
deliberadamente separadas (Secao 13: "FSRS deve determinar/agilizar
quando vale provocar recuperacao novamente, nao declarar dominio").

---

## Pacote de correcao v0.2 (Red Team pos-entrega)

Apos a V0.1 ser entregue, um Red Team independente revisou a
implementacao e encontrou 16 pontos onde o codigo cumpria a letra da
especificacao mas nao o espirito completo. Esta secao documenta a
decisao tomada para cada um. Todos sao classificados como mudancas
"puramente implementacionais e reversiveis" - nenhuma altera pedagogia,
arquitetura aprovada, governanca ou custos; sao correcoes de rigor de
engenharia sobre principios ja aprovados.

### #1-2. Remocao do gatilho direto ProductionResult -> FSRS

**Problema:** qualquer interacao com um ProductionResult mapeavel para
rating FSRS disparava uma revisao, mesmo em atividades de ESTUDO comuns
(nao recuperacao), sem avaliacao valida, ou sobre evidencia mere_presence.

**Decisao:** criei `MemoryObservation` (tabela + entidade) como o UNICO
evento que autoriza `MemoryAdapter.observe_and_review`. A elegibilidade
(`evaluate_recall_eligibility` em `memory/fsrs_adapter.py`) exige,
simultaneamente: (a) `Activity.is_planned_recall=True` - a atividade foi
CRIADA como uma tentativa de recuperacao, nao um estudo comum; (b)
intervalo relevante desde a ultima revisao (>0 dias, calculado contra
`MemoryState.last_review_at`); (c) uma `EvidenceAssessment` valida e
conclusiva associada (nao None, nao inconclusive, sem alternative_cause
pendente); (d) relacao != `mere_presence`. `is_planned_recall` e
setado pelo orquestrador a partir do mesmo booleano `memory_recall_due`
que ja alimentava a rota VALIDATE/"retention_pending" no Decisor - a
mesma informacao que decide "vale a pena testar retencao agora" tambem
decide "esta tentativa conta como observacao de memoria".

### #3. Escrita atomica de memory_state + memory_review_log + memory_observation

**Decisao:** as tres gravacoes agora vivem dentro de um unico
`with transaction(self.repos.conn):` em `MemoryAdapter.observe_and_review`.
Testado explicitamente com `@pytest.mark.parametrize` simulando falha em
CADA uma das tres (`test_failure_at_each_write_leaves_prior_state_byte_identical`),
comparando `fsrs_card_json` (string) antes/depois byte a byte.

### #4. Separacao EvidenceEvent / EvidenceAssessment

**Decisao:** `EvidenceEvent` perdeu a coluna `evidence_type` (que era,
na pratica, uma classificacao duplicada). Ele agora carrega SOMENTE
fatos observados e condicoes da tentativa: `dimension`, `relation`,
`help_level`, `production_result`, `evidence_cluster_id`. Classificacao,
confianca, causa alternativa, conclusividade (`inconclusive`) e
`rule_version_id` vivem exclusivamente em `EvidenceAssessment`. Um CHECK
constraint no banco (`evidence_assessment`) reforca que
`inconclusive=1 <=> classification='inconclusive'` - a mesma regra
tambem e validada na fronteira de entrada (`evaluator.contract.validate_finding`),
entao uma resposta de avaliador semanticamente inconsistente e rejeitada
duas vezes (fronteira de aplicacao + banco).

### #5. Avaliacoes idempotentes / reprocessaveis

**Decisao:** `raw_interaction` ganhou `evaluation_status`
(`pending`/`completed`). A transicao e feita por
`RawInteractionRepository.try_claim_evaluation`, um `UPDATE ... WHERE
evaluation_status = 'pending'` cujo `rowcount` diz se ESTA chamada foi
quem completou a avaliacao. Isso torna a idempotencia uma propriedade do
BANCO (funciona mesmo sob concorrencia real, gracas ao lock de escrita do
SQLite em `BEGIN IMMEDIATE`), nao so uma checagem de aplicacao antes de
escrever. Uma interacao `pending` (nova, ou cuja avaliacao anterior
falhou) pode sempre ser reprocessada; uma `completed` nunca gera
evidencia nova. A trigger de imutabilidade de `raw_interaction` foi
reescrita para permitir EXATAMENTE essa transicao e nenhuma outra.

### #6-7. Clusters computados no servidor, com teto de contribuicao

**Problema:** o codigo original aceitava um `evidence_cluster_id`
arbitrario vindo do CHAMADOR (na pratica, o orquestrador usava
`activity_id`, mas nada impedia outro caminho de inventar qualquer
string). Isso significava que "independencia" de evidencia era, na
pratica, uma alegacao no-confiavel.

**Decisao:** criei `evidence/clustering.py` com `resolve_cluster`,
que calcula deterministicamente um cluster a partir de
`(session_id, activity_type, prompt normalizado)` e persiste isso numa
tabela `evidence_cluster` propria (com `UNIQUE(session_id,
context_signature)`). `EvidenceService.record_interaction` NAO aceita
mais um `evidence_cluster_id` como parametro - o cluster e sempre
resolvido internamente a partir da `Activity` e da sessao. Isso significa
que: (a) duas atividades DIFERENTES (ids diferentes) com o mesmo prompt
na mesma sessao caem no MESMO cluster - impossivel fingir independencia
so criando novas atividades; (b) a mesma atividade repetida em sessoes
DIFERENTES (dias diferentes) gera clusters DIFERENTES, preservando a
contagem de dias distintos que a retencao longitudinal exige. Na
agregacao, `_dedupe_latest_per_cluster` garante no maximo uma
contribuicao por (cluster, direcao) em cada um dos quatro "baldes"
(positivo forte, positivo fraco, negativo/contraditorio-alvo,
contraditorio) antes de qualquer contagem de threshold.

*Limitacao reconhecida:* o cluster e escopado a UMA sessao (nao a uma
janela de tempo explicita dentro dela). Documentado em
KNOWN_LIMITATIONS.md.

### #8. Fim do rebaixamento automatico; thresholds saem do codigo, entram em RuleVersion.config_json

**Decisao dupla:**

1. Removi inteiramente a logica de "corroboracao" que rebaixava o tier
   com >=2 evidencias negativas recentes (Decisao #3 original, revertida).
   `possible_regression` agora e puramente um SINAL: fica `True` quando a
   evidencia TARGET mais recente (comparada contra a ultima positiva
   forte) e negativa/contraditoria, e `False` quando uma positiva mais
   recente ja a superou. Nunca move `state`. Ate existir uma forma
   calibrada e deliberada de confirmar regressao (fora do escopo da V0),
   ela fica "suspeita pendente de validacao" para sempre, refletindo o
   Principio 12 ("um erro isolado nao apaga dominio anterior") levado as
   ultimas consequencias.
2. `demonstrated_min_clusters`, `consolidated_min_clusters`,
   `retention_demonstrated_min_days`, `retention_consolidated_min_days`,
   `contradiction_outweigh_ratio` e `min_confidence_for_aggregation`
   saem de constantes de modulo e viram campos de `AggregationConfig`,
   serializados em `RuleVersion.config_json`. `classify_dimension` recebe
   a config como parametro; o default (`AggregationConfig()`) so existe
   como fallback de compatibilidade, nunca como a fonte de verdade em
   producao - o bootstrap sempre cria uma RuleVersion explicita
   (`evidence/rule_versions.build_v0_2_0_rule_version`).

### #9. RuleVersion realmente imutavel + uma unica versao ativa

**Decisao:** adicionei trigger `BEFORE UPDATE`/`BEFORE DELETE` em
`rule_version` (agora tao imutavel quanto `raw_interaction`). Como isso
tornaria impossivel "desativar" a versao anterior via UPDATE, criei uma
tabela-ponteiro separada e MUTAVEL, `active_rule_version` (linha
singleton, `id=1`), que e a UNICA fonte de verdade sobre qual versao esta
ativa agora. `RuleVersion.algorithm_version` documenta qual FORMA de
codigo sabe interpretar aquele `config_json` - uma protecao contra o
cenario que o Red Team descreveu ("uma nova versao simplesmente
reetiqueta projecoes produzidas pelas regras antigas"): ativar uma nova
versao NUNCA reescreve `competency_state` existente; so afeta
recomputos futuros.

### #10. Geracoes de projecao substituem reconstrucao destrutiva

**Decisao:** `competency_state` ganhou `generation_id` (FK para
`projection_generation`) e virou append-only IMUTAVEL (trigger). A
"reconstrucao total" (`recompute_all_from_log`) deixou de fazer `DELETE
FROM competency_state`; agora ela cria uma `ProjectionGeneration` nova
(`status='building'`), recalcula TODOS os pares (competencia, dimensao)
dentro dela, valida (confere que o numero de linhas bate com o esperado),
e SO ENTAO ativa a geracao (move o ponteiro `active_projection_generation`
e marca a geracao anterior como `superseded`) - tudo dentro de UMA
transacao SQL. Se qualquer passo falhar, a transacao inteira e desfeita e
a geracao ativa anterior nunca e tocada. Geracoes antigas nunca sao
apagadas: ficam no banco para auditoria/historico completo. Escrita
INCREMENTAL (apos uma nova avaliacao) continua simples - so insere uma
nova linha na geracao ja ativa, sem criar geracao nova a cada evidencia.

### #11. Validacao semantica de `inconclusive`/confianca/causa alternativa

**Decisao:** `evaluator.contract.validate_finding` agora rejeita
(`InvalidEvaluatorOutput`) qualquer finding com `inconclusive=True` e
`classification != 'inconclusive'` - o mesmo invariante que o CHECK do
banco reforca. Independentemente disso, `EvidenceService.recompute_state`
filtra (nunca inclui na agregacao) qualquer assessment com
`inconclusive=True` OU `alternative_cause` preenchido, e
`evaluate_recall_eligibility` aplica o mesmo filtro antes de autorizar
qualquer revisao FSRS. `min_confidence_for_aggregation` (configuravel via
RuleVersion) filtra achados de confianca baixa do mesmo jeito.

### #12. Ciclos de pre-requisito bloqueados na escrita

**Decisao:** `PrerequisiteRepository.insert` agora roda uma checagem de
alcancabilidade (BFS a partir do candidato a pre-requisito) ANTES de
inserir, e levanta `PrerequisiteCycleError` (self-loop ou ciclo indireto)
sem tocar o banco. `integrity.checks.check_prerequisite_cycles` continua
existindo como defesa adicional, para o caso hipotetico de algo escrever
SQL bruto contornando o repositorio (testado explicitamente em
`test_integrity_check_still_catches_a_cycle_that_bypassed_the_repository`).

### #13. Imutabilidade estendida

**Decisao:** adicionei triggers de imutabilidade para `rule_version`,
`decision_event`, `provider_event`, `memory_review_log` e `backup_event`.
A UNICA excecao documentada e formal e a propria `memory_state` (Principio
explicitamente NAO imutavel - e um ponteiro "card atual" por competencia,
atualizado a cada review; seu HISTORICO completo e imutavel em
`memory_review_log`) e as duas tabelas-ponteiro (`active_rule_version`,
`active_projection_generation`), que sao bookkeeping mutavel por design,
nao fatos historicos. `projection_generation.status` tambem e mutavel
(e uma maquina de estados de ciclo de vida: building -> active ->
superseded), mas as LINHAS de `competency_state` que cada geracao produz
sao imutaveis.

### #14. Restore operacional real + backup automatico

**Decisao:** `persistence/restore.py` implementa
`restore_from_backup(db_path, backup_path)`: valida o snapshot
(`PRAGMA integrity_check` + confere que parece um banco da Central
Universal) ANTES de tocar em qualquer coisa; copia para um arquivo de
staging; troca atomicamente com `os.replace` (atomico no mesmo
filesystem); reabre, roda migrations e `integrity_check`; se qualquer
passo falhar, reverte a partir de uma copia de seguranca feita antes da
troca. Backup automatico: `persistence.backup.maybe_run_automatic_backup`
dispara um backup no fim de cada sessao (`SessionOrchestrator.end_session`),
no maximo uma vez por hora (`AUTOMATIC_BACKUP_MIN_INTERVAL_HOURS`) -
simples de proposito, sem agendador/cron, consistente com a escala de um
unico usuario local.

### #15. Integrity check ampliado

**Decisao:** alem das checagens da V0.1, adicionei
`check_generation_consistency` (no maximo uma geracao `active`, ponteiro
valido, nenhuma `competency_state` orfa de geracao),
`check_assessment_origin_consistency` (`last_evidence_assessment_id`
sempre aponta para uma avaliacao da MESMA competencia/dimensao) e
`check_memory_card_review_log_consistency` (o `last_review_at` do card
bate com o `MemoryReviewLog` mais recente). `check_state_matches_recomputation`
foi reescrito para recalcular usando a `AggregationConfig` EXATA da
`RuleVersion` que o estado persistido diz ter usado, e agora compara
`state`, `possible_regression` E `has_unresolved_contradiction` - nao so
o estado.

### #16. Red Team reescrito como testes de principio

**Decisao:** `tests/test_redteam.py` foi reescrito do zero. Os antigos
T1-T15 descreviam comportamentos do algoritmo v1 (alguns dos quais, como
"corroboracao rebaixa apos 2 negativas", foram REVERTIDOS pela propria
correcao). O arquivo novo tem 11 testes (`P1`-`P11`), um por item exigido
no pacote de correcao, com nomes descritivos em vez de numeros que
perderiam sentido na proxima revisao. Os principios do algoritmo v1 que
CONTINUAM validos (retencao longitudinal, independencia A0/A1,
mere_presence, contradicao, erro isolado nao apaga dominio, double-submit)
permanecem cobertos - so nao duplicados neste arquivo, ja que vivem em
`test_evidence_aggregation.py`, `test_evidence_service.py`,
`test_memory_adapter.py` e `test_orchestration.py`.

---

## Pacote de correcao v0.2.1 (segunda auditoria pos-entrega)

Uma segunda auditoria, sobre o commit que fechou o pacote de correcao
v0.2, encontrou seis pontos onde a v0.2 ainda nao atendia ao criterio de
aceite - mesmo com os 100 testes daquele pacote passando. Esta secao
documenta a decisao para cada um. Como no pacote v0.2, todas sao mudancas
"puramente implementacionais e reversiveis" - nenhuma altera pedagogia,
arquitetura aprovada, governanca ou custos. A nova politica de
rating/intervalo de recuperacao (itens #2 abaixo) esta registrada em
`RuleVersion` via `evidence.rule_versions.build_v0_2_1_rule_version` -
ver PEDAGOGICAL_CONSTITUTION.md (Principio 24) e DATA_MODEL.md.

### #1. O primeiro card FSRS nasce pelo fluxo normal da aplicacao

**Problema:** `is_planned_recall` so podia ficar `True` via
`memory_recall_due`, que exigia um `MemoryState` JA existente - que so
podia ser criado por `observe_and_review`, que exigia
`is_planned_recall=True`. Um bootstrapping circular: nenhum card FSRS
podia nascer pelo fluxo real da aplicacao, so por um teste que setava
`is_planned_recall=True` diretamente.

**Decisao dupla:**

1. `SessionOrchestrator.start_activity` deixou de aceitar
   `is_planned_recall` como parametro. Agora ele e SEMPRE derivado:
   `is_planned_recall = action is not None and action.decision_type ==
   DecisionType.SCHEDULE_RECALL` - nunca informado por um chamador.
2. `MockProvider.generate_evaluation` deixou de avaliar sempre a dimensao
   `accuracy`: agora usa `EvaluatorInput.target_dimension` (nova
   propriedade, derivada de `activity.activity_type` via o mapa
   `_DIMENSION_BY_ACTION` em `session_service.py`), que aponta para a
   dimensao que cada acao da ladder pedagogica foi desenhada para
   exercitar (MINIMAL_EXPLANATION -> comprehension, ..., SCHEDULE_RECALL
   -> retention). Sem isso, nenhuma dimensao alem da primeira jamais
   recebia evidencia, e o Decisor nunca conseguia progredir a ladder ate
   SCHEDULE_RECALL mesmo com a correcao #1 sozinha.

Testado em `test_orchestration.py::test_first_fsrs_card_is_born_through_normal_application_flow`,
que parte de um banco novo (`repos.memory_states.get(...) is None`) e usa
SOMENTE `start_session -> choose_focus_competency -> start_activity ->
submit_interaction` (a fixture `drive_to_schedule_recall` em
`conftest.py`) ate o Decisor escolher SCHEDULE_RECALL por conta propria -
nenhum teste neste arquivo define `is_planned_recall=True` diretamente.

### #2. Nota FSRS derivada da propria avaliacao de recuperacao, nunca do ProductionResult

**Problema:** embora a v0.2 ja exigisse uma `EvidenceAssessment` valida
para autorizar uma revisao, a NOTA (1-4) continuava vindo do mapeamento
generico `ProductionResult -> Rating` (Decisao #5 original) - a mesma
tabela usada para QUALQUER interacao, nao a avaliacao especifica daquela
tentativa de recuperacao.

**Decisao:** `rating_from_production_result` foi removido.
`memory/fsrs_adapter.derive_recall_rating(assessment, config)` deriva a
nota EXCLUSIVAMENTE de `EvidenceAssessment.classification`/`.confidence`
da avaliacao de RETENTION da propria tentativa: `NEGATIVE` sempre vira
`Again`; `POSITIVE` vira `Easy`/`Good`/`Hard` conforme dois novos
limiares de confianca (`recall_rating_easy_min_confidence` = 0.85,
`recall_rating_good_min_confidence` = 0.65). `evaluate_recall_eligibility`
ganhou dois parametros (`evidence_dimension`, `config`) e perdeu
`production_result`; agora exige, nesta ordem: atividade planejada ->
avaliacao presente -> dimensao == `retention` -> relacao == `target` ->
nao inconclusiva -> sem causa alternativa pendente -> confianca >=
`recall_min_confidence` (0.5) -> intervalo desde a ultima revisao >=
`recall_min_interval_seconds` (3600s = 1h). Os quatro limiares vivem em
`AggregationConfig`, serializados em `RuleVersion.config_json` - nunca
hardcoded no adapter.

Testado em `test_memory_adapter.py` com casos explicitos para avaliacao
negativa (`test_negative_evaluation_is_eligible_with_again_rating`),
baixa confianca (`test_low_confidence_assessment_is_never_eligible`),
evidencia incidental (`test_incidental_evidence_is_never_eligible`) e
tentativas separadas por segundos
(`test_attempts_seconds_apart_are_not_independent_observations`, com o
caso simetrico `test_attempts_far_apart_are_independent_observations`).

### #3. Reprocessamento usa exclusivamente a RawInteraction persistida; idempotency_key reutilizada com conteudo diferente e rejeitada

**Decisao dupla:**

1. `EvidenceService.record_interaction` ganhou `IdempotencyConflictError`:
   se uma `idempotency_key` ja usada chega de novo com `activity_id`,
   `session_id`, `learner_input`, `help_level` ou `production_result`
   DIFERENTE do que foi persistido da primeira vez, a chamada e rejeitada
   (nunca "resolvida" silenciosamente escolhendo uma das duas versoes).
   `tutor_output` deliberadamente NAO entra nessa checagem de conflito
   (nao faz parte da identidade da submissao), mas tambem nunca
   sobrescreve o que ja foi gravado - a linha existente e sempre devolvida
   sem alteracao.
2. `SessionOrchestrator.submit_interaction` monta o `EvaluatorInput`
   exclusivamente a partir dos campos da `RawInteraction` PERSISTIDA
   (`raw_interaction.learner_input`, `.tutor_output`, `.help_level`,
   `.production_result`), nunca dos parametros frescos desta chamada. Numa
   interacao nova isso e o mesmo conteudo (ja validado igual por
   `record_interaction`); numa REPROCESSADA (apos queda do avaliador,
   inclusive apos reinicio do processo), garante que o avaliador sempre ve
   exatamente o que ficou gravado da primeira vez.

Testado em `test_evidence_service.py`:
`test_idempotency_key_reused_with_different_content_is_rejected`,
`test_reprocessing_uses_persisted_raw_interaction_not_fresh_call_params`
e, cobrindo explicitamente "apos reiniciar o processo",
`test_idempotency_conflict_rejected_after_process_restart` (fecha a
conexao, abre uma NOVA no mesmo arquivo, e confirma que a rejeicao
sobrevive porque vem do banco, nunca de estado em memoria de um processo
anterior).

### #4. Migration 0002 atomica, sem perda silenciosa de avaliacoes v0.1

**Problema duplo:** (a) `run_migrations` rodava cada migration via
`conn.executescript()`, que no modulo `sqlite3` do Python emite um COMMIT
implicito ANTES de rodar o script - uma falha no meio de uma migration de
varios passos (como a 0002, que reconstroi varias tabelas) podia deixar o
banco parcialmente migrado, sem rollback. (b) a reconstrucao de
`evidence_assessment` usava um `WHERE` que so copiava linhas ja
consistentes com o novo `CHECK` (`inconclusive` <=> `classification`) -
uma avaliacao v0.1 legitima que violasse essa consistencia (o esquema
antigo nao tinha esse `CHECK`) era DESCARTADA silenciosamente.

**Decisao:**

1. `migrations/0002_v0_2_corrections.sql` agora contem seu proprio
   `BEGIN IMMEDIATE;`/`COMMIT;` explicito - a fronteira transacional real
   precisa vir de DENTRO do texto do script (pelo motivo em (a) acima).
   `persistence/migrations._apply_migration_atomically` liga/desliga
   `PRAGMA foreign_keys` por FORA da transacao (so tem efeito la, nunca
   dentro de uma aberta) e faz `ROLLBACK` explicito se o script falhar no
   meio, antes de propagar `MigrationError` - nenhuma alteracao parcial
   sobrevive.
2. A reconstrucao de `evidence_assessment` deixou de filtrar por `WHERE`:
   toda linha e copiada, com `inconclusive` RECALCULADO a partir de
   `classification` (a fonte de verdade) via `CASE`, nunca confiando no
   valor legado potencialmente divergente. Nenhuma avaliacao e descartada.
3. `run_migrations` agora tira um backup automatico (Online Backup API,
   reaproveitando `persistence.backup.create_backup`) ANTES de aplicar
   qualquer migration pendente num banco que JA tinha migrations
   aplicadas (upgrade de um banco existente - nunca um banco novo vazio).
4. Um diagnostico auditavel (quantas linhas de `evidence_assessment`
   precisaram ser normalizadas) e calculado ANTES da migration alterar
   qualquer coisa e persistido em `schema_migrations.notes` (coluna nova,
   adicionada sob demanda por `_ensure_migrations_table`).

Testado em `tests/test_migrations.py`: atualizacao de um banco v0.1
POPULADO, com avaliacoes cujo par classification/inconclusive so era
permitido pelo esquema antigo
(`test_migration_0002_preserves_all_v01_evaluations_including_incompatible_ones`),
backup automatico previo
(`test_migration_0002_takes_automatic_backup_before_upgrading_existing_database`),
e rollback completo diante de uma falha sintetica no meio do script
(`test_migration_atomic_rollback_on_mid_script_failure`).

### #5. Restore em estado controlado: impede escritas, trata WAL, mostra falha ao usuario

**Decisao:**

1. Antes de copiar/trocar qualquer arquivo, `restore_from_backup` chama
   `close_connections()` (callback do chamador) e tenta ficar EXCLUSIVO no
   banco ativo via `_checkpoint_and_clear_wal`: `PRAGMA
   wal_checkpoint(TRUNCATE)` + `PRAGMA journal_mode = DELETE` (o que
   remove os arquivos `-wal`/`-shm`). O proprio SQLite recusa essa troca
   de modo com `OperationalError: database is locked` se OUTRA conexao
   ainda tiver o banco aberto - usamos exatamente esse comportamento
   nativo como sinal de "ha uma conexao concorrente", em vez de
   reimplementar um lock proprio: a restauracao e ABORTADA nesse caso,
   sem tocar em nenhum arquivo. Um banco ativo corrompido/ilegivel
   (`DatabaseError`, o proprio desastre que a restauracao existe para
   corrigir) e tratado como "nada a proteger" e a restauracao prossegue.
2. `_remove_wal_sidecars` roda de novo logo apos o `os.replace` (o arquivo
   recem-trocado nunca deve herdar sidecars orfaos) e tambem apos
   qualquer reversao (a copia de seguranca restaurada nunca fica ao lado
   de sidecars de uma tentativa que falhou).
3. `web/app.py`'s `/audit/restore` route agora captura o `RestoreResult` e
   redireciona para `/audit?restore_status=...&restore_message=...`; a
   pagina de auditoria mostra a mensagem (classe `ok`/`error`) - o
   resultado nunca e descartado silenciosamente.

Testado em `tests/test_restore.py`: WAL genuinamente ativo (
`test_restore_flattens_active_wal_before_swapping`, com
`PRAGMA wal_autocheckpoint = 0` para garantir que o WAL nao se achate
sozinho antes do teste), conexao concorrente bloqueando a restauracao
(`test_restore_aborts_cleanly_with_concurrent_connection_open`) e reversao
com WAL ativo (`test_restore_reverts_cleanly_when_swap_target_had_active_wal`).
`tests/test_web.py::test_restore_failure_is_shown_to_the_user` cobre a
superficie HTTP.

### #6. Cluster de evidencia nunca usa o texto do prompt

**Problema:** mesmo com clustering server-side (item #6-7 da v0.2), a
assinatura do cluster incluia o prompt normalizado. Um Red Team encontrou
que prompts com texto DIFERENTE mas pedagogicamente PREVISIVEL (mesmo
exercicio de lacuna, so trocando sujeito/verbo - "She ___ (go) to
school." vs. "He ___ (work) at a bank.") gerava clusters diferentes,
permitindo "provar independencia" so variando palavras superficiais de um
template repetido.

**Decisao:** `compute_context_signature` deixou de incluir o texto do
prompt; a assinatura agora e `(activity_type, competency_targets
ordenados)`, com o cluster continuando escopado a uma `session_id`
(inalterado). Detectar predictibilidade textual de forma robusta exigiria
NLP, fora do escopo da V0; a alternativa auditavel e conservadora e usar o
que realmente define "a mesma tarefa" neste sistema - mesma sessao +
mesma acao pedagogica + mesma(s) competencia(s) alvo - deliberadamente
MENOS permissivo com "independencia" do que comparar texto. Isso e MAIS
rigoroso que antes: repetir a mesma acao pedagogica na mesma sessao para a
mesma competencia agora sempre colapsa num cluster so, MESMO que os
prompts sejam visivelmente distintos.

*Efeito colateral documentado:* varios testes que geravam "evidencia
independente" via prompts diferentes na MESMA sessao precisaram passar a
usar sessoes DIFERENTES para continuar genuinamente independentes (ver
`_new_session`/sessoes por iteracao em `test_evidence_service.py` e
`test_redteam.py`).

Testado em `test_clustering.py::test_predictable_template_variation_does_not_prove_independence`
(tres prompts textualmente distintos, mesma competencia/sessao/tipo,
`len(cluster_ids) == 1`) e em
`test_redteam.py::test_P4_different_activity_ids_same_context_collapse_into_one_cluster`.

---

## Pacote de correcao v0.2.2 (terceira auditoria pos-entrega)

Uma terceira auditoria, sobre o commit que fechou o pacote v0.2.1,
encontrou quatro pontos: um bug de nao-determinismo intermitente (P3
falhava de vez em quando), um erro de categoria que sobreviveu a
correcao anterior (confianca do avaliador usada como sinal de facilidade
de recuperacao), e duas janelas de exclusividade/atomicidade que nao
cobriam a operacao inteira (restore, migration 0002). A nova politica de
rating/intervalo esta registrada em `RuleVersion` via
`evidence.rule_versions.build_v0_2_2_rule_version` - ver
PEDAGOGICAL_CONSTITUTION.md (Principio 28) e DATA_MODEL.md.

### #1. `sequence_number` monotonico e explicito substitui `computed_at`+`id` para "estado atual"

**Problema:** `CompetencyStateRepository.current()` escolhia a linha mais
recente por `ORDER BY computed_at DESC, id DESC`. `computed_at` e um
timestamp de texto; sob escritas rapidas (o proprio
`test_P3_two_negatives_from_same_cluster_never_corroborate_regression`
grava varias avaliacoes em sequencia) duas linhas podiam receber o MESMO
`computed_at` se a resolucao do relogio do SO fosse mais grosseira que o
tempo entre duas gravacoes. Quando isso acontecia, o desempate caia para
`id DESC` - e `id` e um `uuid4().hex` ALEATORIO, entao qual linha
"vencia" variava de execucao para execucao: a mesma suite passava ou
falhava dependendo so da sorte do UUID (reproduzido pelo auditor: 118/119
numa execucao, 119/119 na seguinte).

**Decisao:** `competency_state` ganhou `sequence_number INTEGER NOT
NULL` (migration `0003_monotonic_projection_sequence.sql`, com
`UNIQUE INDEX` proprio). `CompetencyStateRepository.insert` atribui o
proximo valor (`MAX(sequence_number) + 1`) DENTRO da mesma escrita -
nunca informado pelo chamador. `current()`/`history()`/`list_by_generation()`
passaram a ordenar EXCLUSIVAMENTE por `sequence_number` - `computed_at`
continua existindo (e o "quando", para exibicao/auditoria), mas nunca
mais decide ordem. O backfill da migration usa `ROW_NUMBER() OVER (ORDER
BY rowid)` - `competency_state` nunca permite `DELETE` (trigger de
imutabilidade), entao `rowid` nunca foi reciclado e reflete fielmente a
ordem de criacao original.

Testado com o RELOGIO CONGELADO (nunca dependendo da velocidade real da
maquina, ao contrario do bug original):
`test_persistence.py::test_competency_state_current_and_history_use_monotonic_sequence_frozen_clock`
grava 30 linhas com o MESMO `computed_at` e confirma que `current()`
devolve sempre a ULTIMA gravada e `history()` preserva a ordem real de
insercao - 20 execucoes consecutivas de
`test_P3_two_negatives_from_same_cluster_never_corroborate_regression` e
10 execucoes consecutivas da suite inteira (125/125) confirmaram o fim da
intermitencia.

### #2. Nota FSRS derivada de sinais observaveis, nunca de confianca; primeira revisao tambem verifica intervalo desde a aprendizagem

**Problema:** a v0.2.1 corrigiu o mapeamento direto `ProductionResult ->
Rating`, mas cometeu o MESMO tipo de erro de novo: `derive_recall_rating`
usava `EvidenceAssessment.confidence` para decidir Easy/Good/Hard.
`confidence` mede o quanto o AVALIADOR confia no proprio julgamento de
classificacao (a resposta estava certa?), nunca o quao FACIL foi para o
APRENDIZ recuperar a informacao - uma avaliacao positiva de alta
confianca virava "Easy" automaticamente mesmo quando a resposta so saiu
certa com uma pista explicita. Alem disso, a PRIMEIRA revisao de uma
competencia (sem `memory_state.last_review_at` para comparar) nunca
verificava intervalo nenhum.

**Decisao dupla:**

1. `derive_recall_rating` deixou de receber `confidence`/`AggregationConfig`
   e passou a exigir `help_level`/`production_result` - os dois sinais
   OBSERVAVEIS do processo de recuperacao da propria tentativa. Politica
   conservadora (nunca assume Easy por omissao): **Easy** exige
   `help_level=A0` E `production_result=SPONTANEOUS_CORRECT` ao MESMO
   tempo; **Hard** se houve suporte explicito (`A2`/`A3`) OU a resposta
   so veio certa apos pista/correcao; **Good** para qualquer outro caso
   POSITIVE (ex.: autocorrecao espontanea, ou contexto levou a resposta
   sem pista explicita). `confidence` continua existindo, mas SO como
   filtro de ELEGIBILIDADE (`recall_min_confidence`) - os dois limiares
   que a v0.2.1 introduzira para converter confianca em rating
   (`recall_rating_easy_min_confidence`/`..._good_min_confidence`) foram
   REMOVIDOS de `AggregationConfig`.
2. `first_review_min_interval_since_learning_seconds` (novo campo):
   `evaluate_recall_eligibility`, quando `memory_state is None` (primeira
   revisao), agora verifica o intervalo desde a PRIMEIRA evidencia ja
   registrada para a competencia (`EvidenceEventRepository.first_created_at_for_competency`,
   um proxy observavel de "quando o aprendiz comecou a aprender isto"),
   contra este novo limiar - nunca mais deixado sem checagem so porque
   nao ha uma revisao anterior para comparar.

Para permitir testar o intervalo desde a aprendizagem sem depender da
velocidade real da maquina (o MESMO tipo de fragilidade do ponto #1
acima), `SessionOrchestrator.submit_interaction` ganhou um parametro
opcional `now` que, quando informado, substitui o relogio real para
TODOS os timestamps daquela submissao (`RawInteraction.occurred_at`,
`EvidenceEvent`/`EvidenceAssessment.created_at`, o `review_datetime` da
revisao FSRS) - omitido, usa sempre o relogio real de sempre (chamadores
web nunca o passam). A fixture `drive_to_schedule_recall` usa isso para
avancar um relogio controlado (2h por iteracao) em vez do relogio real.

Testado em `test_memory_adapter.py::test_derive_recall_rating_uses_observable_signals_not_confidence`
(confianca altissima + pista explicita = Hard, nunca Easy),
`test_first_review_too_soon_after_learning_is_never_eligible` e
`test_first_review_long_after_learning_is_eligible`.

### #3. Guarda de exclusividade do restore protege ate o fim da troca ou reversao, nao so o instante da checagem

**Problema:** `restore_from_backup` abria uma conexao de sondagem,
conferia exclusividade (achatando o WAL) e FECHAVA essa conexao
imediatamente, so entao copiando/trocando os arquivos. Entre o fechamento
da sondagem e o `os.replace`, nada segurava nenhum lock - uma conexao
NOVA, aberta nesse meio-tempo, podia escrever livremente no banco ativo
exatamente durante a janela de copia/troca.

**Decisao:** `_acquire_exclusive_guard` agora MANTEM a conexao aberta com
uma transacao `BEGIN EXCLUSIVE` (apos achatar o WAL e sair do modo WAL)
ate o chamador liberar explicitamente (`_release_guard`) - o lock
continua valendo durante toda a copia do backup para staging e o
`os.replace`. So e liberado IMEDIATAMENTE apos a troca (o lock, preso ao
arquivo ANTERIOR ja substituido, deixa de proteger algo util a partir
dai). No caminho de FALHA, a reversao tambem tenta (best-effort) a mesma
exclusividade antes de copiar a copia de seguranca de volta - e passou a
usar copia-para-staging + `os.replace` atomico, nunca mais sobrescrevendo
`db_path` com bytes soltos via `shutil.copy2` direto (o que podia expor
um leitor concorrente a um arquivo pela metade).

Testado em
`test_restore.py::test_restore_blocks_a_connection_opened_after_the_check_and_before_the_swap`:
intercepta `shutil.copy2` bem no meio do fluxo real (a copia do backup
para staging, que acontece DEPOIS da checagem de exclusividade e ANTES
do `os.replace`) e abre uma conexao nova ali dentro, confirmando que a
escrita dela e recusada (`OperationalError: database is locked`) e que a
restauracao em si conclui normalmente, sem o dado do "escritor tardio".

### #4. Registro em `schema_migrations` entra na mesma transacao atomica da propria migration

**Problema:** o `INSERT INTO schema_migrations` de bookkeeping acontecia
DEPOIS que `executescript()` retornava - ou seja, depois que o `COMMIT;`
embutido no proprio arquivo .sql ja tinha comitado toda a migration. Uma
falha nesse INSERT (disco cheio, por exemplo) deixava o ESQUEMA ja
migrado mas SEM o registro de que a migration tinha sido aplicada: na
proxima execucao, `run_migrations` tentaria aplicar a MESMA migration de
novo sobre um banco que ja a tinha, tipicamente falhando de forma confusa
(ex.: coluna/tabela ja existe).

**Decisao:** `_apply_migration_atomically` agora injeta o `INSERT INTO
schema_migrations` diretamente no TEXTO do script, IMEDIATAMENTE antes do
`COMMIT;` final dele (`_inject_bookkeeping_before_final_commit`, que
localiza a ultima ocorrencia literal de `COMMIT;` no texto) - o registro
passa a fazer parte da MESMA transacao SQL que o resto da migration.
Migrations sem `BEGIN`/`COMMIT` proprio (0001, que roda em modo
autocommit por instrucao) continuam recebendo o bookkeeping acrescentado
ao final, sem mudanca de comportamento.

Testado em
`test_migrations.py::test_migration_bookkeeping_failure_leaves_whole_schema_at_previous_version`:
pre-semeia uma linha com o MESMO filename que uma migration sintetica vai
tentar gravar, forcando o INSERT de bookkeeping (a ULTIMA instrucao do
script) a violar a PRIMARY KEY - confirma que nem a tabela nem os dados
que a migration sintetica criou sobrevivem ao ROLLBACK, e que o registro
de bookkeeping original permanece unico e intocado.

---

## Correcao pos-entrega: restore quebrava no Windows (quarta auditoria)

Um usuario rodou a suite no Windows apos o commit que fechou a v0.2.2 e
reportou 5 falhas, todas em `test_restore.py` - reproduzidas de forma
isolada rodando so aquele arquivo. A causa raiz: `_acquire_exclusive_guard`
mantinha uma conexao SQLite aberta com `BEGIN EXCLUSIVE` durante todo o
`os.replace` (o mecanismo introduzido no pacote v0.2.2, ponto 3, para
bloquear escritores concorrentes durante a troca de arquivo). Isso e
correto no Linux/macOS (`rename()` nao se importa com quem tem o arquivo
aberto), mas no Windows `os.replace`/`MoveFileEx` RECUSA substituir um
arquivo que qualquer processo - inclusive o proprio - ainda tem aberto,
levantando `PermissionError: [WinError 5]`. O caminho de reversao tinha o
mesmo problema (tentava outro `os.replace` com uma guarda de reversao
tambem aberta).

**Decisao:** a protecao contra escritores concorrentes deixou de depender
de manter uma conexao SQLite presa durante a troca - passou a ter duas
camadas PORTATEIS entre sistemas operacionais:

1. Um portao em memoria (`persistence.restore._pause_for_restore` /
   `is_restore_in_progress()`), ligado do inicio ao fim de TODA a
   operacao de restauracao (checagem, troca, verificacao e, se
   necessario, reversao). `web/deps.py:get_conn` - o unico ponto por
   onde QUALQUER requisicao HTTP abre uma conexao - consulta esse portao
   e recusa com HTTP 503 se estiver ligado, em vez de arriscar
   ler/escrever um banco no meio de uma troca de arquivo. Isso substitui
   "segurar um lock do SQLite" por "coordenar no nivel da aplicacao", que
   funciona identicamente em qualquer SO porque nao depende de nenhum
   comportamento de arquivo especifico de plataforma.
2. `_check_exclusive` (renomeada de `_acquire_exclusive_guard`) continua
   confirmando exclusividade no banco (achatando o WAL, saindo do modo
   WAL) ANTES de cada troca - mas agora SEMPRE fecha a propria conexao de
   sondagem imediatamente, nunca a mantem presa durante a copia/`os.replace`
   que vem a seguir. Isso significa que, no exato instante em que
   `os.replace` e chamado, nenhuma conexao sqlite3 deste processo esta
   aberta em `db_path` - condicao necessaria em qualquer SO, e
   suficiente no Windows.

**Tratamento de falha ficou mais defensivo:** `_revert_to_safety_copy`
agora captura qualquer excecao da PROPRIA reversao (nunca deixa
propagar) e devolve uma mensagem descritiva em vez de derrubar o
processo; a copia de seguranca (`*.pre-restore-*`) so e apagada quando a
reversao realmente terminou com sucesso - se a reversao tambem falhar,
ela permanece no disco para recuperacao manual, e o `RestoreResult`
descreve as DUAS falhas (a original e a da reversao).

*Efeito colateral aceito, documentado:* a checagem de exclusividade
antes da troca continua sendo best-effort contra uma conexao TRULY
externa que abra bem no meio da janela entre a checagem e o
`os.replace` - o portao em memoria so protege requisicoes que passam
pela PROPRIA aplicacao (`get_conn`), nao um processo de terceiros
tocando o arquivo diretamente. Isso e uma troca deliberada: a garantia
mais forte da revisao anterior (lock do SQLite mantido durante toda a
troca) e exatamente o que quebrava no Windows; a garantia atual e mais
fraca contra terceiros externos mas funciona nos dois sistemas
operacionais, o que e o requisito real (ver KNOWN_LIMITATIONS.md).

Testado em `test_restore.py::test_restore_holds_no_sqlite_connection_open_across_os_replace`
(intercepta `os.replace` e confirma, via uma sondagem `BEGIN
EXCLUSIVE`/`ROLLBACK` descartavel, que nenhuma conexao deste processo
esta presa no arquivo naquele instante exato - a mesma condicao que
falha no Windows se violada),
`test_restore_pauses_the_application_gate_and_always_resumes_it` (o
portao liga durante a operacao e sempre desliga depois, inclusive apos
sucesso), `test_restore_never_raises_when_revert_itself_also_fails`
(dupla falha - restauracao E reversao - nunca levanta excecao, preserva
a copia de seguranca no disco) e
`test_web.py::test_restore_rejects_new_requests_while_in_progress`
(uma requisicao HTTP real, disparada de DENTRO da janela de copia via um
spy em `shutil.copy2`, recebe 503).

**Limite honesto desta correcao:** os testes acima foram escritos e
executados em Linux (o unico ambiente disponivel nesta sessao) - eles
prova, por raciocinio direto sobre o mecanismo (nenhuma conexao aberta no
instante do `os.replace`), que a causa raiz relatada no Windows deixou de
existir, mas NAO substituem rodar a suite de verdade num Windows real. O
usuario que reportou o bug e quem tem esse ambiente; a validacao final em
Windows depende dele confirmar.

**Atualizacao:** o usuario confirmou 128/128 (suite completa) e 10/10
(`test_restore.py` isolado) num Windows real apos esta correcao - o
`WinError 5` original foi resolvido.

---

## Correcao pos-entrega: portao de concorrencia do restore tinha duas janelas (quinta auditoria)

O mesmo usuario, ja no Windows, encontrou e REPRODUZIU dois problemas
novos no portao introduzido pela correcao anterior:

1. `_pause_for_restore()` so ligava/desligava um `bool` isolado
   (`_restore_in_progress = True`/`False`) protegido por um lock que era
   solto logo depois de cada atribuicao - ele nunca IMPEDIA uma segunda
   chamada de tambem entrar enquanto a primeira ainda estivesse em
   andamento. Com duas restauracoes "dentro" ao mesmo tempo, quando a
   PRIMEIRA terminava, seu `finally` desligava o portao mesmo com a
   SEGUNDA ainda tocando arquivos - reabrindo exatamente a janela sem
   protecao que a correcao anterior deveria ter fechado.
2. `get_conn()` consultava o portao (`is_restore_in_progress()`) e SO
   DEPOIS abria a conexao - dois passos separados, sem nenhum lock em
   comum entre eles. Uma restauracao podia comecar exatamente nesse
   meio-tempo: a requisicao via o portao desligado, comecava a abrir a
   conexao, e a restauracao comecava a tocar arquivos antes dessa conexao
   sequer existir de fato ou ser fechada.

**Decisao:** o portao deixou de ser um `bool` isolado e virou uma
coordenacao leitor/escritor de verdade sobre um unico
`threading.Condition` compartilhado (`persistence/restore.py`):

- `reader_slot()` (usado por `get_conn()`) registra "esta requisicao tem
  uma conexao aberta" e CHECA que nenhuma restauracao esta em andamento
  como UMA UNICA operacao atomica sob o lock - nunca mais dois passos
  separados. O slot fica registrado durante toda a vida da conexao (do
  `connect()` ate o `close()` no `finally` de `get_conn`), nao so no
  instante de abrir.
- `_pause_for_restore()` (o "escritor") primeiro tenta marcar
  `_restore_in_progress = True` sob o MESMO lock - se ja estiver `True`
  (outra restauracao em andamento), levanta `RestoreAlreadyInProgressError`
  IMEDIATAMENTE, antes de tocar em qualquer coisa, nunca deixando uma
  segunda restauracao "entrar" junto com a primeira. So depois de marcar
  com sucesso, espera (`Condition.wait()`, que solta o lock enquanto
  espera) a contagem de leitores registrados chegar a zero - e, como
  `_restore_in_progress` ja esta `True` nesse momento, qualquer NOVO
  leitor que apareca nesse meio-tempo e recusado na hora por
  `reader_slot()`, evitando que uma fila de leitores adie a restauracao
  para sempre.
- `restore_from_backup` captura `RestoreAlreadyInProgressError` e devolve
  um `RestoreResult(success=False, ...)` legivel em vez de deixar a
  excecao escapar - uma segunda tentativa de restauracao concorrente
  falha de forma limpa, nunca com um traceback cru.

Testado em `test_restore.py::test_restore_rejects_a_concurrent_second_restore_attempt`
(duas restauracoes disparadas de fato ao mesmo tempo, via threads reais e
sincronizacao por `threading.Event` - a primeira e segurada "dentro" de
proposito enquanto a segunda e chamada; a segunda precisa ser rejeitada
NA HORA, a primeira precisa terminar normalmente, e o portao so pode
desligar depois que a UNICA restauracao real terminou) e
`test_restore.py::test_restore_waits_for_an_in_flight_request_before_touching_files`
(entra manualmente em `reader_slot()` simulando uma requisicao que ja
passou pela checagem do portao, dispara uma restauracao numa thread
separada, e confirma que `os.replace` NUNCA e chamado enquanto o "leitor"
segue aberto - so depois que ele fecha).

**Escopo permanece o mesmo de antes:** esta coordenacao protege contra
disputas DENTRO deste processo (duas requisicoes/threads do mesmo
servidor web); uma conexao verdadeiramente externa (outro processo do SO
tocando o arquivo diretamente) continua dependendo so da checagem de
exclusividade best-effort no proprio banco (`_check_exclusive`), como ja
documentado na correcao anterior.

**Limite honesto:** os dois testes acima usam threads reais e sincronizacao
determinista (`threading.Event`, nunca `sleep`), rodados repetidamente em
Linux sem falha - mas, como na correcao anterior, a confirmacao final em
Windows depende do usuario rodar a suite de novo la.
