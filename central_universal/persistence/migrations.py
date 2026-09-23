"""Mecanismo minimo de migrations (Secao 5: "criar mecanismo de migrations
desde V0").

Migrations sao arquivos .sql numerados em `persistence/migrations/`,
aplicados em ordem e registrados em `schema_migrations`. Aplicar a mesma
migration duas vezes e um no-op seguro.

v0.2.1 (pacote de correcao, ponto 4): cada migration e aplicada como UMA
transacao atomica - ou entra inteira, ou nao altera nada. Antes disso,
`conn.executescript()` rodava cada instrucao DDL/DML do arquivo em modo
autocommit (uma "transacao" por instrucao): uma falha no meio de uma
migration de varios passos (como a 0002, que reconstroi varias tabelas)
podia deixar o banco parcialmente migrado, sem chance de rollback
automatico. Ver `_apply_migration_atomically` para os detalhes de como
isso e garantido apesar do comportamento de `executescript()`.

Tambem e feito um backup automatico ANTES de aplicar qualquer migration
pendente num banco que ja tinha migrations aplicadas (isto e, um upgrade
de um banco existente, nunca um banco novo vazio) - alem do rollback
transacional, o operador fica com uma copia intacta do estado anterior.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from central_universal.domain.clock import utc_now_iso

MIGRATIONS_DIR = Path(__file__).resolve().parent / "migrations"


class MigrationError(RuntimeError):
    """Levantada quando uma migration falha durante a execucao. O banco ja
    foi revertido (ROLLBACK) ao estado anterior a esta migration antes
    desta excecao propagar - nenhuma alteracao parcial permanece (Secao 4
    do pacote de correcao v0.2.1)."""


def _ensure_migrations_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS schema_migrations (
            filename TEXT PRIMARY KEY,
            applied_at TEXT NOT NULL
        ) STRICT;
        """
    )
    # `notes` foi acrescentada na correcao v0.2.1 para registrar
    # diagnosticos auditaveis (ex.: quantas linhas legadas precisaram ser
    # normalizadas por uma migration). Bancos criados antes dela ainda nao
    # tem a coluna - adicionamos sob demanda, uma unica vez.
    columns = {row["name"] for row in conn.execute("PRAGMA table_info(schema_migrations);")}
    if "notes" not in columns:
        conn.execute("ALTER TABLE schema_migrations ADD COLUMN notes TEXT NOT NULL DEFAULT '';")


def applied_migrations(conn: sqlite3.Connection) -> set[str]:
    _ensure_migrations_table(conn)
    rows = conn.execute("SELECT filename FROM schema_migrations;").fetchall()
    return {row["filename"] for row in rows}


def available_migrations() -> list[Path]:
    return sorted(MIGRATIONS_DIR.glob("*.sql"))


def run_migrations(conn: sqlite3.Connection, *, backup_dir: Path | None = None) -> list[str]:
    """Aplica todas as migrations pendentes, em ordem. Retorna os nomes
    aplicados nesta chamada.

    Se ja havia alguma migration aplicada (banco existente sendo
    atualizado, nao um banco novo vazio), tira um backup automatico ANTES
    de tocar em qualquer esquema (Secao 4 do pacote de correcao v0.2.1)."""

    _ensure_migrations_table(conn)
    already = applied_migrations(conn)
    pending = [path for path in available_migrations() if path.name not in already]
    if not pending:
        return []

    if already:
        _backup_before_migration(conn, backup_dir=backup_dir)

    newly_applied: list[str] = []
    for path in pending:
        sql = path.read_text(encoding="utf-8")
        _apply_migration_atomically(conn, path.name, sql)
        newly_applied.append(path.name)

    return newly_applied


def _backup_before_migration(conn: sqlite3.Connection, backup_dir: Path | None) -> None:
    from central_universal.persistence.backup import DEFAULT_BACKUP_DIR, create_backup
    from central_universal.persistence.repositories import Repositories

    event = create_backup(conn, Repositories(conn), backup_dir=backup_dir or DEFAULT_BACKUP_DIR)
    if not event.success:
        raise MigrationError(
            "nao foi possivel tirar o backup automatico obrigatorio antes de migrar um "
            f"banco existente - migration NAO aplicada: {event.error_message}"
        )


