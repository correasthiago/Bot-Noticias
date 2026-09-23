"""Interface web local minima (Secao 28). Funcional, nao bonita.

Server-rendered com Jinja2 + forms HTML simples. Sem framework JS: o
objetivo da V0 e provar o motor, nao a experiencia de usuario.
"""

from __future__ import annotations

import sqlite3
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlencode

from fastapi import Depends, FastAPI, Form, Request
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from central_universal.decision.service import DecisionService
from central_universal.domain.clock import parse_iso
from central_universal.domain.enums import ALL_DIMENSIONS, HelpLevel, ProductionResult
from central_universal.domain.entities import RuleVersion
from central_universal.integrity.checks import run_all as run_integrity_checks
from central_universal.memory.fsrs_adapter import MemoryAdapter, MemoryReviewError
from central_universal.orchestration.session_service import (
    SessionOrchestrator,
    compute_memory_review_eligibility,
)
from central_universal.persistence.backup import create_backup, list_backups
from central_universal.persistence.repositories import Repositories
from central_universal.persistence.restore import restore_from_backup
from central_universal.providers.base import Provider
from central_universal.web.bootstrap import ensure_bootstrapped
from central_universal.web.deps import (
    BACKUP_DIR,
    DB_PATH,
    get_active_rule_version,
    get_conn,
    get_provider,
    get_repos,
)

BASE_DIR = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))


@asynccontextmanager
async def _lifespan(_app: FastAPI):
    ensure_bootstrapped(DB_PATH)
    yield


app = FastAPI(title="Central Universal de Aprendizagem - V0.2", lifespan=_lifespan)
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")


def _orchestrator(repos: Repositories, provider: Provider, rule_version: RuleVersion) -> SessionOrchestrator:
    return SessionOrchestrator(repos, provider, rule_version, backup_dir=BACKUP_DIR)


@app.get("/")
def index(request: Request, repos: Repositories = Depends(get_repos)):
    learner = repos.learners.list_all()[0]
    decision_service = DecisionService(repos)
    memory_adapter = MemoryAdapter(repos)

    pending = {"STUDY": 0, "VALIDATE": 0, "SKIP": 0}
    due_recalls = 0
    for competency in repos.competencies.list_all():
        recall_due = memory_adapter.is_recall_due(competency.id)
        if recall_due:
            due_recalls += 1
        routing = decision_service.preview_routing(competency.id, memory_recall_due=recall_due)
        pending[routing.routing.value] += 1

    open_sessions = [s for s in repos.sessions.list_for_learner(learner.id) if s.status.value == "active"]

    return templates.TemplateResponse(
        request,
        "index.html",
        {
            "learner": learner,
            "pending": pending,
            "due_recalls": due_recalls,
            "total_competencies": len(repos.competencies.list_all()),
            "open_session": open_sessions[0] if open_sessions else None,
        },
    )


@app.post("/session/start")
def session_start(
    repos: Repositories = Depends(get_repos),
    provider: Provider = Depends(get_provider),
    rule_version: RuleVersion = Depends(get_active_rule_version),
):
    learner = repos.learners.list_all()[0]
    orchestrator = _orchestrator(repos, provider, rule_version)
    session = orchestrator.start_session(learner.id)
    return RedirectResponse(f"/session/{session.id}", status_code=303)


