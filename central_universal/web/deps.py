"""Dependencias FastAPI: uma conexao SQLite por requisicao, provider e
RuleVersion ativos compartilhados pelo processo."""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path
from typing import Iterator

from fastapi import Depends

from central_universal.domain.entities import RuleVersion
from central_universal.persistence.db import DEFAULT_DB_PATH, connect
from central_universal.persistence.repositories import Repositories
from central_universal.providers.base import Provider
from central_universal.providers.mock import MockProvider
from central_universal.web.bootstrap import DEFAULT_RULE_VERSION

DB_PATH = Path(os.environ.get("CENTRAL_DB_PATH", str(DEFAULT_DB_PATH)))

# Principio 17: trocar de fornecedor nunca e automatico. O provider ativo
# e uma unica linha de configuracao explicita, nao uma descoberta em
# runtime. Trocar para um provider real no futuro significa mudar esta
# linha deliberadamente (ou a config equivalente), nunca um fallback
# silencioso.
ACTIVE_PROVIDER: Provider = MockProvider()


def get_conn() -> Iterator[sqlite3.Connection]:
    conn = connect(DB_PATH)
    try:
        yield conn
    finally:
        conn.close()


def get_repos(conn: sqlite3.Connection = Depends(get_conn)) -> Repositories:
    return Repositories(conn)


def get_provider() -> Provider:
    return ACTIVE_PROVIDER


def get_active_rule_version(repos: Repositories = Depends(get_repos)) -> RuleVersion:
    rule_version = repos.rule_versions.get_by_version(DEFAULT_RULE_VERSION)
    if rule_version is None:
        rule_version = repos.rule_versions.get_active()
    if rule_version is None:
        raise RuntimeError("Nenhuma RuleVersion configurada - rode o bootstrap primeiro.")
    return rule_version
