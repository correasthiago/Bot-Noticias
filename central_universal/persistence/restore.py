"""Restauracao operacional de backup (Secao 14 do pacote de correcao v0.2).

Nao e uma copia de arquivo ingenua. O procedimento e:

1. VALIDAR o snapshot (PRAGMA integrity_check + confere que parece um
   banco da Central Universal) ANTES de tocar em qualquer coisa.
2. Fechar quaisquer conexoes conhecidas do chamador (`close_connections`)
   e so entao tentar ficar EXCLUSIVO no banco ativo: achatar o WAL de
   volta no arquivo principal, sair do modo WAL (o que remove os arquivos
   `-wal`/`-shm`) e MANTER uma transacao `BEGIN EXCLUSIVE` aberta numa
   conexao "guarda" (Secao 5 do pacote de correcao v0.2.1; Secao 3 da
   terceira auditoria pos-entrega). Se ainda houver outra conexao aberta
   no banco ativo nesse momento, a restauracao e ABORTADA aqui, sem tocar
   em nenhum arquivo.
3. Copiar o snapshot para um arquivo de STAGING no mesmo diretorio do
   banco ativo - AINDA com a conexao guarda segurando o lock exclusivo no
   arquivo atual, para que nenhuma conexao nova consiga escrever nele
   durante a copia.
4. Trocar o banco ativo pelo staging de forma ATOMICA (`os.replace`, que
   e atomico dentro do mesmo filesystem) - nunca ha uma janela em que
   `db_path` esta "pela metade" - e so ENTAO, com o arquivo ja trocado,
   remover qualquer sidecar `-wal`/`-shm` orfao e liberar a conexao
   guarda (o lock dela, presa ao arquivo ANTIGO ja substituido, deixou de
   ter efeito util).
5. Reabrir o banco, rodar migrations (idempotentes) e integrity_check.
6. Se QUALQUER passo do 3-5 falhar, reverter para uma copia de seguranca
   do banco original - tambem via copia-para-staging + `os.replace`
   atomico, tentando (best-effort) a mesma exclusividade da etapa 2, para
   que a propria reversao nao fique exposta a uma escrita concorrente.

`close_connections` e recebido como um callback opcional: quem chama esta
funcao (o processo web, um script) e responsavel por fechar qualquer
conexao de longa duracao que mantenha aberta antes de restaurar - este
modulo nao mantem nenhuma conexao de longa duracao por conta propria (cada
requisicao HTTP ja abre/fecha a sua). Mesmo sem esse callback, o passo 2
acima detecta e recusa prosseguir se OUTRA conexao (de qualquer origem,
inclusive um processo diferente, inclusive uma aberta DEPOIS da checagem
mas ANTES da troca) ainda estiver com o banco aberto - usamos o proprio
comportamento nativo do SQLite (`BEGIN EXCLUSIVE`) para isso, em vez de
reimplementar um lock proprio.
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


def _acquire_exclusive_guard(db_path: Path) -> tuple[bool, sqlite3.Connection | None]:
    """Tenta ficar EXCLUSIVO no banco em `db_path` e MANTEM a conexao
    aberta com uma transacao `BEGIN EXCLUSIVE` ate o chamador liberar
    (`_release_guard`) - protegendo toda a janela de copia/troca (ou
    reversao) contra qualquer conexao NOVA que tente escrever nesse
    meio-tempo (Secao 3 da terceira auditoria pos-entrega: "proteja toda a
    operacao... desde a verificacao de exclusividade ate o fim da troca ou
    reversao").

    Primeiro achata o WAL de volta no arquivo principal
    (`wal_checkpoint(TRUNCATE)`) e sai do modo WAL (`journal_mode =
    DELETE`, que remove os arquivos `-wal`/`-shm`) - o proprio SQLite
    recusa essa troca de modo (ou o `BEGIN EXCLUSIVE` seguinte) com
    `OperationalError: database is locked` se QUALQUER outra conexao
    ainda tiver o banco aberto; usamos exatamente esse comportamento
    nativo como o sinal de "ha uma conexao concorrente", em vez de
    reimplementar um lock proprio.

    Devolve `(True, conexao)` se conseguiu ficar exclusivo (o chamador
    DEVE liberar a conexao com `_release_guard` assim que a janela
    protegida terminar); `(False, None)` se ha uma conexao concorrente
    genuina; `(True, None)` se o banco esta corrompido/ilegivel (o
    desastre que a restauracao existe para corrigir, nao uma conexao
    concorrente legitima - nada real a proteger)."""

    if not db_path.exists():
        return True, None

    conn: sqlite3.Connection | None = None
    try:
        conn = sqlite3.connect(str(db_path), timeout=_LOCK_TIMEOUT_SECONDS)
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE);")
        mode = conn.execute("PRAGMA journal_mode = DELETE;").fetchone()[0]
        if str(mode).lower() != "delete":
            conn.close()
            return False, None
        conn.execute("BEGIN EXCLUSIVE;")
    except sqlite3.OperationalError:
        # SQLITE_BUSY/SQLITE_LOCKED: exatamente a conexao concorrente que
        # devemos recusar atropelar - nunca forcar a troca por baixo dela.
        if conn is not None:
            conn.close()
        return False, None
    except sqlite3.DatabaseError:
        # Banco ativo corrompido/ilegivel - nao ha WAL valido nem lock
        # significativo a manter; removemos sidecars remanescentes por
        # seguranca e deixamos a restauracao prosseguir sem guarda.
        if conn is not None:
            conn.close()
        _remove_wal_sidecars(db_path)
        return True, None

    return True, conn


def _release_guard(guard: sqlite3.Connection | None) -> None:
    """Libera (se houver) a conexao guarda de `_acquire_exclusive_guard`.
    Idempotente e sempre segura de chamar, mesmo se `guard` for None."""

    if guard is None:
        return
    try:
        guard.execute("ROLLBACK;")
    except sqlite3.Error:
        pass
    guard.close()


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

    had_previous_db = db_path.exists()
    safety_copy = db_path.with_name(db_path.name + f".pre-restore-{utc_now().strftime('%Y%m%dT%H%M%S%f')}")
    staging_path = db_path.with_name(db_path.name + ".restoring")
    revert_staging_path = db_path.with_name(db_path.name + ".reverting")

    guard: sqlite3.Connection | None = None
    if had_previous_db:
        # "Impeca novas escritas... trate o WAL... e so entao troque o
        # banco" (Secao 5 do pacote de correcao v0.2.1) - a guarda fica
        # aberta ate o fim da troca (ou reversao), nao so ate esta
        # checagem (Secao 3 da terceira auditoria pos-entrega).
        exclusive, guard = _acquire_exclusive_guard(db_path)
        if not exclusive:
            return RestoreResult(
                success=False,
                message=(
                    "restauracao abortada: outra conexao ainda esta aberta no banco ativo "
                    "(nao foi possivel obter exclusividade) - feche todas as conexoes/sessoes "
                    "e tente novamente. Nenhum arquivo foi alterado."
                ),
            )
        shutil.copy2(db_path, safety_copy)

    try:
        # Ainda protegido pela guarda: nenhuma conexao nova, mesmo uma
        # aberta DEPOIS da checagem de exclusividade acima, consegue
        # escrever no arquivo atual antes da troca abaixo.
        shutil.copy2(backup_path, staging_path)
        os.replace(staging_path, db_path)  # atomico no mesmo filesystem
        _remove_wal_sidecars(db_path)  # o arquivo recem-trocado nunca deve herdar sidecars orfaos

        # A partir daqui o arquivo em `db_path` e outro (foi trocado) - o
        # lock da guarda, preso ao arquivo ANTERIOR, deixou de proteger
        # qualquer coisa util. Libera antes de reabrir para migrations/
        # integrity_check (que gerenciam suas proprias transacoes).
        _release_guard(guard)
        guard = None

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
        _release_guard(guard)
        guard = None
        if had_previous_db:
            revert_guard: sqlite3.Connection | None = None
            if db_path.exists():
                # Best-effort: tenta a mesma exclusividade para a propria
                # reversao, mas reverte de qualquer forma se nao
                # conseguir - deixar o banco no estado com falha seria
                # pior do que uma reversao sem garantia extra de
                # exclusividade.
                _, revert_guard = _acquire_exclusive_guard(db_path)
            shutil.copy2(safety_copy, revert_staging_path)
            os.replace(revert_staging_path, db_path)  # atomico, nunca bytes soltos sobre db_path
            _remove_wal_sidecars(db_path)
            _release_guard(revert_guard)
        else:
            db_path.unlink(missing_ok=True)
            _remove_wal_sidecars(db_path)
        return RestoreResult(success=False, message=f"restauracao falhou e foi revertida: {exc}")
    finally:
        _release_guard(guard)
        safety_copy.unlink(missing_ok=True)
        staging_path.unlink(missing_ok=True)
        revert_staging_path.unlink(missing_ok=True)
        _remove_wal_sidecars(staging_path)
        _remove_wal_sidecars(revert_staging_path)

    return RestoreResult(success=True, message="restauracao concluida com sucesso", integrity_ok=True)
