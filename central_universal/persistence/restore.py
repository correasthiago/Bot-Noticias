"""Restauracao operacional de backup (Secao 14 do pacote de correcao v0.2).

Nao e uma copia de arquivo ingenua. O procedimento e:

1. VALIDAR o snapshot (PRAGMA integrity_check + confere que parece um
   banco da Central Universal) ANTES de tocar em qualquer coisa.
2. Fechar quaisquer conexoes conhecidas do chamador (`close_connections`)
   e so entao tentar ficar EXCLUSIVO no banco ativo: achatar o WAL de
   volta no arquivo principal e sair do modo WAL (o que remove os
   arquivos `-wal`/`-shm`) - Secao 5 do pacote de correcao v0.2.1. Se
   ainda houver outra conexao aberta no banco ativo nesse momento, a
   restauracao e ABORTADA aqui, sem tocar em nenhum arquivo: nunca
   forcamos a troca por baixo de uma escrita em andamento.
3. Copiar o snapshot para um arquivo de STAGING no mesmo diretorio do
   banco ativo.
4. Trocar o banco ativo pelo staging de forma ATOMICA (`os.replace`, que
   e atomico dentro do mesmo filesystem) - nunca ha uma janela em que
   `db_path` esta "pela metade" - e so ENTAO, com o arquivo ja trocado,
   remover qualquer sidecar `-wal`/`-shm` orfao que possa ter sobrado.
5. Reabrir o banco, rodar migrations (idempotentes) e integrity_check.
6. Se QUALQUER passo do 3-5 falhar, reverter para uma copia de seguranca
   do banco original feita antes da troca (e limpar sidecars orfaos da
   tentativa que falhou).

`close_connections` e recebido como um callback opcional: quem chama esta
funcao (o processo web, um script) e responsavel por fechar qualquer
conexao de longa duracao que mantenha aberta antes de restaurar - este
modulo nao mantem nenhuma conexao de longa duracao por conta propria (cada
requisicao HTTP ja abre/fecha a sua). Mesmo sem esse callback, o passo 2
acima detecta e recusa prosseguir se OUTRA conexao (de qualquer origem,
inclusive um processo diferente) ainda estiver com o banco aberto -
usamos o proprio comportamento nativo do SQLite para isso (ver
`_checkpoint_and_clear_wal`), em vez de reimplementar um lock proprio.
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


_LOCK_TIMEOUT_SECONDS = 5.0


@dataclass(frozen=True)
class RestoreResult:
    success: bool
    message: str
    integrity_ok: bool | None = None


def _checkpoint_and_clear_wal(db_path: Path) -> bool:
    """Tenta ficar EXCLUSIVO no banco em `db_path`: achata o WAL de volta
    no arquivo principal (`wal_checkpoint(TRUNCATE)`) e troca para
    `journal_mode = DELETE`, o que remove os arquivos `-wal`/`-shm` do
    disco (Secao 5 do pacote de correcao v0.2.1: "trate WAL e seus
    arquivos auxiliares").

    Se outra conexao ainda tiver o banco aberto, o proprio SQLite recusa
    o checkpoint/a troca de modo com `OperationalError: database is
    locked` (apos esperar ate `_LOCK_TIMEOUT_SECONDS`) - usamos
    exatamente esse comportamento nativo como o sinal de "ha uma conexao
    concorrente", em vez de reimplementar um lock proprio: devolve False
    nesse caso, e o chamador deve abortar sem tocar em nenhum arquivo."""

    if not db_path.exists():
        return True

    try:
        conn = sqlite3.connect(str(db_path), timeout=_LOCK_TIMEOUT_SECONDS)
        try:
            conn.execute("PRAGMA wal_checkpoint(TRUNCATE);")
            mode = conn.execute("PRAGMA journal_mode = DELETE;").fetchone()[0]
        finally:
            conn.close()
    except sqlite3.OperationalError:
        # SQLITE_BUSY/SQLITE_LOCKED: exatamente a conexao concorrente que
        # devemos recusar atropelar - nunca forcar a troca por baixo dela.
        return False
    except sqlite3.DatabaseError:
        # Banco ativo corrompido/ilegivel - o desastre que a restauracao
        # existe para corrigir, nao uma conexao concorrente legitima. Nao
        # ha WAL valido para tratar aqui; removemos sidecars remanescentes
        # por seguranca e deixamos a restauracao prosseguir.
        _remove_wal_sidecars(db_path)
        return True

    return str(mode).lower() == "delete"


def _remove_wal_sidecars(db_path: Path) -> None:
    for suffix in ("-wal", "-shm"):
        db_path.with_name(db_path.name + suffix).unlink(missing_ok=True)


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
        # "Impeca novas escritas... trate o WAL... e so entao troque o
        # banco" (Secao 5 do pacote de correcao v0.2.1): so seguimos se
        # conseguirmos, de fato, ficar sozinhos no banco ativo agora.
        exclusive = _checkpoint_and_clear_wal(db_path)
        if not exclusive:
            return RestoreResult(
                success=False,
                message=(
                    "restauracao abortada: outra conexao ainda esta aberta no banco ativo "
                    "(nao foi possivel sair do modo WAL com exclusividade) - feche todas as "
                    "conexoes/sessoes e tente novamente. Nenhum arquivo foi alterado."
                ),
            )
        shutil.copy2(db_path, safety_copy)

    try:
        shutil.copy2(backup_path, staging_path)
        os.replace(staging_path, db_path)  # atomico no mesmo filesystem
        _remove_wal_sidecars(db_path)  # o arquivo recem-trocado nunca deve herdar sidecars orfaos

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
        _remove_wal_sidecars(db_path)  # remove sidecars orfaos deixados pela tentativa que falhou
        return RestoreResult(success=False, message=f"restauracao falhou e foi revertida: {exc}")
    finally:
        safety_copy.unlink(missing_ok=True)
        staging_path.unlink(missing_ok=True)
        _remove_wal_sidecars(staging_path)

    return RestoreResult(success=True, message="restauracao concluida com sucesso", integrity_ok=True)
