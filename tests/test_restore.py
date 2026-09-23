from __future__ import annotations

import sqlite3
from pathlib import Path

from central_universal.domain.clock import utc_now_iso
from central_universal.domain.entities import Learner
from central_universal.domain.ids import new_id
from central_universal.persistence.backup import create_backup, maybe_run_automatic_backup
from central_universal.persistence.db import connect
from central_universal.persistence.migrations import run_migrations
from central_universal.persistence.repositories import Repositories
from central_universal.persistence.restore import restore_from_backup, validate_snapshot


def test_restore_substitutes_and_recovers_the_database(tmp_path: Path):
    db_path = tmp_path / "central.db"
    conn = connect(db_path)
    run_migrations(conn)
    repos = Repositories(conn)
    learner = Learner(id=new_id(), display_name="Antes do backup", created_at=utc_now_iso())
    repos.learners.insert(learner)

    backup_event = create_backup(conn, repos, backup_dir=tmp_path / "backups")
    assert backup_event.success is True

    # Corrompe/perde o banco "ativo": simula desastre real.
    conn.close()
    db_path.write_bytes(b"isto nao e mais um banco sqlite valido")

    result = restore_from_backup(db_path, backup_event.backup_path)
    assert result.success is True
    assert result.integrity_ok is True

    restored_conn = connect(db_path)
    restored_repos = Repositories(restored_conn)
    restored = restored_repos.learners.get(learner.id)
    assert restored is not None
    assert restored.display_name == "Antes do backup"
    restored_conn.close()


def test_restore_rejects_invalid_snapshot_without_touching_active_db(tmp_path: Path):
    db_path = tmp_path / "central.db"
    conn = connect(db_path)
    run_migrations(conn)
    repos = Repositories(conn)
    learner = Learner(id=new_id(), display_name="Original", created_at=utc_now_iso())
    repos.learners.insert(learner)
    conn.close()

    fake_backup = tmp_path / "not-a-real-backup.db"
    fake_backup.write_bytes(b"lixo, nao e sqlite")

    valid, _ = validate_snapshot(fake_backup)
    assert valid is False

    result = restore_from_backup(db_path, fake_backup)
    assert result.success is False

    # o banco original continua intacto
    still_there = connect(db_path)
    assert Repositories(still_there).learners.get(learner.id) is not None
    still_there.close()


def test_restore_reverts_when_integrity_check_fails_after_swap(tmp_path: Path, monkeypatch):
    db_path = tmp_path / "central.db"
    conn = connect(db_path)
    run_migrations(conn)
    repos = Repositories(conn)
    original_learner = Learner(id=new_id(), display_name="Fica", created_at=utc_now_iso())
    repos.learners.insert(original_learner)

    # um snapshot valido, mas de um banco DIFERENTE (para simular sucesso
    # na copia porem falha na checagem pos-restauracao)
    other_db = tmp_path / "other.db"
    other_conn = connect(other_db)
    run_migrations(other_conn)
    other_conn.close()

    conn.close()

    import central_universal.persistence.restore as restore_module

    def fake_run_all(_repos):
        class _FakeReport:
            ok = False
            findings = [type("F", (), {"severity": "error", "message": "forcado no teste"})()]

        return _FakeReport()

    monkeypatch.setattr(restore_module, "run_all", fake_run_all)

    result = restore_from_backup(db_path, other_db)
    assert result.success is False
    assert "revertida" in result.message

    reverted = connect(db_path)
    assert Repositories(reverted).learners.get(original_learner.id) is not None
    reverted.close()


