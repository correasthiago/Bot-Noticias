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