@app.get("/session/{session_id}")
def session_view(
    request: Request,
    session_id: str,
    repos: Repositories = Depends(get_repos),
    rule_version: RuleVersion = Depends(get_active_rule_version),
    memory_status: str | None = None,
    memory_message: str | None = None,
):
    session = repos.sessions.get(session_id)
    if session is None:
        return RedirectResponse("/", status_code=303)

    activities = repos.activities.list_for_session(session_id)
    current_activity = None
    interaction = None
    if activities:
        current_activity = activities[-1]
        interactions = repos.raw_interactions.list_by_activity(current_activity.id)
        interaction = interactions[-1] if interactions else None

    competency = None
    dimension_states = {}
    recent_decisions = []
    if current_activity and current_activity.competency_targets:
        target_id = current_activity.competency_targets[0]
        competency = repos.competencies.get(target_id)
        dimension_states = repos.competency_states.current_all_dimensions(target_id)
        recent_decisions = repos.decision_events.list_for_competency(target_id)[-3:]

    # Decima/decima primeira auditoria pos-entrega: "nao elegivel"
    # (intervalo insuficiente, avaliacao inconclusiva, atividade nao
    # planejada, ou SUPERADA por uma revisao mais recente da mesma
    # competencia - ver `evaluate_recall_eligibility`) e PERMANENTE para
    # esta interacao especifica - so descobre isso computando a
    # elegibilidade de verdade (`compute_memory_review_eligibility`, SEM
    # chamar o FSRS), nunca so checando "existe MemoryObservation?" (isso
    # mostrava um botao de retentativa que reaparecia identico apos cada
    # clique, sem nenhuma explicacao - ou, pior, deixava a interacao
    # simplesmente SUMIR da pagina sem nenhum registro do motivo, quando
    # ela nao era mais a atividade corrente).
    #
    # A pagina tambem nao pode se limitar a atividade mais recente: ao
    # avancar (`/session/{id}/next`), uma interacao de recuperacao
    # planejada anterior - recuperavel OU nao - precisa continuar visivel
    # em algum lugar. O loop abaixo cobre TODAS as atividades da sessao e
    # classifica cada `RawInteraction` de recuperacao planejada ainda sem
    # `MemoryObservation` em uma de duas listas: RECUPERAVEL (elegivel
    # agora - falhou antes, ou nunca foi tentada - com um caminho de
    # retentativa) ou NAO RECUPERAVEL (motivo auditavel mostrado, nunca um
    # botao que nao levaria a nada).
    pending_memory_reviews = []
    unrecoverable_memory_reviews = []
    for act in activities:
        if not act.is_planned_recall:
            continue
        act_interactions = repos.raw_interactions.list_by_activity(act.id)
        act_interaction = act_interactions[-1] if act_interactions else None
        if act_interaction is None:
            continue
        if repos.memory_observations.get_by_raw_interaction(act_interaction.id) is not None:
            continue
        result = compute_memory_review_eligibility(
            repos, rule_version, activity=act, raw_interaction=act_interaction,
            now=parse_iso(act_interaction.occurred_at),
        )
        if result is None:
            continue
        eligibility, _matching_assessment = result
        if eligibility.eligible:
            pending_memory_reviews.append({"activity": act, "interaction": act_interaction})
        else:
            unrecoverable_memory_reviews.append(
                {"activity": act, "interaction": act_interaction, "reason": eligibility.reason}
            )

    return templates.TemplateResponse(
        request,
        "session.html",
        {
            "session": session,
            "activity": current_activity,
            "interaction": interaction,
            "competency": competency,
            "dimension_states": dimension_states,
            "recent_decisions": recent_decisions,
            "help_levels": list(HelpLevel),
            "production_results": list(ProductionResult),
            "pending_memory_reviews": pending_memory_reviews,
            "unrecoverable_memory_reviews": unrecoverable_memory_reviews,
            "memory_status": memory_status,
            "memory_message": memory_message,
        },
    )


@app.post("/session/{session_id}/next")
def session_next(
    session_id: str,
    repos: Repositories = Depends(get_repos),
    provider: Provider = Depends(get_provider),
    rule_version: RuleVersion = Depends(get_active_rule_version),
):
    session = repos.sessions.get(session_id)
    if session is None:
        return RedirectResponse("/", status_code=303)

    orchestrator = _orchestrator(repos, provider, rule_version)
    focus = orchestrator.choose_focus_competency(session.learner_id)
    if focus is not None:
        competency_id, _routing, action, _recall_due = focus
        orchestrator.start_activity(session_id=session_id, competency_id=competency_id, action=action)
    return RedirectResponse(f"/session/{session_id}", status_code=303)


@app.post("/session/{session_id}/answer")
def session_answer(
    session_id: str,
    activity_id: str = Form(...),
    learner_input: str = Form(...),
    help_level: str = Form(...),
    production_result: str = Form(...),
    idempotency_key: str = Form(...),
    repos: Repositories = Depends(get_repos),
    provider: Provider = Depends(get_provider),
    rule_version: RuleVersion = Depends(get_active_rule_version),
):
    orchestrator = _orchestrator(repos, provider, rule_version)
    activity = repos.activities.get(activity_id)

    # Nona auditoria pos-entrega (P2): quando esta submissao esta
    # RECUPERANDO a revisao de memoria de uma tentativa ja registrada
    # (mesma idempotency_key ja tem uma RawInteraction persistida), o
    # `now` usado pelo FSRS precisa ser o horario da tentativa ORIGINAL
    # (`RawInteraction.occurred_at`) - nunca o horario deste clique de
    # retentativa, que pode acontecer muito depois. Usar o horario real
    # do clique inflaria artificialmente o intervalo que o FSRS enxerga
    # entre "quando o aprendiz respondeu" e "quando a revisao foi
    # registrada", derivando uma nota/agendamento que nao corresponde ao
    # que realmente aconteceu.
    existing = repos.raw_interactions.get_by_idempotency_key(idempotency_key)
    retry_now = parse_iso(existing.occurred_at) if existing is not None else None

    outcome = orchestrator.submit_interaction(
        activity_id=activity_id,
        session_id=session_id,
        idempotency_key=idempotency_key,
        learner_input=learner_input,
        help_level=HelpLevel(help_level),
        production_result=ProductionResult(production_result),
        tutor_output_text=activity.prompt if activity else "",
        now=retry_now,
    )

    # A revisao de memoria nunca e silenciada aqui: uma falha
    # (`MemoryReviewError`, que `observe_and_review` devolve mas nunca
    # levanta) chega visivel ao usuario na propria pagina da sessao, no
    # mesmo padrao ja usado para o resultado do restore em `/audit`. Uma
    # RETENTATIVA bem-sucedida (so possivel quando `existing` ja existia
    # antes desta chamada) tambem e confirmada explicitamente.
    query: dict[str, str] = {}
    if isinstance(outcome.memory_result, MemoryReviewError):
        query = {"memory_status": "error", "memory_message": outcome.memory_result.message}
    elif existing is not None and outcome.memory_result is not None:
        query = {"memory_status": "ok", "memory_message": "Revisao de memoria recuperada com sucesso."}

    suffix = f"?{urlencode(query)}" if query else ""
    return RedirectResponse(f"/session/{session_id}{suffix}", status_code=303)