def test_restore_flattens_active_wal_before_swapping(tmp_path: Path):
    """Ponto 5 do pacote de correcao v0.2.1: restaurar por cima de um
    banco com WAL genuinamente ativo (dados commitados so no `-wal`,
    ainda nao levados ao arquivo principal) precisa funcionar - e nao
    pode deixar nenhum sidecar `-wal`/`-shm` orfao grudado no banco
    recem-restaurado."""

    db_path = tmp_path / "central.db"
    conn = connect(db_path)
    run_migrations(conn)
    repos = Repositories(conn)
    # Impede o auto-checkpoint para garantir que o WAL fique realmente
    # ativo (nao seja achatado sozinho pelo SQLite antes do teste rodar).
    conn.execute("PRAGMA wal_autocheckpoint = 0;")
    learner = Learner(id=new_id(), display_name="Commitado so no WAL", created_at=utc_now_iso())
    repos.learners.insert(learner)

    backup_event = create_backup(conn, repos, backup_dir=tmp_path / "backups")
    assert backup_event.success is True

    wal_path = db_path.with_name(db_path.name + "-wal")
    assert wal_path.exists() and wal_path.stat().st_size > 0  # WAL genuinamente ativo

    conn.close()

    result = restore_from_backup(db_path, backup_event.backup_path)
    assert result.success is True

    assert not wal_path.exists()  # nenhum sidecar orfao sobrou grudado no banco restaurado

    restored = connect(db_path)
    assert Repositories(restored).learners.get(learner.id) is not None
    restored.close()


def test_restore_aborts_cleanly_with_concurrent_connection_open(tmp_path: Path):
    """Ponto 5 do pacote de correcao v0.2.1: 'impeca novas escritas' -
    uma conexao concorrente ainda aberta no banco ativo tem que bloquear
    a restauracao (sem tocar em nenhum arquivo), nao arriscar uma
    corrida com a troca atomica do arquivo."""

    db_path = tmp_path / "central.db"
    conn = connect(db_path)
    run_migrations(conn)
    repos = Repositories(conn)
    learner = Learner(id=new_id(), display_name="Original", created_at=utc_now_iso())
    repos.learners.insert(learner)
    backup_event = create_backup(conn, repos, backup_dir=tmp_path / "backups")
    assert backup_event.success is True

    # `conn` continua deliberadamente aberta - simula uma sessao/processo
    # concorrente que ainda nao fechou sua conexao com o banco ativo.
    result = restore_from_backup(db_path, backup_event.backup_path)
    assert result.success is False
    assert "conexao" in result.message

    # nada foi tocado: a mesma conexao concorrente ainda enxerga o original
    assert repos.learners.get(learner.id) is not None
    conn.close()

    # com a conexao concorrente fechada, a restauracao agora funciona
    result2 = restore_from_backup(db_path, backup_event.backup_path)
    assert result2.success is True


def test_restore_holds_no_sqlite_connection_open_across_os_replace(tmp_path: Path, monkeypatch):
    """Quarta auditoria pos-entrega, causa raiz do bug relatado no
    Windows: `os.replace` recusa substituir um arquivo que QUALQUER
    processo - inclusive o proprio - ainda tem aberto
    (`PermissionError: [WinError 5]`). Uma revisao anterior mantinha uma
    conexao guarda aberta durante a copia/troca para bloquear escritores
    concorrentes; isso funcionava no Linux/macOS mas quebrava no Windows.
    Este teste prova, sem depender de rodar em Windows de verdade, que
    NENHUMA conexao sqlite3 deste processo esta aberta no exato instante
    em que `os.replace` e chamado - intercepta `os.replace` e confere que
    `sqlite3.connect` nao tem nenhum handle vivo apontando para
    `db_path` naquele momento (usando `PRAGMA database_list` numa conexao
    de sondagem descartavel so para navegar o arquivo - se outra conexao
    tivesse o arquivo aberto com uma transacao pendente, esta sondagem
    falharia com `database is locked`)."""

    import central_universal.persistence.restore as restore_module

    db_path = tmp_path / "central.db"
    conn = connect(db_path)
    run_migrations(conn)
    repos = Repositories(conn)
    learner = Learner(id=new_id(), display_name="Original", created_at=utc_now_iso())
    repos.learners.insert(learner)
    backup_event = create_backup(conn, repos, backup_dir=tmp_path / "backups")
    assert backup_event.success is True
    conn.close()

    observed: dict[str, object] = {}
    real_os_replace = restore_module.os.replace

    def spy_os_replace(src, dst, *args, **kwargs):
        if str(dst) == str(db_path) and "checked" not in observed:
            observed["checked"] = True
            try:
                probe = sqlite3.connect(str(db_path), timeout=0.2)
                probe.execute("BEGIN EXCLUSIVE;")  # so consegue se NINGUEM mais tiver o arquivo aberto
                probe.execute("ROLLBACK;")
                probe.close()
                observed["was_free"] = True
            except sqlite3.OperationalError as exc:
                observed["was_free"] = False
                observed["error"] = str(exc)
        return real_os_replace(src, dst, *args, **kwargs)

    monkeypatch.setattr(restore_module.os, "replace", spy_os_replace)

    result = restore_from_backup(db_path, backup_event.backup_path)

    assert observed.get("checked") is True  # a janela realmente foi exercitada
    assert observed.get("was_free") is True, observed.get("error")
    assert result.success is True

    restored = connect(db_path)
    assert Repositories(restored).learners.get(learner.id) is not None
    restored.close()


