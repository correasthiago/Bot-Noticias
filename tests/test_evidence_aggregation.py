from __future__ import annotations

from datetime import datetime, timedelta, timezone

from central_universal.domain.enums import (
    CompetencyDimensionState,
    Dimension,
    EvidenceRelation,
    EvidenceType,
    HelpLevel,
)
from central_universal.evidence.aggregation import AggregationConfig, UsableEvidence, classify_dimension

BASE = datetime(2026, 1, 1, tzinfo=timezone.utc)


def ev(day_offset=0, evidence_type=EvidenceType.POSITIVE, relation=EvidenceRelation.TARGET,
       help_level=HelpLevel.A0, cluster="c1", minute_offset=0, confidence=0.9):
    return UsableEvidence(
        evidence_type=evidence_type,
        relation=relation,
        help_level=help_level,
        evidence_cluster_id=cluster,
        confidence=confidence,
        created_at=BASE + timedelta(days=day_offset, minutes=minute_offset),
    )


def test_no_evidence_is_not_assessed():
    result = classify_dimension(Dimension.ACCURACY, [])
    assert result.state == CompetencyDimensionState.NOT_ASSESSED


def test_single_cluster_is_acquiring_not_demonstrated():
    events = [ev(cluster="c1")]
    result = classify_dimension(Dimension.ACCURACY, events)
    assert result.state == CompetencyDimensionState.ACQUIRING


def test_two_independent_clusters_demonstrate_non_retention_dimension():
    events = [ev(cluster="c1"), ev(cluster="c2", minute_offset=5)]
    result = classify_dimension(Dimension.ACCURACY, events)
    assert result.state == CompetencyDimensionState.DEMONSTRATED


def test_three_independent_clusters_consolidate_non_retention_dimension():
    events = [ev(cluster="c1"), ev(cluster="c2"), ev(cluster="c3")]
    result = classify_dimension(Dimension.COMPREHENSION, events)
    assert result.state == CompetencyDimensionState.CONSOLIDATED


def test_retention_same_day_repeats_never_consolidate():
    events = [ev(cluster=f"c{i}", minute_offset=i) for i in range(10)]
    result = classify_dimension(Dimension.RETENTION, events)
    assert result.state != CompetencyDimensionState.CONSOLIDATED
    assert result.state != CompetencyDimensionState.DEMONSTRATED


def test_retention_requires_multiple_distinct_days():
    events = [ev(day_offset=0, cluster="c1"), ev(day_offset=5, cluster="c2"), ev(day_offset=10, cluster="c3")]
    result = classify_dimension(Dimension.RETENTION, events)
    assert result.state == CompetencyDimensionState.CONSOLIDATED


def test_a3_help_never_counts_as_independent_retrieval():
    events = [
        ev(cluster="c1", help_level=HelpLevel.A3),
        ev(cluster="c2", help_level=HelpLevel.A3),
        ev(cluster="c3", help_level=HelpLevel.A3),
    ]
    result = classify_dimension(Dimension.RETRIEVAL, events)
    assert result.state == CompetencyDimensionState.ACQUIRING


def test_isolated_negative_does_not_erase_consolidated_state():
    events = [ev(cluster="c1"), ev(cluster="c2"), ev(cluster="c3"),
              ev(cluster="c4", evidence_type=EvidenceType.NEGATIVE, minute_offset=99)]
    result = classify_dimension(Dimension.ACCURACY, events)
    assert result.state == CompetencyDimensionState.CONSOLIDATED
    assert result.possible_regression is True


def test_regression_never_auto_downgrades_even_with_many_negatives():
    """v0.2: nao ha mais rebaixamento automatico (Secao 8 do pacote de
    correcao). Mesmo com varias evidencias negativas recentes, em
    clusters distintos, o tier so fica sinalizado como suspeito - nunca
    e derrubado pela propria agregacao."""

    events = [
        ev(cluster="c1"), ev(cluster="c2"), ev(cluster="c3"),
        ev(cluster="c4", evidence_type=EvidenceType.NEGATIVE, minute_offset=97),
        ev(cluster="c5", evidence_type=EvidenceType.NEGATIVE, minute_offset=98),
        ev(cluster="c6", evidence_type=EvidenceType.NEGATIVE, minute_offset=99),
    ]
    result = classify_dimension(Dimension.ACCURACY, events)
    assert result.state == CompetencyDimensionState.CONSOLIDATED
    assert result.possible_regression is True


def test_two_negatives_from_same_cluster_do_not_double_count():
    """Secao 7 do pacote de correcao: duas negativas do MESMO cluster nao
    podem corroborar nada alem do que uma negativa ja sinalizaria."""

    events = [
        ev(cluster="c1"), ev(cluster="c2"), ev(cluster="c3"),
        ev(cluster="c4", evidence_type=EvidenceType.NEGATIVE, minute_offset=50),
        ev(cluster="c4", evidence_type=EvidenceType.NEGATIVE, minute_offset=51),  # mesmo cluster
    ]
    with_two = classify_dimension(Dimension.ACCURACY, events)

    events_one = events[:-1]  # so uma negativa no cluster c4
    with_one = classify_dimension(Dimension.ACCURACY, events_one)

    assert with_two.state == with_one.state == CompetencyDimensionState.CONSOLIDATED
    assert with_two.possible_regression == with_one.possible_regression is True


