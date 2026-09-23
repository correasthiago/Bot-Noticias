# Limitacoes conhecidas — Central Universal V0.2

Honestas e explicitas, conforme pedido no relatorio final (Secao 34). Nada
aqui viola a constituicao pedagogica — sao lacunas de escopo/maturidade
da V0, nao de principio.

## Pedagogicas

- **Thresholds de agregacao sao um ponto de partida, nao uma
  calibracao.** `AggregationConfig` (lida de `RuleVersion.config_json`)
  usa 2/3 clusters independentes (ou dias distintos, para retencao) como
  limiares de `demonstrated`/`consolidated`. Sao numeros simples e
  auditaveis, nao uma calibracao com dados reais de aprendizado — a
  especificacao pede exatamente isso (nao inventar pesos sem dados). A
  V0 deve gerar os dados que permitirao revisar esses numeros. Desde
  v0.2, pelo menos esses numeros sao configuraveis por RuleVersion, nao
  mais hardcoded - revisa-los agora e criar uma nova RuleVersion, nao
  editar codigo.
- **Regressao nunca e confirmada automaticamente (v0.2).** O pacote de
  correcao removeu de proposito o rebaixamento automatico de estado por
  "corroboracao" de evidencia negativa - era, na pratica, so um
  rebaixamento automatico com um limiar mais alto, e o Red Team apontou
  isso corretamente. `possible_regression=True` agora e um sinal
  PERMANENTE ate que uma "validacao deliberada" resolva a duvida - mas a
  V0 nao implementa NENHUM mecanismo automatico dessa validacao (isso e
  Fase futura: o Decisor ja roteia para `TARGETED_REGRESSION_CHECK`, mas
  nada hoje fecha esse ciclo automaticamente reavaliando e confirmando ou
  descartando a suspeita). Na pratica, uma competencia com
  `possible_regression=True` fica assim indefinidamente ate uma
  intervencao futura - correto pelo Principio 12, mas uma limitacao real
  de completude do ciclo. (Oitava auditoria pos-entrega: ate essa
  correcao, `classify_dimension` na pratica NAO respeitava este paragrafo
  - uma evidencia positiva comum mais recente silenciava
  `possible_regression` sozinha, sem validacao deliberada nenhuma. A
  correcao fez o codigo finalmente bater com o que este paragrafo sempre
  documentou - ver Principio 36 e DECISIONS.md.)
- **Uma unica dimensao "escolhida" por interacao no MockProvider -
  PARCIALMENTE RESOLVIDA em v0.2.1.** Ate a v0.2, o `MockProvider`
  avaliava sempre a dimensao `accuracy`, o que impedia o Decisor de
  progredir a ladder ate SCHEDULE_RECALL pelo fluxo normal (Decisao #1 do
  pacote v0.2.1). Desde v0.2.1, ele consulta
  `EvaluatorInput.target_dimension` (derivado da acao pedagogica que a
  atividade foi criada para exercitar) - mas isso continua sendo uma
  heuristica MECANICA (um mapa fixo acao->dimensao), nao NLU real. Um
  avaliador real (LLM) ainda escolheria a dimensao certa por conteudo da
  resposta, nao so pelo tipo de atividade; o Mock existe para provar o
  motor, nao para ensinar ingles de verdade.
- **Automaticidade e provisoria na V0 textual (Secao 20).** Os dados
  estao estruturados para receber `response latency`, pausas,
  reformulacoes e modalidade de fala no futuro, mas nenhum desses sinais
  e capturado hoje — `ProductionResult` e o unico sinal disponivel.
- **Um card FSRS por competencia, nao por dimensao.** Ver DECISIONS.md
  #4. Se experiencia real mostrar que dimensoes esquecem em ritmos muito
  diferentes, isso e uma mudanca aditiva no adapter, nao uma reescrita.
  Este continua sendo o UNICO ponto pendente da pergunta "um card por
  competencia ou por dimensao?" apos a terceira auditoria pos-entrega -
  nao foi revisitado nesta rodada, e permanece a decisao original.
- **A politica Easy/Good/Hard e uma heuristica MECANICA sobre dois sinais
  discretos, nao uma medida continua de esforco (v0.2.2).** Desde a
  terceira auditoria pos-entrega, `derive_recall_rating` usa
  EXCLUSIVAMENTE `help_level`/`production_result` da propria tentativa -
  nunca confianca do avaliador (Secao 2, DECISIONS.md). Isso corrige o
  erro de categoria anterior, mas a politica resultante ainda e uma
  tabela de decisao fixa (2 sinais discretos -> 4 notas), nao uma medida
  continua de esforco de recuperacao (tempo de resposta, numero de
  tentativas, hesitacao) - esses sinais mais ricos so existem para voz
  (ver "O que falta para voz" abaixo). A politica em si (quais
  combinacoes viram Easy/Good/Hard) e um ponto de partida auditavel, nao
  uma calibracao com dados reais de aprendizes.
