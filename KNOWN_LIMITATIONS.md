# Limitacoes conhecidas — Central Universal V0.1

Honestas e explicitas, conforme pedido no relatorio final (Secao 34). Nada
aqui viola a constituicao pedagogica — sao lacunas de escopo/maturidade
da V0, nao de principio.

## Pedagogicas

- **Thresholds de agregacao sao um ponto de partida, nao uma
  calibracao.** `classify_dimension` usa 2/3 clusters independentes
  (ou dias distintos, para retencao) como limiares de
  `demonstrated`/`consolidated`. Sao numeros simples e auditaveis, nao
  uma calibracao com dados reais de aprendizado — a especificacao pede
  exatamente isso (nao inventar pesos sem dados). A V0 deve gerar os
  dados que permitirao revisar esses numeros.
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
   avaliador).
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
- **Ambiguidade real entre "EvidenceEvent" e "EvidenceAssessment".** A
  especificacao original nao deixa 100% claro qual das duas entidades
  carrega o julgamento fino do avaliador; a decisao tomada (DECISIONS.md
  #1) e razoavel mas nao e a unica interpretacao possivel — se a
  intencao original era outra, revisar antes de calibrar qualquer coisa
  em cima disso.
- **Custo de manter triggers de imutabilidade em SQLite.** Funcionam bem
  na V0, mas se o sistema precisar migrar dados historicos no futuro
  (ex.: uma correcao retroativa de bug de gravacao), os triggers vao
  bloquear ate UPDATE administrativo direto — sera necessario um
  procedimento explicito de manutencao (desabilitar trigger, corrigir,
  reabilitar), nao uma operacao trivial.
