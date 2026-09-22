"""Agregacao deterministica: deriva CompetencyState a partir do event log.

Este e o unico lugar do sistema que decide "qual e o estado desta
dimensao de competencia agora". A funcao e pura (sem I/O) para que possa
ser testada exaustivamente e para que `CompetencyState` seja sempre
recalculavel a partir de RawInteraction + EvidenceEvent + EvidenceAssessment
+ RuleVersion (Principio Constitucional 6, Secao 14).

Deliberadamente NAO usamos pesos/scores numericos de "mastery": a
especificacao pede explicitamente que thresholds fiquem para depois, quando
houver dados reais para calibra-los (ver DECISIONS.md). O que ha aqui e uma
maquina de estados baseada em contagem de evidencia INDEPENDENTE, nao um
score artificial.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from central_universal.domain.enums import (
    CompetencyDimensionState,
    Dimension,
    EvidenceRelation,
    EvidenceType,
    HelpLevel,
)

_STATE_ORDER = [
    CompetencyDimensionState.NOT_ASSESSED,
    CompetencyDimensionState.INSUFFICIENT_EVIDENCE,
    CompetencyDimensionState.ACQUIRING,
    CompetencyDimensionState.DEMONSTRATED,
    CompetencyDimensionState.CONSOLIDATED,
]


def _downgrade_one_tier(state: CompetencyDimensionState) -> CompetencyDimensionState:
    idx = _STATE_ORDER.index(state)
    return _STATE_ORDER[max(idx - 1, 1)]  # nunca cai abaixo de INSUFFICIENT_EVIDENCE


@dataclass(frozen=True)
class UsableEvidence:
    """Uma evidencia ja assessada, pronta para entrar na agregacao.

    `mere_presence` nunca chega aqui: e filtrada antes, na camada de
    servico (Principio: mere_presence nao atualiza estado - Secao 8, T5).
    """

    evidence_type: EvidenceType
    relation: EvidenceRelation
    help_level: HelpLevel
    evidence_cluster_id: str
    created_at: datetime


@dataclass(frozen=True)
class AggregationResult:
    state: CompetencyDimensionState
    possible_regression: bool
    has_unresolved_contradiction: bool
    explanation: str


def classify_dimension(
    dimension: Dimension, events: list[UsableEvidence]
) -> AggregationResult:
    if not events:
        return AggregationResult(
            CompetencyDimensionState.NOT_ASSESSED,
            False,
            False,
            "Nenhuma evidencia registrada para esta dimensao.",
        )

    events_sorted = sorted(events, key=lambda e: e.created_at)

    # "Recuperacao independente nao pode ser considerada demonstrada
    # somente por A2/A3" (Secao 9). Aplicamos essa barreira a toda
    # evidencia POSITIVA de relacao 'target': so conta como evidencia
    # forte (independente) quando o nivel de ajuda foi A0 ou A1.
    strong_positive = [
        e
        for e in events_sorted
        if e.evidence_type == EvidenceType.POSITIVE
        and e.relation == EvidenceRelation.TARGET
        and e.help_level in (HelpLevel.A0, HelpLevel.A1)
    ]
    weak_positive = [
        e
        for e in events_sorted
        if e.evidence_type == EvidenceType.POSITIVE and e not in strong_positive
    ]
    negatives = [e for e in events_sorted if e.evidence_type == EvidenceType.NEGATIVE]
    contradictory = [
        e for e in events_sorted if e.evidence_type == EvidenceType.CONTRADICTORY
    ]

    strong_clusters = {e.evidence_cluster_id for e in strong_positive}
    strong_days = {e.created_at.date() for e in strong_positive}

    if dimension == Dimension.RETENTION:
        # Principio 10 / Secao 12: retencao e longitudinal. Repeticoes no
        # mesmo dia nao contam como dias distintos, nao importa quantas
        # sessoes ou clusters existam (T1, T12).
        if len(strong_days) >= 3:
            tier = CompetencyDimensionState.CONSOLIDATED
        elif len(strong_days) >= 2:
            tier = CompetencyDimensionState.DEMONSTRATED
        elif strong_clusters or weak_positive:
            tier = CompetencyDimensionState.ACQUIRING
        else:
            tier = CompetencyDimensionState.INSUFFICIENT_EVIDENCE
    else:
        if len(strong_clusters) >= 3:
            tier = CompetencyDimensionState.CONSOLIDATED
        elif len(strong_clusters) >= 2:
            tier = CompetencyDimensionState.DEMONSTRATED
        elif strong_clusters or weak_positive:
            tier = CompetencyDimensionState.ACQUIRING
        else:
            tier = CompetencyDimensionState.INSUFFICIENT_EVIDENCE

    explanation_parts = [
        f"{len(strong_positive)} evidencia(s) positiva(s) independente(s) "
        f"em {len(strong_clusters)} cluster(s) distinto(s)"
        + (f" e {len(strong_days)} dia(s) distinto(s)" if dimension == Dimension.RETENTION else "")
        + "."
    ]
    if weak_positive:
        explanation_parts.append(
            f"{len(weak_positive)} evidencia(s) positiva(s) assistida(s)/incidental(is) "
            "(nao contam para demonstrar/consolidar, mas sustentam 'acquiring')."
        )

    # Evidencia contradictoria pode coexistir (Principio 13). Se nao for
    # claramente superada pela evidencia positiva forte, o estado nao pode
    # afirmar dominio: cai para insufficient_evidence e a decisao correta e
    # validar, nao promover nem derrubar (T6).
    has_unresolved_contradiction = False
    if contradictory and len(strong_clusters) < 2 * len(contradictory):
        has_unresolved_contradiction = True
        if _STATE_ORDER.index(tier) > _STATE_ORDER.index(
            CompetencyDimensionState.INSUFFICIENT_EVIDENCE
        ):
            tier = CompetencyDimensionState.INSUFFICIENT_EVIDENCE
        explanation_parts.append(
            f"{len(contradictory)} evidencia(s) contraditoria(s) nao superada(s) "
            "pela evidencia positiva: estado mantido em incerteza."
        )

    # Regressao possivel (Principio 12): um erro ISOLADO recente nao apaga
    # dominio anterior - apenas sinaliza. So corroboramos (derrubamos um
    # nivel) quando ha >=2 evidencias negativas/contraditorias entre as
    # 3 mais recentes, ou seja, quando NAO e mais um erro isolado.
    possible_regression = False
    if tier in (
        CompetencyDimensionState.DEMONSTRATED,
        CompetencyDimensionState.CONSOLIDATED,
    ):
        recent = events_sorted[-3:]
        recent_bad = [
            e
            for e in recent
            if e.evidence_type in (EvidenceType.NEGATIVE, EvidenceType.CONTRADICTORY)
        ]
        if recent_bad:
            possible_regression = True
            explanation_parts.append(
                f"{len(recent_bad)} evidencia(s) negativa(s)/contraditoria(s) recente(s) "
                "detectada(s): possivel regressao sinalizada."
            )
            if len(recent_bad) >= 2:
                tier = _downgrade_one_tier(tier)
                explanation_parts.append(
                    "Regressao corroborada por mais de uma evidencia recente: "
                    "estado rebaixado em um nivel (nao para insufficient_evidence "
                    "direto, para nao apagar todo o historico de uma vez)."
                )

    return AggregationResult(
        tier, possible_regression, has_unresolved_contradiction, " ".join(explanation_parts)
    )
