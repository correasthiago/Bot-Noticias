"""Restauracao operacional de backup (Secao 14 do pacote de correcao v0.2).

Nao e uma copia de arquivo ingenua. O procedimento e:

1. VALIDAR o snapshot (PRAGMA integrity_check + confere que parece um
   banco da Central Universal) ANTES de tocar em qualquer coisa.
2. Copiar o snapshot para um arquivo de STAGING no mesmo diretorio do
   banco ativo.
3. Trocar o banco ativo pelo staging de forma ATOMICA (`os.replace`, que
   e atomico dentro do mesmo filesystem) - nunca ha uma janela em que
   `db_path` esta "pela metade".
4. Reabrir o banco, rodar migrations (idempotentes) e integrity_check.
5. Se QUALQUER passo do 3-4 falhar, reverter para uma copia de seguranca
   do banco original feita antes da troca.

`close_connections` e recebido como um callback opcional: quem chama esta
funcao (o processo web, um script) e responsavel por fechar qualquer
conexao que mantenha aberta antes de restaurar - este modulo nao mantem
nenhuma conexao de longa duracao por conta propria (cada requisicao HTTP
ja abre/fecha a sua), mas expõe o parametro para deixar essa
responsabilidade explicita em vez de implicita.
"""

from __future__ import annotations

import os
import shutil
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from central_universal.domain.clock import utc_now
from central_universal.integrity.checks import run_all
from central_universal.persistence.db import connect
from central_universal.persistence.migrations import run_migrations
from central_universal.persistence.repositories import Repositories


@dataclass(frozen=True)
class RestoreResult:
    success: bool
    message: str
    integrity_ok: bool | None = None


def validate_snapshot(backup_path: Path) -> tuple[bool, str]:
    """So-leitura: abre o snapshot numa conexao separada (nao mexe no
    banco ativo) e confere que ele e um SQLite valido e consistente."""

    if not backup_path.exists():
        return False, f"arquivo de backup nao encontrado: {backup_path}"

    try:
        probe = sqlite3.connect(f"file:{backup_path}?mode=ro", uri=True)
        try:
            result = probe.execute("PRAGMA integrity_check;").fetchone()
            if result is None or result[0] != "ok":
                return False, f"PRAGMA integrity_check falhou: {result}"
            tables = {
                r[0]
                for r in probe.execute(
                    "SELECT name FROM sqlite_master WHERE type='table';"
                ).fetchall()
            }
            if "schema_migrations" not in tables:
                return False, "snapshot nao parece ser um banco da Central Universal (schema_migrations ausente)"
        finally:
            probe.close()
    except sqlite3.Error as exc:
        return False, f"snapshot corrompido ou ilegivel: {exc}"

    return True, "snapshot valido"


def restore_from_backup(
    db_path: str | Path,
    backup_path: str | Path,
    close_connections: Callable[[], None] | None = None,
) -> RestoreResult:
    db_path = Path(db_path)
    backup_path = Path(backup_path)

    valid, message = validate_snapshot(backup_path)
    if not valid:
        return RestoreResult(success=False, message=f"restauracao abortada antes de qualquer alteracao: {message}")

    if close_connections is not None:
        close_connections()

    safety_copy = db_path.with_name(db_path.name + f".pre-restore-{utc_now().strftime('%Y%m%dT%H%M%S%f')}")
    staging_path = db_path.with_name(db_path.name + ".restoring")

    had_previous_db = db_path.exists()
    if had_previous_db:
        shutil.copy2(db_path, safety_copy)

    try:
        shutil.copy2(backup_path, staging_path)
        os.replace(staging_path, db_path)  # atomico no mesmo filesystem

        conn = connect(db_path)
        try:
            run_migrations(conn)
            report = run_all(Repositories(conn))
        finally:
            conn.close()

        if not report.ok:
            errors = [f.message for f in report.findings if f.severity == "error"]
            raise RuntimeError(f"integrity_check falhou apos restauracao: {errors}")

    except Exception as exc:  # noqa: BLE001 - fronteira externa deliberada
        if had_previous_db:
            shutil.copy2(safety_copy, db_path)
        else:
            db_path.unlink(missing_ok=True)
        return RestoreResult(success=False, message=f"restauracao falhou e foi revertida: {exc}")
    finally:
        safety_copy.unlink(missing_ok=True)
        staging_path.unlink(missing_ok=True)

    return RestoreResult(success=True, message="restauracao concluida com sucesso", integrity_ok=True)
