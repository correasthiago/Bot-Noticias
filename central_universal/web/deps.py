"""Dependencias FastAPI: uma conexao SQLite por requisicao, provider e
RuleVersion ativos compartilhados pelo processo."""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path
from typing import Iterator

from fastapi import Depends, HTTPException

from central_universal.domain.entities import RuleVersion
from central_universal.persistence.db import DEFAULT_DB_PATH, connect
from central_universal.persistence.repositories import Repositories
from central_universal.persistence.restore import is_restore_in_progress
from central_universal.providers.base import Provider
from central_universal.providers.mock import MockProvider
from central_universal.web.bootstrap import DEFAULT_RULE_VERSION

DB_PATH = Path(os.environ.get("CENTRAL_DB_PATH", str(DEFAULT_DB_PATH)))
BACKUP_DIR = DB_PATH.parent / "backups"

# Principio 17: trocar de fornecedor nunca e automatico. O provider ativo
# e uma unica linha de configuracao explicita, nao uma descoberta em
# runtime. Trocar para um provider real no futuro significa mudar esta
# linha deliberadamente (ou a config equivalente), nunca um fallback
# silencioso.
ACTIVE_PROVIDER: Provider = MockProvider()


def get_conn() -> Iterator[sqlite3.Connection]:
    # Quarta auditoria pos-entrega, Secao 3: enquanto uma restauracao de
    # backup estiver em andamento (`persistence.restore._pause_for_restore`),
    # nenhuma requisicao nova pode abrir uma conexao com o banco - ele
    # pode estar no meio de uma troca de arquivo. 503 e o codigo correto
    # (servico temporariamente indisponivel, tente de novo em breve).
    if is_restore_in_progress():
        raise HTTPException(status_code=503, detail="Restauracao de backup em andamento - tente novamente em instantes.")
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
    rule_version = repos.active_rule_version.get()
    if rule_version is None:
        raise RuntimeError("Nenhuma RuleVersion ativa configurada - rode o bootstrap primeiro.")
    return rule_version