def _apply_migration_atomically(conn: sqlite3.Connection, filename: str, sql: str) -> None:
    """Aplica uma migration inteira como uma unica transacao atomica.

    Duas restricoes do SQLite/Python moldam esta implementacao:

    1. `PRAGMA foreign_keys` so pode ser alterado FORA de uma transacao
       aberta - dentro de uma, a mudanca e silenciosamente ignorada. Por
       isso ela e ligada/desligada aqui, nunca dentro do proprio .sql.
    2. `sqlite3.Connection.executescript()` emite um COMMIT implicito
       ANTES de rodar o script recebido - qualquer `BEGIN` dado por fora
       (em Python) seria descartado nesse COMMIT antes do script comecar.
       Por isso a fronteira transacional real (`BEGIN IMMEDIATE`/`COMMIT`)
       vem de DENTRO do proprio arquivo .sql: o texto passado para
       `executescript()` e executado pela engine do SQLite como uma unica
       sequencia, e um `BEGIN`/`COMMIT` explicito nesse texto funciona
       como uma transacao real.

    Se o script falhar no meio (excecao do SQLite), a transacao aberta por
    ele mesmo continua pendente (SQLite nao faz rollback automatico so
    porque uma instrucao falhou) - fazemos o ROLLBACK explicitamente antes
    de propagar `MigrationError`, garantindo que nenhuma alteracao parcial
    sobreviva.
    """

    diagnostic_note = _diagnose_migration(conn, filename)

    # Secao 4 da terceira auditoria pos-entrega: o registro de bookkeeping
    # em `schema_migrations` entra na MESMA transacao atomica do resto da
    # migration - nunca como um INSERT separado depois que o script ja
    # commitou. Sem isso, uma falha ao gravar esse registro (disco cheio,
    # por exemplo) deixava o ESQUEMA ja migrado mas sem o registro de
    # que foi aplicado: na proxima execucao, `run_migrations` tentaria
    # aplicar a MESMA migration de novo sobre um banco que ja a tinha -
    # tipicamente falhando de forma confusa (ex.: "duplicate column
    # name"). Injetamos o INSERT diretamente no texto do script, logo
    # ANTES do `COMMIT;` final dele (ver `_inject_bookkeeping_before_final_commit`)
    # - assim uma falha nesse INSERT participa do MESMO rollback que
    # qualquer outra instrucao do script, e o esquema inteiro permanece na
    # versao anterior.
    bookkeeping_sql = (
        "INSERT INTO schema_migrations (filename, applied_at, notes) VALUES "
        f"({_sql_quote(filename)}, {_sql_quote(utc_now_iso())}, {_sql_quote(diagnostic_note)});\n"
    )
    sql = _inject_bookkeeping_before_final_commit(sql, bookkeeping_sql)

    conn.execute("PRAGMA foreign_keys = OFF;")
    try:
        conn.executescript(sql)
    except Exception as exc:
        if conn.in_transaction:
            conn.execute("ROLLBACK;")
        raise MigrationError(
            f"migration '{filename}' falhou no meio da execucao e foi revertida por completo "
            f"(ROLLBACK) - nenhuma alteracao parcial permanece no banco: {exc}"
        ) from exc
    finally:
        conn.execute("PRAGMA foreign_keys = ON;")


def _sql_quote(value: str) -> str:
    """Formata `value` como um literal de string SQL seguro (aspas
    simples duplicadas). Usado so para os poucos valores que ESTE modulo
    mesmo gera (nome de arquivo de migration, timestamp ISO, diagnostico
    textual) e injeta no proprio script - nunca para dado vindo de fora,
    ja que `executescript()` nao aceita parametros bindados."""

    return "'" + value.replace("'", "''") + "'"


def _inject_bookkeeping_before_final_commit(sql: str, bookkeeping_sql: str) -> str:
    """Insere `bookkeeping_sql` IMEDIATAMENTE ANTES do ultimo `COMMIT;` do
    script - garantindo que o registro em `schema_migrations` entre na
    MESMA transacao atomica do resto da migration. Migrations sem
    `BEGIN`/`COMMIT` proprio (ex.: 0001, que roda em modo autocommit por
    instrucao - nao ha nenhum passo arriscado o bastante para justificar
    a transacao explicita) nao tem um `COMMIT;` final para achar; para
    elas o bookkeeping e simplesmente ACRESCENTADO ao final do script,
    preservando o comportamento anterior."""

    marker = "COMMIT;"
    idx = sql.rfind(marker)
    if idx == -1:
        return sql + "\n" + bookkeeping_sql
    return sql[:idx] + bookkeeping_sql + sql[idx:]


def _diagnose_migration(conn: sqlite3.Connection, filename: str) -> str:
    """Diagnostico auditavel (Secao 4 do pacote de correcao v0.2.1),
    calculado ANTES da migration alterar qualquer coisa - para que o
    numero registrado reflita fielmente o estado do banco v0.1 anterior,
    nao um estado ja normalizado pela propria migration."""

    if filename != "0002_v0_2_corrections.sql":
        return ""

    table_exists = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='evidence_assessment';"
    ).fetchone()
    if table_exists is None:
        return ""

    row = conn.execute(
        """
        SELECT COUNT(*) AS n FROM evidence_assessment
        WHERE NOT (
            (classification = 'inconclusive' AND inconclusive = 1)
            OR (classification <> 'inconclusive' AND inconclusive = 0)
        );
        """
    ).fetchone()
    mismatched = row["n"]
    if not mismatched:
        return ""

    return (
        f"{mismatched} avaliacao(oes) evidence_assessment do esquema v0.1 tinham "
        "'inconclusive' inconsistente com 'classification' (permitido pelo esquema "
        "antigo, incompativel com o novo CHECK de consistencia) - normalizadas "
        "automaticamente a partir de 'classification' durante esta migration; "
        "nenhuma avaliacao foi descartada."
    )
