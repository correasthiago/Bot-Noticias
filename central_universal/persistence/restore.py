"""Restauracao operacional de backup (Secao 14 do pacote de correcao v0.2).

Nao e uma copia de arquivo ingenua. O procedimento e:

1. VALIDAR o snapshot (PRAGMA integrity_check + confere que parece um
   banco da Central Universal) ANTES de tocar em qualquer coisa.
2. PAUSAR a aplicacao: `_pause_for_restore()` adquire exclusividade de
   ESCRITOR sobre o portao em memoria - rejeitando IMEDIATAMENTE uma
   segunda restauracao que tente comecar enquanto esta ja estiver em
   andamento (nunca as duas "dentro" ao mesmo tempo) - e so entao espera
   qualquer requisicao com uma conexao ja aberta (`web/deps.py:get_conn`,
   via `reader_slot()`) terminar, bloqueando NOVAS requisicoes nesse
   meio-tempo. Fechar quaisquer conexoes de longa duracao conhecidas do
   chamador (`close_connections`) tambem acontece aqui.
3. Confirmar exclusividade no banco ativo (achatar o WAL de volta no
   arquivo principal, sair do modo WAL) - se OUTRA conexao ainda tiver o
   banco aberto nesse momento, a restauracao e ABORTADA, sem tocar em
   nenhum arquivo. A conexao de sondagem e SEMPRE fechada logo em
   seguida, nunca mantida aberta durante a troca (ver nota sobre Windows
   abaixo).
4. Copiar o snapshot para um arquivo de STAGING no mesmo diretorio do
   banco ativo, e SO ENTAO trocar o banco ativo pelo staging de forma
   ATOMICA (`os.replace`) - com NENHUMA conexao sqlite3 aberta para
   `db_path` neste processo.
5. Reabrir o banco (conexao nova), rodar migrations (idempotentes) e
   integrity_check.
6. Se QUALQUER passo do 4-5 falhar, reverter para uma copia de seguranca
   do banco original - tambem via copia-para-staging + `os.replace`
   atomico, com a mesma checagem de exclusividade best-effort e sem
   conexao aberta durante a troca. Se a PROPRIA reversao falhar, isso
   NUNCA propaga como excecao nao tratada: o banco original permanece
   preservado (a copia de seguranca so e apagada quando a reversao
   realmente terminou com sucesso) e um `RestoreResult` legivel e sempre
   devolvido, com as duas falhas descritas.
7. DESPAUSAR a aplicacao (`finally` do portao) - so entao novas
   requisicoes voltam a ser atendidas.

**Por que a guarda deixou de MANTER uma transacao `BEGIN EXCLUSIVE`
aberta durante o `os.replace`:** uma revisao anterior segurava a conexao
de sondagem aberta do inicio ao fim da troca para bloquear qualquer
escritor novo. Isso funciona no Linux/macOS (`rename()` nao se importa
com quem tem o arquivo aberto), mas quebra no Windows:
`os.replace`/`MoveFileEx` recusa substituir um arquivo que QUALQUER
processo - inclusive o proprio - ainda tem aberto, levantando
`PermissionError: [WinError 5]` (relatado por um usuario rodando a suite
em Windows). A protecao correta e PORTATIL entre plataformas e,
portanto, em duas camadas: (a) o portao em memoria acima, que impede a
PROPRIA aplicacao de abrir novas conexoes durante a janela inteira da
restauracao (troca ou reversao), e (b) a checagem de exclusividade no
banco antes de cada troca, que detecta uma conexao concorrente genuina
(de qualquer origem) - mas, ao contrario da revisao anterior, nunca
mantida presa durante a copia/`os.replace` em si.

**Por que o portao virou uma coordenacao leitor/escritor de verdade (`threading.Condition`),
nao um booleano simples:** a primeira versao do portao (`_restore_in_progress`
como um `bool` isolado, ligado/desligado por fora de qualquer lock
compartilhado com `get_conn`) tinha DOIS problemas relatados por um
usuario apos testar em Windows: (1) duas chamadas a `_pause_for_restore()`
simultaneas podiam ambas "entrar" (o booleano so registra
ligado/desligado, nao QUANTAS restauracoes estao dentro) - quando a
PRIMEIRA saia, ela desligava o portao mesmo com a SEGUNDA ainda em
andamento; (2) havia uma janela real em `get_conn()` entre CONSULTAR o
portao e ABRIR a conexao - uma restauracao podia comecar bem nesse
meio-tempo. A correcao usa um `threading.Condition` compartilhado entre
`_pause_for_restore` (o "escritor") e `reader_slot()` (usado por
`get_conn`, os "leitores"): entrar como leitor e SAIR do portao como
escritor sao, cada um, uma unica operacao atomica sob o mesmo lock -
nunca ha uma janela onde checar e agir sao passos separados. Uma segunda
restauracao encontrando o escritor ja ocupado e rejeitada NA HORA (nunca
espera, nunca "entra" junto); o escritor so avanca depois que TODOS os
leitores registrados terminaram, e novos leitores sao recusados assim que
o escritor comeca a esperar - garantindo que nenhuma conexao aberta via
`get_conn` sobrevive ate o momento em que arquivos sao tocados.

`close_connections` e recebido como um callback opcional: quem chama esta
funcao (o processo web, um script) e responsavel por fechar qualquer
conexao de longa duracao que mantenha aberta antes de restaurar - este
modulo nao mantem nenhuma conexao de longa duracao por conta propria (cada
requisicao HTTP ja abre/fecha a sua).
"""

