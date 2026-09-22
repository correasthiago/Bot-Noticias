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