def test_common_positive_evidence_after_regression_never_silences_the_signal():
    """Achado da oitava auditoria pos-entrega: `possible_regression` e um
    sinal PERMANENTE ate uma validacao DELIBERADA resolve-lo (Principio
    12, Secao 17 regra 8 - TARGETED_REGRESSION_CHECK; KNOWN_LIMITATIONS.md
    diz explicitamente que nada hoje fecha esse ciclo automaticamente). A
    versao anterior comparava qual evidencia TARGET era mais recente e
    silenciava o sinal assim que QUALQUER evidencia positiva mais nova
    aparecesse - mesmo vinda de uma atividade COMUM, nunca de uma
    validacao deliberada. Este teste reproduz exatamente isso: a
    regressao e sinalizada primeiro, e uma resposta positiva COMUM chega
    DEPOIS (mais recente) - o sinal precisa continuar ligado."""

    events = [
        ev(cluster="c1"), ev(cluster="c2"), ev(cluster="c3"),
        ev(cluster="c4", evidence_type=EvidenceType.NEGATIVE, minute_offset=50),
        # evidencia positiva COMUM, MAIS RECENTE que a negativa acima -
        # nao e uma validacao deliberada, so mais uma resposta comum.
        ev(cluster="c5", minute_offset=99),
    ]
    result = classify_dimension(Dimension.ACCURACY, events)
    assert result.state == CompetencyDimensionState.CONSOLIDATED
    assert result.possible_regression is True  # NAO foi silenciado pela evidencia positiva posterior


def test_incidental_negative_never_sets_possible_regression():
    events = [
        ev(cluster="c1"), ev(cluster="c2"), ev(cluster="c3"),
        ev(cluster="c4", evidence_type=EvidenceType.NEGATIVE,
           relation=EvidenceRelation.QUALIFIED_INCIDENTAL, minute_offset=99),
    ]
    result = classify_dimension(Dimension.ACCURACY, events)
    assert result.state == CompetencyDimensionState.CONSOLIDATED
    assert result.possible_regression is False  # incidental gera hipotese, nao sinal de regressao


def test_qualified_incidental_alone_only_supports_acquiring():
    events = [ev(relation=EvidenceRelation.QUALIFIED_INCIDENTAL, cluster="c1")]
    result = classify_dimension(Dimension.TRANSFER, events)
    assert result.state == CompetencyDimensionState.ACQUIRING


def test_unresolved_contradiction_keeps_insufficient_evidence():
    events = [ev(cluster="c1", evidence_type=EvidenceType.CONTRADICTORY)]
    result = classify_dimension(Dimension.COMPREHENSION, events)
    assert result.state == CompetencyDimensionState.INSUFFICIENT_EVIDENCE


def test_strong_positive_evidence_overrides_minority_contradiction():
    events = [ev(cluster="c1"), ev(cluster="c2"), ev(cluster="c3"),
              ev(cluster="c4", evidence_type=EvidenceType.CONTRADICTORY, minute_offset=50)]
    result = classify_dimension(Dimension.COMPREHENSION, events)
    assert result.state == CompetencyDimensionState.CONSOLIDATED


def test_cluster_cap_ten_identical_events_count_as_one_cluster():
    events = [ev(cluster="only-one-cluster", minute_offset=i) for i in range(10)]
    result = classify_dimension(Dimension.ACCURACY, events)
    assert result.state == CompetencyDimensionState.ACQUIRING  # nunca passa de 1 cluster


def test_low_confidence_evidence_is_excluded_from_aggregation():
    events = [
        ev(cluster="c1", confidence=0.2),
        ev(cluster="c2", confidence=0.3),
        ev(cluster="c3", confidence=0.4),
    ]
    result = classify_dimension(Dimension.ACCURACY, events, AggregationConfig(min_confidence_for_aggregation=0.5))
    assert result.state == CompetencyDimensionState.INSUFFICIENT_EVIDENCE


def test_config_thresholds_are_not_hardcoded():
    events = [ev(cluster="c1"), ev(cluster="c2", minute_offset=5)]
    default_result = classify_dimension(Dimension.ACCURACY, events)
    assert default_result.state == CompetencyDimensionState.DEMONSTRATED

    stricter_config = AggregationConfig(demonstrated_min_clusters=5, consolidated_min_clusters=8)
    stricter_result = classify_dimension(Dimension.ACCURACY, events, stricter_config)
    assert stricter_result.state == CompetencyDimensionState.ACQUIRING