from __future__ import annotations

import os
import shutil
import sqlite3
import threading
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterator

from central_universal.domain.clock import utc_now
from central_universal.integrity.checks import run_all
from central_universal.persistence.db import connect
from central_universal.persistence.migrations import run_migrations
from central_universal.persistence.repositories import Repositories


_LOCK_TIMEOUT_SECONDS = 5.0

# Coordenacao leitor/escritor em memoria (Secao 3, quarta e quinta
# auditorias pos-entrega - "coordene a pausa de requisicoes no nivel da
# aplicacao" + "serializar o restore exige manter a exclusao da aplicacao
# durante a operacao inteira e coordenar atomicamente a abertura/fechamento
# das conexoes das requisicoes com essa exclusao"). `threading.Condition`
# porque o FastAPI/uvicorn desta V0 roda handlers sincronos numa
# threadpool (nao em processos separados) - o mesmo lock protege tanto o
# estado "uma restauracao esta em andamento" quanto a contagem de
# requisicoes com conexao aberta agora, entao checar-e-agir e sempre
# atomico dos dois lados. Nao protege contra outro PROCESSO do SO; isso
# continua sendo responsabilidade da checagem de exclusividade no proprio
# banco (`_check_exclusive`).
_state_lock = threading.Lock()
_state_cond = threading.Condition(_state_lock)
_restore_in_progress = False
_active_connections = 0


class RestoreAlreadyInProgressError(RuntimeError):
    """Levantada quando uma restauracao tenta comecar enquanto OUTRA ja
    esta em andamento - a segunda e sempre rejeitada na hora, nunca fica
    esperando nem "entra" ao mesmo tempo que a primeira."""


class RestoreBlockedError(RuntimeError):
    """Levantada por `reader_slot()` (usado por `web/deps.py:get_conn`)
    quando uma restauracao ja esta em andamento - nenhuma conexao nova
    pode ser aberta nesse meio-tempo."""


def is_restore_in_progress() -> bool:
    """Consultado por `web/deps.py`/testes: True enquanto uma restauracao
    estiver em andamento. Apenas leitura - `get_conn` usa `reader_slot()`
    para a checagem real (atomica com a abertura da conexao), nunca esta
    funcao sozinha, que sofreria da mesma janela check-then-act que
    motivou esta correcao."""

    with _state_lock:
        return _restore_in_progress


@contextmanager
def reader_slot() -> Iterator[None]:
    """Usado por `get_conn()`: registra "uma requisicao esta com uma
    conexao aberta" ATOMICAMENTE com a checagem do portao - nunca ha uma
    janela entre "consultei o portao" e "abri a conexao" em que uma
    restauracao poderia comecar no meio (o bug relatado). Levanta
    `RestoreBlockedError` (sem incrementar nada) se uma restauracao ja
    estiver em andamento. Enquanto QUALQUER leitor estiver registrado, uma
    restauracao que queira comecar espera ele terminar antes de tocar
    arquivos; uma vez que a restauracao comeca a esperar, novos leitores
    sao recusados imediatamente (evita fila indefinida de leitores
    adiando a restauracao para sempre)."""

    global _active_connections
    with _state_cond:
        if _restore_in_progress:
            raise RestoreBlockedError("restauracao de backup em andamento")
        _active_connections += 1
    try:
        yield
    finally:
        with _state_cond:
            _active_connections -= 1
            if _active_connections == 0:
                _state_cond.notify_all()