def test_restore_pauses_the_application_gate_and_always_resumes_it(tmp_path: Path, monkeypatch):
    """Ponto 3 da quarta auditoria pos-entrega: 'coordene a pausa de
    requisicoes no nivel da aplicacao' - `is_restore_in_progress()` deve
    estar ligado durante toda a janela de copia/troca (a protecao
    portatil que substitui manter uma conexao SQLite presa, que quebrava
    no Windows) e SEMPRE desligar de novo ao final, mesmo apos sucesso."""

    import central_universal.persistence.restore as restore_module

    db_path = tmp_path / "central.db"
    conn = connect(db_path)
    run_migrations(conn)
    repos = Repositories(conn)
    learner = Learner(id=new_id(), display_name="Original", created_at=utc_now_iso())
    repos.learners.insert(learner)
    backup_event = create_backup(conn, repos, backup_dir=tmp_path / "backups")
    assert backup_event.success is True
    conn.close()

    assert restore_module.is_restore_in_progress() is False

    staging_path = db_path.with_name(db_path.name + ".restoring")
    observed: dict[str, object] = {}
    real_copy2 = restore_module.shutil.copy2

    def spy_copy2(src, dst, *args, **kwargs):
        if str(dst) == str(staging_path) and "checked" not in observed:
            observed["checked"] = True
            observed["gate_on"] = restore_module.is_restore_in_progress()
        return real_copy2(src, dst, *args, **kwargs)

    monkeypatch.setattr(restore_module.shutil, "copy2", spy_copy2)

    result = restore_from_backup(db_path, backup_event.backup_path)

    assert observed.get("checked") is True
    assert observed.get("gate_on") is True  # ligado DURANTE a restauracao
    assert result.success is True
    assert restore_module.is_restore_in_progress() is False  # desligado depois