@app.post("/session/{session_id}/end")
def session_end(
    session_id: str,
    repos: Repositories = Depends(get_repos),
    provider: Provider = Depends(get_provider),
    rule_version: RuleVersion = Depends(get_active_rule_version),
):
    orchestrator = _orchestrator(repos, provider, rule_version)
    orchestrator.end_session(session_id)
    return RedirectResponse("/", status_code=303)


@app.get("/map")
def competency_map(request: Request, repos: Repositories = Depends(get_repos)):
    rows = []
    for competency in repos.competencies.list_all():
        states = repos.competency_states.current_all_dimensions(competency.id)
        rows.append({"competency": competency, "states": states})
    return templates.TemplateResponse(
        request, "map.html", {"rows": rows, "dimensions": ALL_DIMENSIONS}
    )


@app.get("/competency/{competency_id}")
def competency_detail(request: Request, competency_id: str, repos: Repositories = Depends(get_repos)):
    competency = repos.competencies.get(competency_id)
    states = repos.competency_states.current_all_dimensions(competency_id)
    memory_state = repos.memory_states.get(competency_id)

    evidence_rows = []
    for dimension in ALL_DIMENSIONS:
        events = repos.evidence_events.list_for_competency(competency_id, dimension)
        for event in events:
            assessments = repos.evidence_assessments.get_for_evidence_event(event.id)
            evidence_rows.append({"event": event, "assessments": assessments})

    return templates.TemplateResponse(
        request,
        "competency.html",
        {
            "competency": competency,
            "states": states,
            "dimensions": ALL_DIMENSIONS,
            "memory_state": memory_state,
            "evidence_rows": evidence_rows,
        },
    )


@app.get("/audit")
def audit(
    request: Request,
    repos: Repositories = Depends(get_repos),
    restore_status: str | None = None,
    restore_message: str | None = None,
):
    report = run_integrity_checks(repos)
    return templates.TemplateResponse(
        request,
        "audit.html",
        {
            "decisions": repos.decision_events.list_recent(50),
            "provider_events": repos.provider_events.list_recent(50),
            "rule_versions": repos.rule_versions.list_all(),
            "active_rule_version": repos.active_rule_version.get(),
            "report": report,
            "backups": list_backups(repos),
            # Secao 5 do pacote de correcao v0.2.1: "mostre falha ao
            # usuario quando o restore falhar" - o resultado do POST
            # anterior chega aqui via querystring do redirect.
            "restore_status": restore_status,
            "restore_message": restore_message,
        },
    )


@app.post("/audit/backup")
def audit_backup(
    conn: sqlite3.Connection = Depends(get_conn),
    repos: Repositories = Depends(get_repos),
):
    create_backup(conn, repos, backup_dir=BACKUP_DIR)
    return RedirectResponse("/audit", status_code=303)


@app.post("/audit/restore")
def audit_restore(backup_path: str = Form(...)):
    # Secao 14 do pacote de correcao v0.2 / Secao 5 do pacote de correcao
    # v0.2.1: restore real, nao so uma copia de arquivo.
    # `restore_from_backup` valida o snapshot, impede novas escritas
    # (recusa prosseguir se outra conexao ainda estiver aberta), achata o
    # WAL, troca o banco atomicamente, roda migrations+integrity_check e
    # reverte sozinho se algo falhar - nao ha conexao de longa duracao
    # aqui para fechar (cada requisicao HTTP ja abre/fecha a sua). O
    # resultado (sucesso ou falha, com motivo) e sempre mostrado ao
    # usuario na propria pagina de auditoria - nunca silenciado.
    result = restore_from_backup(DB_PATH, backup_path)
    status = "ok" if result.success else "error"
    query = urlencode({"restore_status": status, "restore_message": result.message})
    return RedirectResponse(f"/audit?{query}", status_code=303)
