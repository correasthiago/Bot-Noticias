"""Clustering de evidencia computado no SERVIDOR (Secao 6 do pacote de
correcao v0.2; refinado pela Secao 6 do pacote de correcao v0.2.1).

Principio: "Nao aceite um ID arbitrario como prova de independencia."
Antes, o chamador podia passar qualquer `evidence_cluster_id` e o sistema
confiava cegamente nele para decidir se duas evidencias eram
independentes. Agora o cluster e uma funcao DETERMINISTICA e
SERVER-SIDE de:

- sessao (proximidade temporal: uma sessao e um bloco de tempo contiguo);
- tipo de atividade + competencia(s) alvo (previsibilidade: a MESMA acao
  pedagogica repetida para a MESMA competencia nao e uma nova prova
  independente - mesmo que o texto exato do prompt mude);
- origem (de onde a interacao veio - hoje so existe uma: interacao de
  sessao ao vivo).

v0.2.1 (auditoria pos-correcao): a assinatura DEIXOU de incluir o texto
literal do prompt. Um Red Team encontrou que dois prompts com textos
DIFERENTES mas pedagogicamente PREVISIVEIS (o mesmo exercicio de
lacuna, so trocando o sujeito/verbo - "She ___ (go) to school." vs.
"He ___ (work) at a bank.") gerava assinaturas diferentes e, portanto,
clusters diferentes - permitindo "provar independencia" so variando
palavras superficiais de um template repetido. Detectar predictibilidade
textual de forma robusta exigiria NLP, o que a V0 nao tem; a alternativa
auditavel e conservadora e usar o que realmente define "a mesma tarefa"
neste sistema: MESMA sessao + MESMO tipo de atividade (a acao pedagogica
escolhida pelo Decisor) + MESMA(S) competencia(s) alvo. Isso e
deliberadamente mais rigoroso (menos permissivo com "independencia") do
que comparar texto - exercicios genuinamente distintos (competencias ou
acoes pedagogicas diferentes) continuam gerando clusters diferentes.

Duas interacoes na MESMA sessao com a MESMA assinatura de contexto caem
no MESMO cluster, nao importa quantos `activity_id`/`raw_interaction_id`
distintos existam por baixo, e independente do texto exato do prompt. A
mesma atividade repetida em sessoes DIFERENTES (dias diferentes, por
exemplo) gera clusters DIFERENTES - o que e exatamente o que permite a
evidencia de retencao longitudinal contar dias distintos (Secao 12).
"""

from __future__ import annotations

import hashlib

from central_universal.domain.clock import utc_now_iso
from central_universal.domain.entities import Activity, EvidenceCluster
from central_universal.domain.ids import new_id
from central_universal.persistence.repositories import Repositories

DEFAULT_ORIGIN = "session_interaction"


def compute_context_signature(activity_type: str, competency_targets: list[str]) -> str:
    targets_key = ",".join(sorted(competency_targets))
    raw = f"{activity_type.strip().lower()}|{targets_key}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]


def resolve_cluster(
    repos: Repositories, *, session_id: str, activity: Activity, origin: str = DEFAULT_ORIGIN
) -> EvidenceCluster:
    """Encontra ou cria o EvidenceCluster desta tentativa. Idempotente:
    chamar de novo para a mesma (sessao, tipo de atividade, competencias
    alvo) devolve o MESMO cluster, so atualizando `last_seen_at` - o
    texto exato do prompt NUNCA entra na decisao (ver docstring do
    modulo)."""

    signature = compute_context_signature(activity.activity_type, activity.competency_targets)
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