def test_restore_rejects_a_concurrent_second_restore_attempt(tmp_path: Path, monkeypatch):
    """Relato do usuario (quinta auditoria pos-entrega): 'Duas chamadas
    simultaneas a _pause_for_restore() podem entrar; quando a primeira
    sai, _restore_in_progress vira False enquanto a segunda ainda
    restaura.' Uma SEGUNDA restauracao disparada enquanto a primeira ainda
    esta em andamento precisa ser REJEITADA imediatamente - nunca as duas
    'dentro' ao mesmo tempo, e o portao nunca desliga achando que
    terminou quando na verdade so a primeira terminou."""

    import threading

    import central_universal.persistence.restore as restore_module

    db_path = tmp_path / "central.db"
    conn = connect(db_path)
    run_migrations(conn)
    repos = Repositories(conn)
    learner = Learner(id=new_id(), display_name="Original", created_at=utc_now_iso())
    repos.learners.insert(learner)
    backup_event = create_backup(conn, repos, backup_dir=tmp_path / "backups")
    assert backup_event.success is True
    conn.close()

    entered_first = threading.Event()
    release_first = threading.Event()
    observed: dict[str, object] = {}
    real_copy2 = restore_module.shutil.copy2

    def spy_copy2(src, dst, *args, **kwargs):
        if "first_hit" not in observed:
            observed["first_hit"] = True
            entered_first.set()
            release_first.wait(timeout=5)  # segura a PRIMEIRA restauracao "dentro" de proposito
        return real_copy2(src, dst, *args, **kwargs)

    monkeypatch.setattr(restore_module.shutil, "copy2", spy_copy2)

    results: dict[str, object] = {}

    def run_first():
        results["first"] = restore_from_backup(db_path, backup_event.backup_path)

    first_thread = threading.Thread(target=run_first)
    first_thread.start()
    assert entered_first.wait(timeout=5), "a primeira restauracao nunca entrou na janela protegida"

    # a primeira restauracao esta comprovadamente "dentro" agora - uma
    # segunda tentativa, disparada NESTE exato momento, precisa ser
    # rejeitada na hora, sem esperar e sem tocar nenhum arquivo.
    assert restore_module.is_restore_in_progress() is True
    second_result = restore_from_backup(db_path, backup_event.backup_path)

    release_first.set()
    first_thread.join(timeout=5)
    assert not first_thread.is_alive()

    assert second_result.success is False
    assert "andamento" in second_result.message.lower()
    assert results["first"].success is True  # a primeira prossegue e termina normalmente
    assert restore_module.is_restore_in_progress() is False  # desligado so depois que a UNICA restauracao real terminou

    restored = connect(db_path)
    assert Repositories(restored).learners.get(learner.id) is not None
    restored.close()


def test_restore_waits_for_an_in_flight_request_before_touching_files(tmp_path: Path, monkeypatch):
    """Relato do usuario (quinta auditoria pos-entrega): 'ha uma janela em
    get_conn() entre consultar o portao e abrir a conexao'. Uma
    requisicao que ja passou pela checagem do portao e esta com uma
    conexao aberta (registrada via `reader_slot()`, exatamente o que
    `get_conn()` faz) precisa impedir que uma restauracao comece a tocar
    arquivos ate essa conexao ser fechada - nunca uma janela onde as duas
    coexistem."""

    import threading

    import central_universal.persistence.restore as restore_module

    db_path = tmp_path / "central.db"
    conn = connect(db_path)
    run_migrations(conn)
    repos = Repositories(conn)
    learner = Learner(id=new_id(), display_name="Original", created_at=utc_now_iso())
    repos.learners.insert(learner)
    backup_event = create_backup(conn, repos, backup_dir=tmp_path / "backups")
    assert backup_event.success is True
    conn.close()

    # simula uma requisicao que ja passou pela checagem do portao (como
    # `get_conn()` faz) e esta com a conexao "aberta" - exatamente o
    # cenario relatado, capturado logo ANTES do restore comecar.
    reader_cm = restore_module.reader_slot()
    reader_cm.__enter__()
    touched_files = threading.Event()
    real_os_replace = restore_module.os.replace

    def spy_os_replace(src, dst, *args, **kwargs):
        touched_files.set()
        return real_os_replace(src, dst, *args, **kwargs)

    monkeypatch.setattr(restore_module.os, "replace", spy_os_replace)

    result_holder: dict[str, object] = {}

    def run_restore():
        result_holder["result"] = restore_from_backup(db_path, backup_event.backup_path)

    restore_thread = threading.Thread(target=run_restore)
    restore_thread.start()
    try:
        # enquanto a "requisicao" mantiver a conexao aberta, a restauracao
        # NUNCA deve chegar a tocar arquivos.
        assert touched_files.wait(timeout=0.5) is False
    finally:
        reader_cm.__exit__(None, None, None)  # a "requisicao" termina, fecha a conexao

    restore_thread.join(timeout=5)
    assert not restore_thread.is_alive()

    # so DEPOIS que a conexao fechou a restauracao prossegue e termina.
    assert touched_files.wait(timeout=5) is True
    assert result_holder["result"].success is True

    restored = connect(db_path)
    assert Repositories(restored).learners.get(learner.id) is not None
    restored.close()


