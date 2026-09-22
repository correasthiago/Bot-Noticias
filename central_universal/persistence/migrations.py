"""Mecanismo minimo de migrations (Secao 5: "criar mecanismo de migrations
desde V0").

Migrations sao arquivos .sql numerados em `persistence/migrations/`,
aplicados em ordem e registrados em `schema_migrations`. Aplicar a mesma
migration duas vezes e um no-op seguro.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from central_universal.domain.clock import utc_now_iso

MIGRATIONS_DIR = Path(__file__).resolve().parent / "migrations"


def _ensure_migrations_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS schema_migrations (
            filename TEXT PRIMARY KEY,
            applied_at TEXT NOT NULL
        ) STRICT;
        """
    )


def applied_migrations(conn: sqlite3.Connection) -> set[str]:
    _ensure_migrations_table(conn)
    rows = conn.execute("SELECT filename FROM schema_migrations;").fetchall()
    return {row["filename"] for row in rows}


def available_migrations() -> list[Path]:
    return sorted(MIGRATIONS_DIR.glob("*.sql"))


def run_migrations(conn: sqlite3.Connection) -> list[str]:
    """Aplica todas as migrations pendentes, em ordem. Retorna os nomes
    aplicados nesta chamada."""

    _ensure_migrations_table(conn)
    already = applied_migrations(conn)
    newly_applied: list[str] = []

    for path in available_migrations():
        if path.name in already:
            continue
        sql = path.read_text(encoding="utf-8")
        # sqlite3.Connection.executescript() emite um COMMIT implicito antes
        # de rodar (encerrando qualquer transacao pendente) e cada instrucao
        # DDL do script e atomica por si so em modo autocommit; por isso nao
        # envolvemos o script em BEGIN/COMMIT explicito aqui.
        conn.executescript(sql)
        conn.execute(
            "INSERT INTO schema_migrations (filename, applied_at) VALUES (?, ?);",
            (path.name, utc_now_iso()),
        )
        newly_applied.append(path.name)

    return newly_applied
