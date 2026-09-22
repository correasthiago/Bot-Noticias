"""Backup consistente via SQLite Online Backup API (Secao 26).

Deliberadamente NAO copiamos o arquivo .db com `shutil.copy`: um banco em
WAL pode estar com paginas nao sincronizadas no arquivo principal, e uma
copia bruta enquanto ha escritas em andamento pode gerar um arquivo
corrompido. `sqlite3.Connection.backup()` usa a Online Backup API do
SQLite (mesma API nativa, exposta pelo modulo padrao do Python) e produz
um snapshot consistente mesmo com o banco de producao ativo.

Falha de backup nunca derruba a aplicacao nem apaga dado valido (Secao 25):
qualquer excecao aqui vira um BackupEvent com success=False, nunca uma
excecao que propaga para o chamador.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from central_universal.domain.clock import utc_now, utc_now_iso
from central_universal.domain.entities import BackupEvent
from central_universal.domain.ids import new_id
from central_universal.persistence.repositories import Repositories

DEFAULT_BACKUP_DIR = Path(__file__).resolve().parent.parent.parent / "data" / "backups"

# Politica de retencao simples (Secao 26): mantem os N backups bem
# sucedidos mais recentes. Nao ha requisito de calibrar isso com dados
# reais ainda - e um numero pequeno e razoavel para uso local de uma
# pessoa so.
RETENTION_KEEP = 14


def create_backup(
    conn: sqlite3.Connection,
    repos: Repositories,
    backup_dir: Path = DEFAULT_BACKUP_DIR,
    retention_keep: int = RETENTION_KEEP,
) -> BackupEvent:
    backup_dir.mkdir(parents=True, exist_ok=True)
    timestamp = utc_now().strftime("%Y%m%dT%H%M%S%fZ")
    path = backup_dir / f"central-{timestamp}-{new_id()[:8]}.db"
    started_at = utc_now_iso()

    try:
        destination = sqlite3.connect(str(path))
        try:
            conn.backup(destination)
        finally:
            destination.close()
        size_bytes = path.stat().st_size
        event = BackupEvent(
            id=new_id(),
            backup_path=str(path),
            started_at=started_at,
            completed_at=utc_now_iso(),
            success=True,
            size_bytes=size_bytes,
            retention_note=f"mantendo os {retention_keep} backups bem sucedidos mais recentes",
        )
    except Exception as exc:  # noqa: BLE001 - fronteira externa deliberada
        event = BackupEvent(
            id=new_id(),
            backup_path=str(path),
            started_at=started_at,
            completed_at=utc_now_iso(),
            success=False,
            size_bytes=None,
            retention_note="",
            error_message=str(exc),
        )

    repos.backup_events.insert(event)

    if event.success:
        _apply_retention(backup_dir, retention_keep)

    return event


def _apply_retention(backup_dir: Path, keep: int) -> None:
    files = sorted(
        backup_dir.glob("central-*.db"), key=lambda p: p.stat().st_mtime, reverse=True
    )
    for stale in files[keep:]:
        stale.unlink(missing_ok=True)


def list_backups(repos: Repositories, limit: int = 20) -> list[BackupEvent]:
    return repos.backup_events.list_recent(limit)
