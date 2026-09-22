"""Agregacao deterministica: deriva CompetencyState a partir do event log.

Este e o unico lugar do sistema que decide "qual e o estado desta
dimensao de competencia agora". A funcao e pura (sem I/O) para que possa
ser testada exaustivamente e para que `CompetencyState` seja sempre
recalculavel a partir de RawInteraction + EvidenceEvent + EvidenceAssessment
+ RuleVersion (Principio Constitucional 6, Secao 14).

v0.2 (pacote de correcao pos Red Team): dois principios reforcados aqui:

- **Nenhum threshold hardcoded.** Todos os limiares vem de
  `AggregationConfig`, que e lido do `config_json` IMUTAVEL da
  `RuleVersion` em uso. Mudar um numero e criar uma nova RuleVersion,
  nunca editar uma constante de modulo (Principio 18).
- **Sem rebaixamento automatico.** Regressao NUNCA derruba o tier aqui:
  so fica marcada em `possible_regression`, pendente de validacao
  deliberada (Secao 17, regra 8 - TARGETED_REGRESSION_CHECK). Ate existir
  calibracao real, e mais seguro deixar uma suspeita visivel do que
  reescrever um estado que pode estar certo.
- **Independencia e por cluster, nao por evento.** Cada bucket de
  evidencia (positiva forte, positiva fraca, negativa/contraditoria alvo)
  conta NO MAXIMO uma contribuicao por cluster - dez respostas do mesmo
  cluster nunca pesam mais que uma.
"""

from __future__ import annotations

import json
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


@dataclass(frozen=True)
class AggregationConfig:
    """Politica provisoria de agregacao, versionada via RuleVersion
    (Secao 8/9 do pacote de correcao v0.2). Os valores default aqui sao
    SOMENTE o fallback usado se um RuleVersion antigo nao trouxer
    `config_json` (compatibilidade) - toda RuleVersion nova deve
    declarar os seus explicitamente.
    """

    demonstrated_min_clusters: int = 2
    consolidated_min_clusters: int = 3
    retention_demonstrated_min_days: int = 2
    retention_consolidated_min_days: int = 3
    contradiction_outweigh_ratio: float = 2.0
    min_confidence_for_aggregation: float = 0.5

    @classmethod
    def from_json(cls, config_json: str | None) -> "AggregationConfig":
        if not config_json:
            return cls()
        data = json.loads(config_json)
        known_fields = cls.__dataclass_fields__.keys()
        filtered = {k: v for k, v in data.items() if k in known_fields}
        return cls(**filtered)

    def to_json(self) -> str:
        return json.dumps(
            {
                "demonstrated_min_clusters": self.demonstrated_min_clusters,
                "consolidated_min_clusters": self.consolidated_min_clusters,
                "retention_demonstrated_min_days": self.retention_demonstrated_min_days,
                "retention_consolidated_min_days": self.retention_consolidated_min_days,
                "contradiction_outweigh_ratio": self.contradiction_outweigh_ratio,
                "min_confidence_for_aggregation": self.min_confidence_for_aggregation,
            },
            sort_keys=True,
        )


@dataclass(frozen=True)
class UsableEvidence:
    """Uma evidencia ja assessada, pronta para entrar na agregacao.

    `mere_presence`, achados `inconclusive` e achados com
    `alternative_cause` pendente nunca chegam aqui: sao filtrados antes,
    na camada de servico (Secao 8 e Secao 11 do pacote de correcao v0.2).
    """

    evidence_type: EvidenceType
    relation: EvidenceRelation
    help_level: HelpLevel
    evidence_cluster_id: str
    confidence: float
    created_at: datetime


@dataclass(frozen=True)
class AggregationResult:
    state: CompetencyDimensionState
    possible_regression: bool
    has_unresolved_contradiction: bool
    explanation: str


def _dedupe_latest_per_cluster(events: list[UsableEvidence]) -> dict[str, UsableEvidence]:
    """Garante 'no maximo uma contribuicao por cluster' (Secao 6 do pacote
    de correcao v0.2): se o mesmo cluster aparece varias vezes num bucket,
    so a ocorrencia mais recente conta."""

    by_cluster: dict[str, UsableEvidence] = {}
    for event in events:
        current = by_cluster.get(event.evidence_cluster_id)
        if current is None or event.created_at > current.created_at:
            by_cluster[event.evidence_cluster_id] = event
    return by_cluster


