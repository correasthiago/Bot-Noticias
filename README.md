# Central Universal de Aprendizagem — V0.1

Motor pedagogico local-first que prova um ciclo completo de aprendizagem
adaptativa: **diagnostico -> competencia -> atividade -> interacao ->
evidencia bruta -> avaliacao -> estado da competencia -> memoria -> decisao
-> proxima atividade -> persistencia longitudinal.**

Esta e a V0: um unico usuario, um unico dominio (ingles), interface
majoritariamente textual, rodando 100% localmente. Veja
`PEDAGOGICAL_CONSTITUTION.md` para os principios que o codigo nunca pode
violar, `ARCHITECTURE.md` para a organizacao interna, `DATA_MODEL.md` para
o schema e `KNOWN_LIMITATIONS.md` para o que ainda falta.

## Requisitos

- Python 3.11+
- Nenhum servico externo, nenhuma chave de API, nenhuma internet
  necessaria para rodar (o `MockProvider` e usado por padrao).

## Instalacao

```bash
python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

## Rodar o servidor local

```bash
python scripts/run_server.py
# ou, com auto-reload durante desenvolvimento:
python scripts/run_server.py --reload
```

Abra `http://127.0.0.1:8000` no navegador. Na primeira execucao o banco
SQLite e criado automaticamente em `data/central.db`, as migrations sao
aplicadas, um `Learner` padrao e uma `RuleVersion` ativa sao criados, e o
grafo de competencias de ingles (Secao 19 da especificacao) e semeado.

Para usar um banco em outro caminho (por exemplo, para testes manuais
isolados):

```bash
CENTRAL_DB_PATH=/tmp/outro.db python scripts/run_server.py
```

## Rodar os testes

```bash
pip install -r requirements.txt   # inclui pytest e httpx
python -m pytest -q
```

Todos os 75 testes (unitarios, integracao, end-to-end e o Red Team
T1-T15) rodam offline, sem rede e sem `MockProvider` precisar de nenhuma
credencial. Veja `TESTING.md` para a lista completa e os resultados.

## Estrutura do projeto

```
central_universal/   pacote principal (domain, evidence, decision, memory,
                      tutor, evaluator, providers, persistence, integrity,
                      orchestration, web)
seed/                 seed do grafo de competencias de ingles
tests/                 suite de testes (pytest)
scripts/run_server.py  ponto de entrada do servidor local
data/                  banco SQLite e backups (gerado em runtime, git-ignored)
```

## Telas da interface web

- `/` — inicio: resumo de competencias pendentes e botao para comecar sessao.
- `/session/{id}` — sessao ativa: atividade atual, formulario de resposta,
  feedback, proxima atividade, encerrar sessao.
- `/map` — mapa de competencias x 6 dimensoes.
- `/competency/{id}` — estado, evidencias, justificativas e proxima revisao
  de uma competencia.
- `/audit` — decisoes recentes, chamadas a providers, versoes de regras e
  resultado do `integrity_check`, alem de backup manual.

## Comandos uteis

Rodar apenas a checagem de integridade a partir de um script:

```bash
python -c "
from central_universal.persistence.db import connect
from central_universal.persistence.repositories import Repositories
from central_universal.integrity.checks import run_all
conn = connect('data/central.db')
report = run_all(Repositories(conn))
print('OK' if report.ok else 'PROBLEMAS ENCONTRADOS')
for f in report.findings:
    print(f.severity, f.check, f.message)
"
```