def test_restore_reverts_cleanly_when_swap_target_had_active_wal(tmp_path: Path, monkeypatch):
    """Combina reversao (integrity_check falha apos a troca) com um WAL
    ativo no banco original - a reversao precisa devolver exatamente o
    estado anterior, sem sidecars orfaos da tentativa que falhou."""

    db_path = tmp_path / "central.db"
    conn = connect(db_path)
    run_migrations(conn)
    repos = Repositories(conn)
    conn.execute("PRAGMA wal_autocheckpoint = 0;")
    original_learner = Learner(id=new_id(), display_name="Fica mesmo com WAL ativo", created_at=utc_now_iso())
    repos.learners.insert(original_learner)

    wal_path = db_path.with_name(db_path.name + "-wal")
    assert wal_path.exists() and wal_path.stat().st_size > 0

    other_db = tmp_path / "other.db"
    other_conn = connect(other_db)
    run_migrations(other_conn)
    other_conn.close()

    conn.close()

    import central_universal.persistence.restore as restore_module

    def fake_run_all(_repos):
        class _FakeReport:
            ok = False
            findings = [type("F", (), {"severity": "error", "message": "forcado no teste"})()]

        return _FakeReport()

    monkeypatch.setattr(restore_module, "run_all", fake_run_all)

    result = restore_from_backup(db_path, other_db)
    assert result.success is False
    assert "revertida" in result.message

    assert not wal_path.exists()  # nenhum sidecar orfao da tentativa que falhou

    reverted = connect(db_path)
    assert Repositories(reverted).learners.get(original_learner.id) is not None
    reverted.close()


def test_restore_never_raises_when_revert_itself_also_fails(tmp_path: Path, monkeypatch):
    """Relato do usuario: 'o tratamento de falha deve preservar o banco
    original e sempre devolver um resultado legivel, mesmo se a reversao
    encontrar outro erro'. Simula falha na restauracao (integrity_check)
    E na propria reversao (o `os.replace` da reversao falha, ex.: disco
    cheio) - `restore_from_backup` NUNCA pode deixar uma excecao escapar,
    e a copia de seguranca precisa sobreviver no disco para recuperacao
    manual em vez de ser apagada."""

    db_path = tmp_path / "central.db"
    conn = connect(db_path)
    run_migrations(conn)
    repos = Repositories(conn)
    original_learner = Learner(id=new_id(), display_name="Fica", created_at=utc_now_iso())
    repos.learners.insert(original_learner)

    other_db = tmp_path / "other.db"
    other_conn = connect(other_db)
    run_migrations(other_conn)
    other_conn.close()

    conn.close()

    import central_universal.persistence.restore as restore_module

    def fake_run_all(_repos):
        class _FakeReport:
            ok = False
            findings = [type("F", (), {"severity": "error", "message": "forcado no teste"})()]

        return _FakeReport()

    monkeypatch.setattr(restore_module, "run_all", fake_run_all)

    real_os_replace = restore_module.os.replace

    def flaky_os_replace(src, dst, *args, **kwargs):
        if "reverting" in str(src):
            raise OSError("falha simulada na propria reversao (ex.: disco cheio)")
        return real_os_replace(src, dst, *args, **kwargs)

    monkeypatch.setattr(restore_module.os, "replace", flaky_os_replace)

    result = restore_from_backup(db_path, other_db)  # nunca deve levantar excecao

    assert result.success is False
    assert "tambem falhou" in result.message.lower()

    # a copia de seguranca sobrevive no disco - nao foi apagada, ja que a
    # reversao nao terminou com sucesso.
    safety_copies = list(tmp_path.glob("central.db.pre-restore-*"))
    assert len(safety_copies) == 1

    # o portao da aplicacao foi desligado mesmo com a dupla falha.
    assert restore_module.is_restore_in_progress() is False