- **`first_review_min_interval_since_learning_seconds` usa a PRIMEIRA
  evidencia de QUALQUER dimensao como proxy de "aprendizagem" (v0.2.2).**
  `EvidenceEventRepository.first_created_at_for_competency` nao distingue
  uma evidencia de ensino genuino (ex.: `comprehension`) de uma incidental
  (`qualified_incidental`/`mere_presence`) ou de uma avaliacao que falhou
  e foi reprocessada - qualquer evento conta como "o momento em que o
  aprendiz comecou a aprender isto". Na pratica isso e conservador (tende
  a marcar a aprendizagem mais cedo, nunca mais tarde, entao o intervalo
  medido tende a ser maior, nao menor, que o real) mas nao e uma medida
  pedagogicamente precisa de "primeira exposicao ao conteudo".

## Tecnicas

- **Cluster de evidencia e escopado a UMA sessao, sem janela de tempo
  explicita (v0.2; assinatura refinada em v0.2.1).** `evidence_cluster`
  agrupa por `(session_id, activity_type, competency_targets)` - o texto
  do prompt saiu da assinatura em v0.2.1 (ver DECISIONS.md #6), porque
  prompts textualmente diferentes mas pedagogicamente previsiveis
  conseguiam forjar independencia. Isso impede a maioria dos casos de
  "fingir independencia", mas uma sessao anormalmente longa (varias
  horas) com a mesma acao/competencia repetida no inicio e no fim
  continuaria contando como um cluster so - o que e conservador
  (subestima independencia), nao permissivo.
- **Idempotencia de avaliacao nao foi testada sob concorrencia real.**
  `try_claim_evaluation` usa um `UPDATE ... WHERE evaluation_status =
  'pending'` atomico, que deveria ser seguro sob o lock `BEGIN IMMEDIATE`
  do SQLite mesmo com multiplas conexoes tentando completar a mesma
  avaliacao ao mesmo tempo - mas isso nunca foi exercitado com threads ou
  processos reais, so logicamente.
- **Concorrencia real nao foi testada com processos separados do SO.**
  SQLite em WAL suporta um escritor por vez; para um unico usuario local
  isso nunca e um problema. `restore_from_backup` DETECTA uma conexao
  concorrente ANTES de cada troca (recusando prosseguir se nao conseguir
  sair do modo WAL com exclusividade), mas isso e testado com uma segunda
  `sqlite3.Connection` no MESMO processo/thread do teste, nao um processo
  do SO realmente separado. O timeout de espera pela exclusividade
  (`_LOCK_TIMEOUT_SECONDS = 5.0` em `persistence/restore.py`) e um numero
  arbitrario, nao calibrado contra nenhuma carga real.
- **A protecao do restore contra escritores concorrentes mudou de "lock
  do SQLite mantido durante a troca" para "portao em memoria + checagem
  best-effort", especificamente porque a primeira quebrava no Windows -
  a garantia contra conexoes verdadeiramente EXTERNAS ficou mais fraca.**
  Uma correcao anterior mantinha uma conexao SQLite presa (`BEGIN
  EXCLUSIVE`) durante toda a copia/troca de arquivo, bloqueando qualquer
  escritor concorrente de verdade - mas um usuario reportou que isso
  fazia `os.replace` falhar no Windows com `PermissionError: [WinError
  5]` (o Windows recusa substituir um arquivo que qualquer processo,
  inclusive o proprio, ainda tem aberto). A correcao trocou essa garantia
  por duas camadas portateis: um portao em memoria
  (`is_restore_in_progress()`), que bloqueia (503) qualquer requisicao
  NOVA que passe por `web/deps.py:get_conn` durante toda a janela da
  restauracao, e uma checagem de exclusividade no banco ANTES de cada
  troca, mas sempre fechada imediatamente - nunca mais mantida presa
  durante o `os.replace` em si. Isso significa que uma conexao
  verdadeiramente EXTERNA (um script Python tocando o arquivo
  diretamente, nao roteado pela aplicacao web) que abra bem no meio da
  janela entre a checagem e a troca NAO e mais bloqueada por um lock do
  SQLite - so o portao em memoria (que so cobre a propria aplicacao)
  continua protegendo essa janela. Ver DECISIONS.md ("Correcao
  pos-entrega: restore quebrava no Windows") para a decisao completa e
  TESTING.md para o resultado por sistema operacional (a correcao foi
  validada em Linux; a confirmacao em Windows depende do usuario que
  reportou o bug original rodar a suite de novo).
- **Sem autenticacao/autorizacao.** A V0 assume fisicamente um unico
  computador de um unico usuario (Secao 1). Nao ha login, nao ha
  isolamento entre "learners" alem de uma FK — se o escopo mudar para
  multiusuario real, isso precisa ser desenhado, nao improvisado.
- **Interface web e funcional, nao responsiva/polida.** CSS minimo,
  sem JS alem do necessario para formularios HTML padrao. Cumpre a
  Secao 28 ("funcional, nao bonita") deliberadamente.
- **Sem paginacao nas telas de auditoria/mapa.** Para uma unica pessoa
  com um grafo de ~17 competencias de ingles isso e irrelevante; escala
  mal se o grafo crescer para milhares de nos sem paginacao.
- **Backup automatico e baseado em intervalo simples, nao em
  criticidade.** `maybe_run_automatic_backup` dispara no fim de cada
  sessao, no maximo uma vez por hora. Nao ha politica de "backup antes de
  uma operacao arriscada" (ex.: antes de uma reconstrucao completa de
  geracao) - so o gatilho de fim de sessao.
- **Pin de dependencias `fastapi`/`starlette`** por um bug encontrado na
  combinacao mais recente no momento da implementacao (ver DECISIONS.md
  #7). Precisa ser revisitado quando houver uma versao corrigida.

## Fora de escopo por decisao explicita da especificacao (Secao 32)

Voz, aplicativo mobile, multiusuario real, login complexo, cloud,
pagamentos, gamificacao, dashboards empresariais, vector database, RAG
generico, fine-tuning, BKT, ML proprio, recomendacao baseada em dados
populacionais, microsservicos. Nenhum desses foi implementado, nem
parcialmente.

## O que falta para conectar uma IA real

1. Implementar `Provider` em `central_universal/providers/` para o
   fornecedor escolhido (ex.: `AnthropicProvider`), retornando o mesmo
   formato de payload que `MockProvider` ja retorna hoje
   (`{"utterance": ..., "activity_prompt": ...}` para o tutor;
   `{"findings": [...]}` no formato de `evaluator.contract` para o
   avaliador, respeitando a regra `inconclusive=True <=>
   classification='inconclusive'`).
2. Apontar `central_universal.web.deps.ACTIVE_PROVIDER` para a nova
   classe (uma linha, configuracao explicita — Principio 17).
3. Desenhar os prompts do tutor e do avaliador — hoje isso nao existe
   porque o Mock nao precisa de prompt nenhum.
4. Decidir tratamento de custo/latencia real (o campo `cost`/
   `tokens_used`/`latency_ms` de `ProviderEvent` ja existe e esta pronto
   para ser preenchido).
5. Revisar `validate_evaluator_payload` para garantir que ela cobre
   variacoes reais de saida do LLM escolhido (hoje ela ja e fail-closed:
   qualquer campo fora do formato esperado rejeita o payload inteiro).
6. Calibrar `AggregationConfig` (thresholds, confianca minima) com dados
   reais, publicando uma nova `RuleVersion` - nunca editando os defaults
   em codigo.

## O que falta para voz

1. Capturar e persistir os sinais listados na Secao 20 (latencia,
   pausas, reformulacoes, autocorrecoes, velocidade) - o schema de
   `raw_interaction` precisaria de colunas adicionais ou uma tabela
   auxiliar `interaction_audio_signal`.
2. Um pipeline de STT (speech-to-text) antes do texto chegar ao
   Avaliador, e possivelmente um provider dedicado para analise
   prosodica.
3. Uma interface de captura de audio no navegador (Web Audio API) —
   nada disso existe na V0 textual.
4. Estender `memory.fsrs_adapter.derive_recall_rating` (desde v0.2.2, a
   funcao que deriva Easy/Good/Hard de `help_level`/`production_result`)
   para tambem considerar fluencia oral - tempo de resposta, pausas,
   hesitacao - nao so corretude textual e nivel de suporte.

## Riscos tecnicos observados durante a implementacao

- **A suite foi desenvolvida e rodada quase inteiramente em Linux; uma
  premissa de arquivo POSIX (que `rename()`/`os.replace` nao se importa
  com quem tem o arquivo de destino aberto) se infiltrou no codigo de
  restore e so foi descoberta quando um usuario rodou a suite no
  Windows.** `os.replace`/`MoveFileEx` no Windows recusa substituir um
  arquivo aberto por qualquer processo - uma diferenca de plataforma que
  nao aparece em NENHUM teste rodado em Linux/macOS, porque la o
  comportamento e permissivo. Corrigido (ver DECISIONS.md, "Correcao
  pos-entrega: restore quebrava no Windows"), mas e um lembrete de que
  "passa na CI" (quando a CI e so Linux) nao e o mesmo que "funciona em
  todo SO suportado" - qualquer codigo futuro que manipule arquivos
  diretamente (nao so via SQLite) precisa considerar essa diferenca
  deliberadamente, nao supor semantica POSIX por default.
- **Divergencia rapida de versoes de dependencias PyPI.** A versao mais
  recente de `fastapi`/`starlette` no momento da implementacao tinha um
  bug de regressao afetando `TemplateResponse` com contexto contendo
  `dict`. Resolvido com pin explicito (DECISIONS.md #7), mas e um
  lembrete de que "instalar a mais recente" nao e seguro sem teste de
  fumaca.
- **Custo de manter triggers de imutabilidade em SQLite.** Funcionam bem
  na V0, mas se o sistema precisar migrar dados historicos no futuro
  (ex.: uma correcao retroativa de bug de gravacao), os triggers vao
  bloquear ate UPDATE administrativo direto — sera necessario um
  procedimento explicito de manutencao (desabilitar trigger, corrigir,
  reabilitar), nao uma operacao trivial. Desde v0.2 isso se aplica a
  MAIS tabelas (`rule_version`, `competency_state`, `decision_event`,
  `provider_event`, `memory_review_log`, `backup_event`) - o custo de
  manutencao cresceu junto com a garantia de integridade.
- **Migration 0002 faz varios `DROP TABLE`/`CREATE TABLE` para adicionar
  FKs e CHECKs que o SQLite nao permite adicionar via `ALTER TABLE`.**
  Isso e seguro para o volume de dados de uma V0 pre-lancamento (a
  migration inclusive faz backfill honesto de `evidence_cluster` a
  partir de dados legados, se existirem), mas um banco de producao muito
  grande levaria mais tempo para migrar do que uma `ALTER TABLE` simples
  levaria. Nao e um problema na escala desta V0. Desde v0.2.1 ela roda
  como uma transacao atomica de verdade (ver DECISIONS.md #4) - uma falha
  no meio desfaz tudo, e todas as avaliacoes v0.1 sao preservadas
  (normalizadas, nunca descartadas).
- **A atomicidade explicita (`BEGIN IMMEDIATE`/`COMMIT` dentro do proprio
  arquivo .sql) e o diagnostico auditavel em `schema_migrations.notes`
  foram aplicados especificamente as migrations 0002 e 0003 (v0.2.1/
  v0.2.2), nao a TODA migration presente ou futura.**
  `migrations/0001_init.sql` continua rodando em modo autocommit por
  instrucao (e so cria tabelas com `CREATE TABLE IF NOT EXISTS` - baixo
  risco de falha parcial problematica). `persistence/migrations._diagnose_migration`
  hoje so reconhece o nome `0002_v0_2_corrections.sql`; uma migration
  futura que precise do mesmo tipo de diagnostico vai precisar do proprio
  ramo explicito ali (documentado no docstring da funcao) - nao ha
  (ainda) um mecanismo generico de "toda migration declara seu proprio
  diagnostico". Desde v0.2.2, o PROPRIO registro de bookkeeping em
  `schema_migrations` (nao so o diagnostico) entra na mesma transacao
  atomica de QUALQUER migration com `BEGIN`/`COMMIT` proprio
  (`_inject_bookkeeping_before_final_commit` localiza o `COMMIT;` final
  do texto por busca de substring - um script que, por algum motivo
  futuro, precisasse literalmente da string `"COMMIT;"` dentro de uma
  string SQL ou comentario DEPOIS do seu proprio COMMIT de fechamento
  confundiria essa busca; nenhuma migration atual faz isso).
- **Ambiguidade original entre "EvidenceEvent" e "EvidenceAssessment" -
  RESOLVIDA no pacote de correcao v0.2.** A V0.1 tinha uma coluna
  `evidence_type` duplicada em `EvidenceEvent`; o pacote de correcao a
  removeu e formalizou a separacao (fatos vs. julgamento) descrita em
  DECISIONS.md #4. Mantido aqui como registro historico do risco que foi
  endereçado.
