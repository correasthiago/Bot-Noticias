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


def _activity(repos: Repositories, session_id: str, activity_type: str, prompt: str, competency_targets: list[str]) -> Activity:
    activity = Activity(
        id=new_id(), session_id=session_id, competency_targets=competency_targets, activity_type=activity_type,
        prompt=prompt, support_level=HelpLevel.A0, created_at=utc_now_iso(),
    )
    repos.activities.insert(activity)
    return activity


def test_same_session_same_prompt_share_one_cluster(repos: Repositories, make_competency):
    competency_id = make_competency()
    session = _session(repos)
    a1 = _activity(repos, session.id, "drill", "  She  ___ (go) to school.  ", [competency_id])
    a2 = _activity(repos, session.id, "drill", "She ___ (go) to school.", [competency_id])

    c1 = resolve_cluster(repos, session_id=session.id, activity=a1)
    c2 = resolve_cluster(repos, session_id=session.id, activity=a2)

    assert c1.id == c2.id  # atividades distintas, mesmo contexto -> mesmo cluster


def test_predictable_template_variation_does_not_prove_independence(repos: Repositories, make_competency):
    """Secao 6 (auditoria pos-correcao v0.2): prompts com texto DIFERENTE
    mas pedagogicamente previsivel (mesmo tipo de exercicio, mesma
    competencia, so trocando sujeito/verbo) NAO podem contar como
    tentativas independentes so por terem textos distintos."""

    competency_id = make_competency()
    session = _session(repos)
    prompts = [
        "Complete: She ___ (go) to school.",
        "Complete: He ___ (work) at a bank.",
        "Complete: They ___ (study) every day.",
    ]
    clusters = [
        resolve_cluster(
            repos, session_id=session.id,
            activity=_activity(repos, session.id, "contrastive_practice", prompt, [competency_id]),
        )
        for prompt in prompts
    ]

    cluster_ids = {c.id for c in clusters}
    assert len(cluster_ids) == 1  # mesma acao pedagogica + mesma competencia = mesmo cluster


def test_different_competency_same_session_and_activity_type_different_cluster(repos: Repositories, make_competency):
    competency_a = make_competency("a")
    competency_b = make_competency("b")
    session = _session(repos)
    a1 = _activity(repos, session.id, "drill", "She ___ (go) to school.", [competency_a])
    a2 = _activity(repos, session.id, "drill", "They ___ (have) a car.", [competency_b])

    c1 = resolve_cluster(repos, session_id=session.id, activity=a1)
    c2 = resolve_cluster(repos, session_id=session.id, activity=a2)

    assert c1.id != c2.id  # competencias diferentes: exercicios genuinamente distintos


def test_different_activity_type_same_competency_different_cluster(repos: Repositories, make_competency):
    competency_id = make_competency()
    session = _session(repos)
    a1 = _activity(repos, session.id, "guided_retrieval", "She ___ (go) to school.", [competency_id])
    a2 = _activity(repos, session.id, "contrastive_practice", "She ___ (go) to school.", [competency_id])

    c1 = resolve_cluster(repos, session_id=session.id, activity=a1)
    c2 = resolve_cluster(repos, session_id=session.id, activity=a2)

    assert c1.id != c2.id  # acoes pedagogicas diferentes (dimensoes diferentes) nao colidem


def test_same_prompt_different_session_different_cluster(repos: Repositories, make_competency):
    competency_id = make_competency()
    session1 = _session(repos)
    session2 = _session(repos)
    a1 = _activity(repos, session1.id, "drill", "She ___ (go) to school.", [competency_id])
    a2 = _activity(repos, session2.id, "drill", "She ___ (go) to school.", [competency_id])

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
