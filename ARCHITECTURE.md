# Arquitetura — Central Universal V0.1

## Visao geral

Monolito modular, local-first, web local. Um processo Python roda um
servidor FastAPI que serve tanto a API quanto o HTML renderizado no
servidor; o navegador acessa `http://127.0.0.1:8000`. Nao ha
microsservicos, nao ha Docker obrigatorio, nao ha dependencia de nuvem.

```
navegador  <-- HTML/CSS/JS simples -->  FastAPI (central_universal/web)
                                              |
                          orchestration.SessionOrchestrator
                                              |
        +---------+---------+---------+---------+---------+
        |         |         |         |         |         |
     evidence  decision   memory     tutor   evaluator  integrity
        |         |         |         |         |
        +---------+----+----+---------+---------+
                       |
                 persistence (SQLite local)
                       |
                    providers (MockProvider / futuro real)
```

## Modulos e responsabilidades

- **domain** — entidades (`entities.py`), enums (`enums.py`), geracao de
  IDs opacos (`ids.py`) e fonte unica de tempo (`clock.py`). Nao importa
  nenhum outro modulo do projeto: e a base.

- **persistence** — `db.py` (conexao SQLite com `PRAGMA foreign_keys=ON`
  e WAL em CADA conexao), `migrations.py` (runner numerado e idempotente),
  `migrations/0001_init.sql` (schema STRICT), `repositories.py` (unica
  camada que escreve SQL) e `backup.py` (Online Backup API do SQLite).
  Nenhuma regra pedagogica vive aqui.

- **evidence** — `aggregation.py` (funcao pura `classify_dimension`, o
  coracao do motor: deriva `CompetencyDimensionState` a partir de uma
  lista de evidencia) e `service.py` (`EvidenceService`, que grava
  `RawInteraction`/`EvidenceEvent`/`EvidenceAssessment` com idempotencia e
  aciona o recomputo de `CompetencyState`).

- **decision** — `engine.py` (funcoes puras `route_competency` e
  `choose_next_action`, sem I/O, sem LLM) e `service.py`
  (`DecisionService`, que monta o `CompetencySnapshot` a partir do banco e
  grava `DecisionEvent`).

- **memory** — `fsrs_adapter.py`: UNICO lugar do sistema que importa o
  pacote `fsrs`. O resto do codigo so conhece `MemoryState` (dataclass do
  dominio) e `MemoryReviewError` — nunca `fsrs.Card` diretamente.

- **tutor** / **evaluator** — contratos (`contract.py`) com dataclasses
  de entrada/saida e, no caso do avaliador, validacao estrutural
  (`validate_evaluator_payload`) que rejeita qualquer resposta malformada
  antes que ela possa virar evidencia. `service.py` em cada um liga o
  contrato a um `Provider` concreto e grava `ProviderEvent`.

- **providers** — `base.py` (interface abstrata `Provider`) e
  `mock.py` (`MockProvider`, 100% local/deterministico, sem rede).

- **integrity** — `checks.py`: dez verificacoes automaticas (Secao 23),
  todas somente-leitura, agregadas em `run_all`.

- **orchestration** — `session_service.py` (`SessionOrchestrator`): o
  UNICO modulo que conhece evidence + decision + memory + tutor +
  evaluator ao mesmo tempo. Implementa o ciclo completo descrito na
  missao da V0. Nao cria nenhuma regra pedagogica nova — so chama, na
  ordem certa, o que os outros modulos ja implementam.

- **web** — FastAPI + Jinja2 + HTML simples. `bootstrap.py` garante
  migrations + learner padrao + RuleVersion ativa + seed do grafo de
  ingles na primeira execucao. `deps.py` centraliza QUAL provider e QUAL
  RuleVersion estao ativos (Principio 17: nunca automatico).

## Por que FastAPI (mesmo sendo um so usuario local)

FastAPI cria uma fronteira limpa entre interface e nucleo pedagogico: o
`SessionOrchestrator` nao sabe que HTTP existe, e a camada web e uma
consumidora fina dele. Isso significa que um app mobile, uma interface de
voz ou um cliente CLI futuro podem reusar o mesmo motor sem tocar em
nenhuma regra pedagogica — so escrever um novo adapter fino, do mesmo jeito
que `web/app.py` e hoje. A geracao automatica de OpenAPI (`/docs`) tambem
vem de graca para essa integracao futura.

## Fluxo de uma interacao (o ciclo da Secao 1)

1. `SessionOrchestrator.choose_focus_competency` varre as competencias e
   usa `DecisionService.preview_routing` (sem gravar `DecisionEvent`,
   para nao poluir o log so por visualizacao) ate achar uma que nao seja
   `SKIP`.
2. `DecisionService.decide` roda de verdade para a competencia escolhida,
   grava o `DecisionEvent` de roteamento e, se nao for `SKIP`, o de acao
   (Secao 17).
3. `SessionOrchestrator.start_activity` monta um `TutorInput` a partir da
   acao escolhida, chama `TutorService` (que chama o `Provider` e grava
   `ProviderEvent`) e persiste a `Activity`.
4. O aprendiz responde. `SessionOrchestrator.submit_interaction`:
   a. grava a `RawInteraction` (idempotente pela `idempotency_key`);
   b. chama `EvaluatorService` (que valida a resposta do provider antes
      de aceitar qualquer coisa);
   c. se valida, grava `EvidenceEvent` + `EvidenceAssessment` +
      recomputa `CompetencyState` DENTRO de uma transacao SQL explicita
      (tudo ou nada);
   d. chama `MemoryAdapter.review` (FSRS) — falha aqui nunca desfaz o
      passo anterior;
   e. chama `DecisionService.decide` de novo para a proxima acao.
5. A interface web renderiza o resultado e oferece "proxima atividade" ou
   "encerrar sessao".

## Persistencia

SQLite local, tabelas STRICT, `foreign_keys=ON` reativado em toda conexao
(nao e global no arquivo), WAL para permitir leitura concorrente com
escrita local. `raw_interaction`, `evidence_event` e `evidence_assessment`
tem triggers de imutabilidade no proprio banco. `competency_state` e
append-only: o "estado atual" e sempre a linha mais recente por
`(competency_id, dimension)`.

## Extensibilidade deliberada

- Trocar o FSRS por outra biblioteca de repeticao espacada: só
  `memory/fsrs_adapter.py` muda.
- Trocar o MockProvider por um provider real: implementar `Provider` em
  `providers/`, apontar `web/deps.ACTIVE_PROVIDER` para ele. Nenhuma outra
  linha do motor pedagogico muda.
- Adicionar um novo dominio alem de ingles: rodar outro seed (como
  `seed/english_graph.py`) contra as mesmas tabelas `learning_domain`/
  `competency`/`prerequisite_relation`.