def classify_dimension(
    dimension: Dimension,
    events: list[UsableEvidence],
    config: AggregationConfig | None = None,
) -> AggregationResult:
    config = config or AggregationConfig()

    if not events:
        return AggregationResult(
            CompetencyDimensionState.NOT_ASSESSED,
            False,
            False,
            "Nenhuma evidencia registrada para esta dimensao.",
        )

    usable = [e for e in events if e.confidence >= config.min_confidence_for_aggregation]
    if not usable:
        return AggregationResult(
            CompetencyDimensionState.INSUFFICIENT_EVIDENCE,
            False,
            False,
            f"{len(events)} evidencia(s) registrada(s), mas nenhuma atinge a "
            f"confianca minima de agregacao ({config.min_confidence_for_aggregation}).",
        )

    events_sorted = sorted(usable, key=lambda e: e.created_at)

    # "Recuperacao independente nao pode ser considerada demonstrada
    # somente por A2/A3" (Secao 9).
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
    # Regressao so pode ser sinalizada por evidencia TARGET (Secao 7 do
    # pacote de correcao v0.2: "evidencia incidental negativa deve gerar
    # hipotese de validacao e nao rebaixamento automatico" - qualified
    # incidental nunca entra aqui).
    regression_signal = [
        e
        for e in events_sorted
        if e.relation == EvidenceRelation.TARGET
        and e.evidence_type in (EvidenceType.NEGATIVE, EvidenceType.CONTRADICTORY)
    ]
    contradictory = [e for e in events_sorted if e.evidence_type == EvidenceType.CONTRADICTORY]
    incidental_negative = [
        e
        for e in events_sorted
        if e.evidence_type == EvidenceType.NEGATIVE and e.relation == EvidenceRelation.QUALIFIED_INCIDENTAL
    ]

    strong_by_cluster = _dedupe_latest_per_cluster(strong_positive)
    weak_by_cluster = _dedupe_latest_per_cluster(weak_positive)
    regression_by_cluster = _dedupe_latest_per_cluster(regression_signal)
    contradictory_by_cluster = _dedupe_latest_per_cluster(contradictory)

    strong_clusters = set(strong_by_cluster.keys())
    strong_days = {e.created_at.date() for e in strong_by_cluster.values()}

    if dimension == Dimension.RETENTION:
        # Principio 10 / Secao 12: retencao e longitudinal. Repeticoes no
        # mesmo dia nao contam como dias distintos, nao importa quantos
        # clusters/sessoes existam (T1, T12).
        if len(strong_days) >= config.retention_consolidated_min_days:
            tier = CompetencyDimensionState.CONSOLIDATED
        elif len(strong_days) >= config.retention_demonstrated_min_days:
            tier = CompetencyDimensionState.DEMONSTRATED
        elif strong_clusters or weak_by_cluster:
            tier = CompetencyDimensionState.ACQUIRING
        else:
            tier = CompetencyDimensionState.INSUFFICIENT_EVIDENCE
    else:
        if len(strong_clusters) >= config.consolidated_min_clusters:
            tier = CompetencyDimensionState.CONSOLIDATED
        elif len(strong_clusters) >= config.demonstrated_min_clusters:
            tier = CompetencyDimensionState.DEMONSTRATED
        elif strong_clusters or weak_by_cluster:
            tier = CompetencyDimensionState.ACQUIRING
        else:
            tier = CompetencyDimensionState.INSUFFICIENT_EVIDENCE

    explanation_parts = [
        f"{len(strong_by_cluster)} cluster(s) independente(s) com evidencia positiva forte"
        + (f", {len(strong_days)} dia(s) distinto(s)" if dimension == Dimension.RETENTION else "")
        + "."
    ]
    if weak_by_cluster:
        explanation_parts.append(
            f"{len(weak_by_cluster)} cluster(s) com evidencia positiva assistida/incidental "
            "(sustenta 'acquiring', nunca promove sozinha a demonstrated/consolidated)."
        )
    if incidental_negative:
        explanation_parts.append(
            f"{len(incidental_negative)} evidencia(s) negativa(s) incidental(is) registrada(s) "
            "como hipotese de validacao - nao contam para regressao nem rebaixam o estado."
        )

    # Evidencia contraditoria pode coexistir (Principio 13). Se nao for
    # claramente superada pela evidencia positiva forte, o estado nao pode
    # afirmar dominio: cai para insufficient_evidence (T6).
    has_unresolved_contradiction = False
    if contradictory_by_cluster and len(strong_clusters) < config.contradiction_outweigh_ratio * len(contradictory_by_cluster):
        has_unresolved_contradiction = True
        if _STATE_ORDER.index(tier) > _STATE_ORDER.index(CompetencyDimensionState.INSUFFICIENT_EVIDENCE):
            tier = CompetencyDimensionState.INSUFFICIENT_EVIDENCE
        explanation_parts.append(
            f"{len(contradictory_by_cluster)} cluster(s) com evidencia contraditoria nao "
            "superada pela evidencia positiva: estado mantido em incerteza."
        )

    # Regressao (Secao 8 do pacote de correcao v0.2): NUNCA rebaixa o tier
    # automaticamente aqui. So sinaliza, e so quando a evidencia TARGET
    # mais recente (comparando positiva forte contra negativa/contraditoria,
    # ja deduplicadas por cluster) e desfavoravel - "erro isolado" nao
    # movimenta esse sinal se uma evidencia positiva mais recente já o
    # superou.
    possible_regression = False
    if tier in (CompetencyDimensionState.DEMONSTRATED, CompetencyDimensionState.CONSOLIDATED) and regression_by_cluster:
        combined = list(strong_by_cluster.values()) + list(regression_by_cluster.values())
        most_recent = max(combined, key=lambda e: e.created_at)
        if most_recent.evidence_type in (EvidenceType.NEGATIVE, EvidenceType.CONTRADICTORY):
            possible_regression = True
            explanation_parts.append(
                f"{len(regression_by_cluster)} cluster(s) com evidencia alvo negativa/contraditoria "
                "mais recente que a ultima confirmacao positiva: possivel regressao sinalizada, "
                "pendente de validacao deliberada (sem rebaixamento automatico)."
            )

    return AggregationResult(tier, possible_regression, has_unresolved_contradiction, " ".join(explanation_parts))
