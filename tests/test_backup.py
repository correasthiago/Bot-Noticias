from __future__ import annotations

import sqlite3
from pathlib import Path

from central_universal.domain.clock import utc_now_iso
from central_universal.domain.entities import Learner
from central_universal.domain.ids import new_id
from central_universal.persistence.backup import create_backup, list_backups
from central_universal.persistence.repositories import Repositories


def test_backup_produces_consistent_restorable_snapshot(
    conn: sqlite3.Connection, repos: Repositories, tmp_path: Path
):
    learner = Learner(id=new_id(), display_name="Aluno", created_at=utc_now_iso())
    repos.learners.insert(learner)

    event = create_backup(conn, repos, backup_dir=tmp_path / "backups")
    assert event.success is True
    assert event.size_bytes and event.size_bytes > 0

    restored = sqlite3.connect(event.backup_path)
    row = restored.execute("SELECT display_name FROM learner WHERE id = ?;", (learner.id,)).fetchone()
    assert row is not None
    assert row[0] == "Aluno"
    restored.close()

    backups = list_backups(repos)
    assert len(backups) == 1
    assert backups[0].id == event.id


def test_backup_failure_does_not_raise_or_destroy_state(
    conn: sqlite3.Connection, repos: Repositories, tmp_path: Path, monkeypatch
):
    def boom(*args, **kwargs):
        raise sqlite3.OperationalError("disk full (simulado)")

    monkeypatch.setattr("central_universal.persistence.backup.sqlite3.connect", boom)

    event = create_backup(conn, repos, backup_dir=tmp_path / "backups")
    assert event.success is False
    assert event.error_message is not None

    # o estado original continua intacto e consultavel
    assert repos.learners.list_all() == []
    logged = list_backups(repos)
    assert len(logged) == 1
    assert logged[0].success is False


def test_retention_keeps_only_recent_backups(conn: sqlite3.Connection, repos: Repositories, tmp_path: Path):
    backup_dir = tmp_path / "backups"
    for _ in range(5):
        create_backup(conn, repos, backup_dir=backup_dir, retention_keep=2)

    files = list(backup_dir.glob("central-*.db"))
    assert len(files) == 2
    assert len(list_backups(repos, limit=100)) == 5  # historico de eventos preserva tudo
