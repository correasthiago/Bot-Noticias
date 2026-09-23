"""Fabrica de conexoes SQLite.

Requisito explicito da especificacao: SQLite NAO ativa enforcement de
foreign keys automaticamente em todas as conexoes. `PRAGMA foreign_keys=ON`
precisa ser configurado em CADA conexao aberta - nao e uma configuracao
global do arquivo. Por isso toda conexao criada por `connect()` passa por
este PRAGMA antes de ser devolvida ao chamador.
"""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

DEFAULT_DB_PATH = Path(__file__).resolve().parent.parent.parent / "data" / "central.db"


def connect(db_path: str | Path = DEFAULT_DB_PATH) -> sqlite3.Connection:
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(str(path), isolation_level=None, timeout=30)
    conn.row_factory = sqlite3.Row

    # Enforcement de foreign keys e por-conexao em SQLite: precisa ser
    # reativado aqui, sempre.
    conn.execute("PRAGMA foreign_keys = ON;")
    # WAL: permite leituras concorrentes enquanto o servidor local grava,
    # e e o modo recomendado para uso local single-writer.
    conn.execute("PRAGMA journal_mode = WAL;")
    conn.execute("PRAGMA synchronous = NORMAL;")
    return conn


@contextmanager
def transaction(conn: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    """Envolve um bloco de alteracoes de estado em uma transacao explicita.

    Em caso de excecao, faz ROLLBACK completo (Secao 25: "Se banco falhar
    em transacao: operacao inteira deve fazer rollback"). Como a conexao e
    aberta com isolation_level=None (autocommit), o BEGIN IMMEDIATE aqui e
    o unico ponto que define a fronteira da transacao.
    """

    conn.execute("BEGIN IMMEDIATE;")
    try:
        yield conn
    except Exception:
        conn.execute("ROLLBACK;")
        raise
    else:
        conn.execute("COMMIT;")
