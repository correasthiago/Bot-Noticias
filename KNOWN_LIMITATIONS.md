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
  de completude do ciclo.
- **Uma unica dimensao "escolhida" por interacao no MockProvider.**
  O `MockProvider` avalia sempre a dimensao `accuracy` para toda
  competencia candidata, porque ele nao faz NLU real. Um avaliador real
  (LLM) escolheria a dimensao certa por interacao; isso e esperado e
  documentado — o Mock existe para provar o motor, nao para ensinar
  ingles de verdade.
- **Automaticidade e provisoria na V0 textual (Secao 20).** Os dados
  estao estruturados para receber `response latency`, pausas,
  reformulacoes e modalidade de fala no futuro, mas nenhum desses sinais
  e capturado hoje — `ProductionResult` e o unico sinal disponivel.
- **Um card FSRS por competencia, nao por dimensao.** Ver DECISIONS.md
  #4. Se experiencia real mostrar que dimensoes esquecem em ritmos muito
  diferentes, isso e uma mudanca aditiva no adapter, nao uma reescrita.

## Tecnicas

- **Cluster de evidencia e escopado a UMA sessao, sem janela de tempo
  explicita (v0.2).** `evidence_cluster` agrupa por
  `(session_id, activity_type, prompt normalizado)`. Isso impede a
  maioria dos casos de "fingir independencia" (ver DECISIONS.md #6-7),
  mas uma sessao anormalmente longa (varias horas) com o mesmo prompt
  repetido no inicio e no fim continuaria contando como um cluster so -
  o que e conservador (subestima independencia), nao permissivo.
- **Idempotencia de avaliacao nao foi testada sob concorrencia real.**
  `try_claim_evaluation` usa um `UPDATE ... WHERE evaluation_status =
  'pending'` atomico, que deveria ser seguro sob o lock `BEGIN IMMEDIATE`
  do SQLite mesmo com multiplas conexoes tentando completar a mesma
  avaliacao ao mesmo tempo - mas isso nunca foi exercitado com threads ou
  processos reais, so logicamente.
- **Concorrencia real nao foi testada.** SQLite em WAL suporta um
  escritor por vez; para um unico usuario local isso nunca e um
  problema, mas o codigo nao foi testado sob multiplos processos
  escrevendo simultaneamente.
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
4. Recalibrar `rating_from_production_result` para considerar fluencia
   oral, nao so corretude textual.

## Riscos tecnicos observados durante a implementacao

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
  levaria. Nao e um problema na escala desta V0.
- **Ambiguidade original entre "EvidenceEvent" e "EvidenceAssessment" -
  RESOLVIDA no pacote de correcao v0.2.** A V0.1 tinha uma coluna
  `evidence_type` duplicada em `EvidenceEvent`; o pacote de correcao a
  removeu e formalizou a separacao (fatos vs. julgamento) descrita em
  DECISIONS.md #4. Mantido aqui como registro historico do risco que foi
  endereçado.