@contextmanager
def _pause_for_restore() -> Iterator[None]:
    """O lado "escritor" da coordenacao: rejeita IMEDIATAMENTE
    (`RestoreAlreadyInProgressError`) se outra restauracao ja estiver
    marcada como em andamento - nunca deixa duas "entrarem" juntas, o bug
    relatado. So entao espera (bloqueando novos leitores, ja que
    `_restore_in_progress` ja esta True nesse ponto) qualquer conexao
    ainda aberta via `reader_slot()` terminar, antes de liberar o
    chamador para tocar arquivos."""

    global _restore_in_progress
    with _state_cond:
        if _restore_in_progress:
            raise RestoreAlreadyInProgressError("restauracao de backup ja em andamento")
        _restore_in_progress = True
        while _active_connections > 0:
            _state_cond.wait()
    try:
        yield
    finally:
        with _state_cond:
            _restore_in_progress = False
            _state_cond.notify_all()


@dataclass(frozen=True)
class RestoreResult:
    success: bool
    message: str
    integrity_ok: bool | None = None


def _check_exclusive(db_path: Path) -> bool:
    """Confere que NENHUMA outra conexao tem `db_path` aberto agora,
    achatando o WAL de volta no arquivo principal e saindo do modo WAL -
    e SEMPRE fecha a propria conexao de sondagem antes de devolver,
    nunca a mantem presa (ao contrario de uma revisao anterior: manter
    uma conexao aberta durante o `os.replace` seguinte quebra no Windows,
    que recusa substituir um arquivo ainda aberto por qualquer processo -
    `PermissionError: [WinError 5]`).

    O proprio SQLite recusa a troca de `journal_mode` (ou levanta
    `OperationalError: database is locked`) se QUALQUER outra conexao
    ainda tiver o banco aberto - usamos exatamente esse comportamento
    nativo como o sinal de "ha uma conexao concorrente", em vez de
    reimplementar um lock proprio. Devolve True tambem quando o banco
    esta corrompido/ilegivel (o desastre que a restauracao existe para
    corrigir, nao uma conexao concorrente legitima - nada real a
    proteger)."""

    if not db_path.exists():
        return True

    conn: sqlite3.Connection | None = None
    try:
        conn = sqlite3.connect(str(db_path), timeout=_LOCK_TIMEOUT_SECONDS)
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE);")
        mode = conn.execute("PRAGMA journal_mode = DELETE;").fetchone()[0]
        exclusive = str(mode).lower() == "delete"
    except sqlite3.OperationalError:
        # SQLITE_BUSY/SQLITE_LOCKED: exatamente a conexao concorrente que
        # devemos recusar atropelar - nunca forcar a troca por baixo dela.
        exclusive = False
    except sqlite3.DatabaseError:
        # Banco ativo corrompido/ilegivel - nao ha WAL valido a tratar;
        # removemos sidecars remanescentes por seguranca e deixamos a
        # restauracao prosseguir mesmo sem confirmar exclusividade real.
        _remove_wal_sidecars(db_path)
        exclusive = True
    finally:
        if conn is not None:
            conn.close()

    return exclusive


def _remove_wal_sidecars(db_path: Path) -> None:
    for suffix in ("-wal", "-shm"):
        db_path.with_name(db_path.name + suffix).unlink(missing_ok=True)


def _atomic_replace(source: Path, target_staging: Path, db_path: Path) -> None:
    """Copia `source` para `target_staging` e troca `db_path` por ele via
    `os.replace` (atomico dentro do mesmo filesystem). O chamador precisa
    garantir, ANTES de chamar isto, que nenhuma conexao sqlite3 deste
    processo esta aberta em `db_path` (Windows recusa substituir um
    arquivo aberto - ver docstring do modulo)."""

    shutil.copy2(source, target_staging)
    try:
        os.replace(target_staging, db_path)
    finally:
        target_staging.unlink(missing_ok=True)
    _remove_wal_sidecars(db_path)


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


