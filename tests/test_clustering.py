from __future__ import annotations

from central_universal.domain.clock import utc_now_iso
from central_universal.domain.entities import Activity, Learner, LearningSession
from central_universal.domain.enums import HelpLevel, SessionStatus
from central_universal.domain.ids import new_id
from central_universal.evidence.clustering import resolve_cluster
from central_universal.persistence.repositories import Repositories


def _session(repos: Repositories) -> LearningSession:
    learner = Learner(id=new_id(), display_name="Aluno", created_at=utc_now_iso())
    repos.learners.insert(learner)
    session = LearningSession(id=new_id(), learner_id=learner.id, started_at=utc_now_iso(), status=SessionStatus.ACTIVE)
    repos.sessions.insert(session)
    return session


def _activity(repos: Repositories, session_id: str, activity_type: str, prompt: str) -> Activity:
    activity = Activity(
        id=new_id(), session_id=session_id, competency_targets=[], activity_type=activity_type,
        prompt=prompt, support_level=HelpLevel.A0, created_at=utc_now_iso(),
    )
    repos.activities.insert(activity)
    return activity


def test_same_session_same_prompt_share_one_cluster(repos: Repositories):
    session = _session(repos)
    a1 = _activity(repos, session.id, "drill", "  She  ___ (go) to school.  ")
    a2 = _activity(repos, session.id, "drill", "She ___ (go) to school.")  # mesmo prompt, espacamento diferente

    c1 = resolve_cluster(repos, session_id=session.id, activity=a1)
    c2 = resolve_cluster(repos, session_id=session.id, activity=a2)

    assert c1.id == c2.id  # atividades distintas, mesmo contexto -> mesmo cluster


def test_different_prompt_same_session_different_cluster(repos: Repositories):
    session = _session(repos)
    a1 = _activity(repos, session.id, "drill", "She ___ (go) to school.")
    a2 = _activity(repos, session.id, "drill", "They ___ (have) a car.")

    c1 = resolve_cluster(repos, session_id=session.id, activity=a1)
    c2 = resolve_cluster(repos, session_id=session.id, activity=a2)

    assert c1.id != c2.id


def test_same_prompt_different_session_different_cluster(repos: Repositories):
    session1 = _session(repos)
    session2 = _session(repos)
    a1 = _activity(repos, session1.id, "drill", "She ___ (go) to school.")
    a2 = _activity(repos, session2.id, "drill", "She ___ (go) to school.")

    c1 = resolve_cluster(repos, session_id=session1.id, activity=a1)
    c2 = resolve_cluster(repos, session_id=session2.id, activity=a2)

    assert c1.id != c2.id  # sessoes diferentes (ex.: dias diferentes) permanecem independentes


def test_client_cannot_forge_cluster_identity(repos: Repositories):
    """Nao existe parametro que aceite um cluster id arbitrario do
    chamador - resolve_cluster SEMPRE deriva o id a partir de
    sessao+contexto, nunca de um valor externo."""

    import inspect

    params = inspect.signature(resolve_cluster).parameters
    assert "cluster_id" not in params
    assert "evidence_cluster_id" not in params
