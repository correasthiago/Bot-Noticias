# Decisoes tecnicas — Central Universal V0.1

Decisoes puramente implementacionais e reversiveis, tomadas de forma
autonoma pelo executor conforme autorizado pelo pacote de implementacao.
Nenhuma delas altera pedagogia, arquitetura aprovada, governanca,
persistencia ou custos.

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

## 3. Regressao so "corrobora" (derruba estado) com >=2 evidencias negativas recentes

Principio 12 diz que um erro isolado nao apaga dominio anterior. Precisava
de um numero concreto para "isolado" vs. "corroborado". Escolhi: 1
evidencia negativa/contraditoria entre as 3 mais recentes marca
`possible_regression=True` mas NAO rebaixa o estado; 2 ou mais rebaixam em
UM nivel (nunca direto para `insufficient_evidence`, para nao apagar todo
o historico de uma vez). E uma regra simples e auditavel, nao uma
formula estatistica.

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
