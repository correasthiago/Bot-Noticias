from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from central_universal.domain.clock import utc_now_iso
from central_universal.domain.entities import Competency, LearningDomain, RuleVersion
from central_universal.domain.ids import new_id
from central_universal.evidence.aggregation import AggregationConfig
from central_universal.persistence.db import connect
from central_universal.persistence.migrations import run_migrations
from central_universal.persistence.repositories import Repositories


@pytest.fixture()
def conn(tmp_path: Path) -> sqlite3.Connection:
    db_path = tmp_path / "test.db"
    connection = connect(db_path)
    run_migrations(connection)
    yield connection
    connection.close()


@pytest.fixture()
def repos(conn: sqlite3.Connection) -> Repositories:
    return Repositories(conn)


@pytest.fixture()
def make_competency(repos: Repositories):
    """Fabrica um Competency valido (com seu LearningDomain) e devolve o id.
    Util em testes que so precisam de um competency_id existente para
    satisfazer as foreign keys, sem montar o cenario pedagogico inteiro."""

    domain = LearningDomain(id=new_id(), code=f"domain-{new_id()[:8]}", name="Dominio de teste")
    repos.domains.insert(domain)

    def _make(code: str | None = None) -> str:
        competency = Competency(
            id=new_id(),
            domain_id=domain.id,
            code=code or f"comp-{new_id()[:8]}",
            name="Competencia de teste",
        )
        repos.competencies.insert(competency)
        return competency.id

    return _make


@pytest.fixture()
def orchestrator_factory(repos: Repositories, tmp_path: Path):
    """Fabrica um SessionOrchestrator com backup_dir isolado em tmp_path,
    para que testes que chamam end_session() (e disparam o backup
    automatico) nunca escrevam no data/backups real do repositorio."""

    from central_universal.orchestration.session_service import SessionOrchestrator

    def _make(provider, rule_version) -> SessionOrchestrator:
        return SessionOrchestrator(repos, provider, rule_version, backup_dir=tmp_path / "backups")

    return _make


@pytest.fixture()
def drive_to_schedule_recall():
    """Avanca uma competencia ate a acao SCHEDULE_RECALL usando SOMENTE o
    fluxo real do orquestrador (escolher foco -> comecar atividade ->
    responder), a mesma sequencia de chamadas que a interface web usa -
    nunca definindo `is_planned_recall` diretamente (Secao 1 do pacote de
    correcao v0.2.1: o primeiro card FSRS precisa nascer pelo fluxo
    normal). Cada interacao usa uma sessao NOVA porque, desde a correcao
    de clustering (Secao 6), repetir a mesma acao pedagogica na MESMA
    sessao para a MESMA competencia conta como um cluster so - precisa de
    sessoes distintas para produzir evidencia genuinamente independente.

    Cada submissao usa um `now` explicito, avancando 2h por iteracao a
    partir de uma data fixa - nunca o relogio real da maquina (Secao 2 da
    terceira auditoria pos-entrega: a PRIMEIRA revisao de memoria verifica
    o intervalo desde a primeira evidencia/aprendizagem; sem um relogio
    controlado, esse intervalo dependeria de quao rapido o teste roda,
    exatamente o tipo de fragilidade que esta auditoria pede para
    eliminar).

    Devolve (session, activity, tutor_output, now) da atividade de
    SCHEDULE_RECALL, ainda SEM resposta submetida - `now` e o mesmo
    relogio controlado, para o teste reusar na sua propria submissao."""

    from datetime import datetime, timedelta, timezone

    from central_universal.domain.enums import HelpLevel, ProductionResult
    from central_universal.domain.ids import new_id as _new_id

    def _drive(orchestrator, learner_id: str, competency_id: str):
        base = datetime(2026, 1, 1, tzinfo=timezone.utc)
        for i in range(30):
            session = orchestrator.start_session(learner_id)
            focus = orchestrator.choose_focus_competency(learner_id)
            assert focus is not None, "competencia foi pulada (SKIP) antes de alcancar SCHEDULE_RECALL"
            focus_competency_id, _routing, action, _recall_due = focus
            assert focus_competency_id == competency_id

            activity, tutor_output = orchestrator.start_activity(
                session_id=session.id, competency_id=competency_id, action=action
            )
            now = base + timedelta(hours=2 * i)
            if activity.is_planned_recall:
                return session, activity, tutor_output, now

            orchestrator.submit_interaction(
                activity_id=activity.id, session_id=session.id, idempotency_key=_new_id(),
                learner_input="resposta espontanea correta", help_level=HelpLevel.A0,
                production_result=ProductionResult.SPONTANEOUS_CORRECT,
                tutor_output_text=tutor_output.utterance if tutor_output else "",
                now=now,
            )
        raise AssertionError("nao alcancou SCHEDULE_RECALL em tempo habil (possivel regressao na ladder do Decisor)")

    return _drive


@pytest.fixture()
def make_rule_version(repos: Repositories):
    """Fabrica e insere uma RuleVersion valida (config_json default) e
    devolve o objeto. Cada chamada cria uma versao nova (version unica)."""

    def _make(version: str | None = None, config: AggregationConfig | None = None) -> RuleVersion:
        rv = RuleVersion(
            id=new_id(),
            version=version or f"v-test-{new_id()[:8]}",
            description="RuleVersion de teste",
            created_at=utc_now_iso(),
            config_json=(config or AggregationConfig()).to_json(),
            algorithm_version="v2-config-driven-clusters-no-autodowngrade",
        )
        repos.rule_versions.insert(rv)
        return rv

    return _make
