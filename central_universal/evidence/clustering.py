"""Clustering de evidencia computado no SERVIDOR (Secao 6 do pacote de
correcao v0.2).

Principio: "Nao aceite um ID arbitrario como prova de independencia."
Antes, o chamador podia passar qualquer `evidence_cluster_id` e o sistema
confiava cegamente nele para decidir se duas evidencias eram
independentes. Agora o cluster e uma funcao DETERMINISTICA e
SERVER-SIDE de:

- sessao (proximidade temporal: uma sessao e um bloco de tempo contiguo);
- tipo de atividade + prompt normalizado (previsibilidade: a mesma
  atividade repetida nao e uma nova prova independente);
- origem (de onde a interacao veio - hoje so existe uma: interacao de
  sessao ao vivo).

Duas interacoes na MESMA sessao com a MESMA assinatura de contexto caem
no MESMO cluster, nao importa quantos `activity_id`/`raw_interaction_id`
distintos existam por baixo. A mesma atividade repetida em sessoes
DIFERENTES (dias diferentes, por exemplo) gera clusters DIFERENTES -
o que e exatamente o que permite a evidencia de retencao longitudinal
contar dias distintos (Secao 12).
"""

from __future__ import annotations

import hashlib

from central_universal.domain.clock import utc_now_iso
from central_universal.domain.entities import Activity, EvidenceCluster
from central_universal.domain.ids import new_id
from central_universal.persistence.repositories import Repositories

DEFAULT_ORIGIN = "session_interaction"


def compute_context_signature(activity_type: str, prompt: str) -> str:
    normalized_prompt = " ".join(prompt.strip().lower().split())
    raw = f"{activity_type.strip().lower()}|{normalized_prompt}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]


def resolve_cluster(
    repos: Repositories, *, session_id: str, activity: Activity, origin: str = DEFAULT_ORIGIN
) -> EvidenceCluster:
    """Encontra ou cria o EvidenceCluster desta tentativa. Idempotente:
    chamar de novo para a mesma (sessao, atividade) devolve o MESMO
    cluster, so atualizando `last_seen_at`."""

    signature = compute_context_signature(activity.activity_type, activity.prompt)
    now = utc_now_iso()

    existing = repos.evidence_clusters.find_by_signature(session_id, signature)
    if existing is not None:
        repos.evidence_clusters.touch_last_seen(existing.id, now)
        return EvidenceCluster(
            id=existing.id,
            session_id=existing.session_id,
            activity_type=existing.activity_type,
            context_signature=existing.context_signature,
            origin=existing.origin,
            first_seen_at=existing.first_seen_at,
            last_seen_at=now,
        )

    cluster = EvidenceCluster(
        id=new_id(),
        session_id=session_id,
        activity_type=activity.activity_type,
        context_signature=signature,
        origin=origin,
        first_seen_at=now,
        last_seen_at=now,
    )
    repos.evidence_clusters.insert(cluster)
    return cluster