def test_restore_returns_readable_result_when_close_connections_fails(tmp_path: Path):
    """Achado remanescente da sexta auditoria pos-entrega (P2): o callback
    `close_connections()` roda ANTES do bloco que converte falhas em
    `RestoreResult`. Se ele levantar, a excecao nao pode escapar ate o
    chamador web - a troca do banco nem comecou, entao nao ha nada para
    reverter, so um resultado legivel para devolver."""

    import central_universal.persistence.restore as restore_module

    db_path = tmp_path / "central.db"
    conn = connect(db_path)
    run_migrations(conn)
    repos = Repositories(conn)
    original_learner = Learner(id=new_id(), display_name="Intocado", created_at=utc_now_iso())
    repos.learners.insert(original_learner)
    backup_event = create_backup(conn, repos, backup_dir=tmp_path / "backups")
    assert backup_event.success is True
    conn.close()

    def failing_close_connections():
        raise RuntimeError("falha simulada ao fechar conexoes do chamador")

    result = restore_from_backup(db_path, backup_event.backup_path, close_connections=failing_close_connections)

    assert result.success is False
    assert "antes de tocar o banco ativo" in result.message

    # nenhuma copia de seguranca parcial ficou para tras
    assert list(tmp_path.glob("central.db.pre-restore-*")) == []

    # o portao da aplicacao foi desligado mesmo com a falha na preparacao
    assert restore_module.is_restore_in_progress() is False

    # o banco original continua absolutamente intacto - a troca nunca comecou
    untouched = connect(db_path)
    assert Repositories(untouched).learners.get(original_learner.id) is not None
    untouched.close()


def test_restore_returns_readable_result_when_safety_copy_creation_fails(tmp_path: Path, monkeypatch):
    """Mesmo achado (P2), segundo ponto: a criacao da copia de seguranca
    (`shutil.copy2`) tambem roda antes do bloco de tratamento de falha.
    Uma falha ali (ex.: disco cheio) precisa virar um `RestoreResult`
    legivel, sem tentar reverter (nada foi trocado ainda) e sem deixar
    nenhuma copia parcial no disco."""

    import central_universal.persistence.restore as restore_module

    db_path = tmp_path / "central.db"
    conn = connect(db_path)
    run_migrations(conn)
    repos = Repositories(conn)
    original_learner = Learner(id=new_id(), display_name="Intocado", created_at=utc_now_iso())
    repos.learners.insert(original_learner)
    backup_event = create_backup(conn, repos, backup_dir=tmp_path / "backups")
    assert backup_event.success is True
    conn.close()

    def failing_copy2(src, dst, *args, **kwargs):
        if "pre-restore" in str(dst):
            raise OSError("falha simulada ao criar a copia de seguranca (ex.: disco cheio)")
        raise AssertionError("copy2 chamado de forma inesperada antes da copia de seguranca")

    monkeypatch.setattr(restore_module.shutil, "copy2", failing_copy2)

    result = restore_from_backup(db_path, backup_event.backup_path)

    assert result.success is False
    assert "antes de tocar o banco ativo" in result.message

    assert list(tmp_path.glob("central.db.pre-restore-*")) == []
    assert restore_module.is_restore_in_progress() is False

    untouched = connect(db_path)
    assert Repositories(untouched).learners.get(original_learner.id) is not None
    untouched.close()


def test_maybe_run_automatic_backup_respects_min_interval(tmp_path: Path):
    db_path = tmp_path / "central.db"
    conn = connect(db_path)
    run_migrations(conn)
    repos = Repositories(conn)
    backup_dir = tmp_path / "backups"

    first = maybe_run_automatic_backup(conn, repos, backup_dir=backup_dir, min_interval_hours=1.0)
    assert first is not None

    second = maybe_run_automatic_backup(conn, repos, backup_dir=backup_dir, min_interval_hours=1.0)
    assert second is None  # ainda dentro do intervalo minimo

    third = maybe_run_automatic_backup(conn, repos, backup_dir=backup_dir, min_interval_hours=0.0)
    assert third is not None  # intervalo zerado, roda de novo
    conn.close()
