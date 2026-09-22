from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from central_universal.domain.entities import Competency, LearningDomain
from central_universal.domain.ids import new_id
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