def _revert_to_safety_copy(db_path: Path, safety_copy: Path | None, had_previous_db: bool) -> str | None:
    """Tenta devolver `db_path` ao estado anterior a restauracao. NUNCA
    levanta - se a propria reversao falhar (disco cheio, permissao,
    o que for), captura o erro e devolve uma mensagem legivel para o
    chamador incluir no `RestoreResult`, em vez de deixar uma excecao
    nao tratada estourar por cima de uma falha que ja estava sendo
    tratada. Devolve None quando a reversao terminou bem."""

    try:
        if had_previous_db and safety_copy is not None:
            _check_exclusive(db_path)  # best-effort - reverte de qualquer forma, mesmo sem confirmar
            revert_staging = db_path.with_name(db_path.name + f".reverting-{utc_now().strftime('%Y%m%dT%H%M%S%f')}")
            _atomic_replace(safety_copy, revert_staging, db_path)
        else:
            db_path.unlink(missing_ok=True)
            _remove_wal_sidecars(db_path)
    except Exception as revert_exc:  # noqa: BLE001 - fronteira externa deliberada
        copy_note = f" Uma copia de seguranca do estado anterior a tentativa continua em {safety_copy}." if safety_copy else ""
        return (
            f"ATENCAO: a reversao automatica TAMBEM falhou ({revert_exc}) - o banco em "
            f"{db_path} pode estar no estado da tentativa de restauracao que falhou, "
            f"nao no estado anterior a ela.{copy_note} Restaure manualmente antes de "
            "continuar usando a aplicacao."
        )
    return None


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

    try:
        return _restore_from_backup_locked(db_path, backup_path, close_connections)
    except RestoreAlreadyInProgressError:
        return RestoreResult(
            success=False,
            message=(
                "restauracao abortada: ja existe outra restauracao de backup em andamento. "
                "Aguarde ela terminar e tente novamente. Nenhum arquivo foi alterado por esta tentativa."
            ),
        )


def _restore_from_backup_locked(
    db_path: Path,
    backup_path: Path,
    close_connections: Callable[[], None] | None,
) -> RestoreResult:
    with _pause_for_restore():
        safety_copy: Path | None = None
        staging_path = db_path.with_name(db_path.name + ".restoring")

        # Preparacao (fechar conexoes de longa duracao do chamador,
        # confirmar exclusividade, tirar a copia de seguranca) tambem
        # participa do tratamento de falha (Secao 2, sexta auditoria
        # pos-entrega): se QUALQUER passo daqui falhar, a troca do banco
        # NUNCA comecou - `_atomic_replace` nem foi chamado, `db_path`
        # continua exatamente como estava - entao nao ha o que reverter.
        # Descartamos qualquer copia de seguranca parcial e devolvemos um
        # `RestoreResult` legivel, nunca deixamos a excecao escapar ate o
        # chamador web (que nao a capturaria).
        try:
            if close_connections is not None:
                close_connections()

            had_previous_db = db_path.exists()
            if had_previous_db:
                if not _check_exclusive(db_path):
                    return RestoreResult(
                        success=False,
                        message=(
                            "restauracao abortada: outra conexao ainda esta aberta no banco ativo "
                            "(nao foi possivel obter exclusividade) - feche todas as conexoes/sessoes "
                            "e tente novamente. Nenhum arquivo foi alterado."
                        ),
                    )
                safety_copy = db_path.with_name(db_path.name + f".pre-restore-{utc_now().strftime('%Y%m%dT%H%M%S%f')}")
                shutil.copy2(db_path, safety_copy)
        except Exception as prep_exc:  # noqa: BLE001 - fronteira externa deliberada
            if safety_copy is not None:
                safety_copy.unlink(missing_ok=True)
            return RestoreResult(
                success=False,
                message=f"restauracao abortada antes de tocar o banco ativo: {prep_exc}",
            )

        try:
            # Nenhuma conexao sqlite3 deste processo esta aberta em
            # `db_path` neste ponto - seguro em qualquer SO.
            _atomic_replace(backup_path, staging_path, db_path)

            conn = connect(db_path)
            try:
                run_migrations(conn)
                report = run_all(Repositories(conn))
            finally:
                conn.close()  # fecha ANTES de qualquer possivel reversao abaixo

            if not report.ok:
                errors = [f.message for f in report.findings if f.severity == "error"]
                raise RuntimeError(f"integrity_check falhou apos restauracao: {errors}")

        except Exception as exc:  # noqa: BLE001 - fronteira externa deliberada
            revert_failure = _revert_to_safety_copy(db_path, safety_copy, had_previous_db)
            if revert_failure is not None:
                return RestoreResult(success=False, message=f"restauracao falhou ({exc}). {revert_failure}")
            if safety_copy is not None:
                safety_copy.unlink(missing_ok=True)
            return RestoreResult(success=False, message=f"restauracao falhou e foi revertida: {exc}")
        else:
            if safety_copy is not None:
                safety_copy.unlink(missing_ok=True)

    return RestoreResult(success=True, message="restauracao concluida com sucesso", integrity_ok=True)
